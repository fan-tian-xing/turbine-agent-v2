"""Duplicate/derivative relation detection and real group materialization."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .identity import digest


GROUP_RELATION_TYPES = {
    "byte_identical",
    "visual_content_identical",
    "normalized_text_identical",
    "ocr_derivative_of",
}


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root

    def components(self) -> dict[int, list[int]]:
        result: dict[int, list[int]] = defaultdict(list)
        for index in range(len(self.parent)):
            result[self.find(index)].append(index)
        return {root: members for root, members in result.items() if len(members) > 1}


def duplicate_relations(assets: list[dict[str, Any]]) -> tuple[UnionFind, list[dict[str, Any]]]:
    groups = UnionFind(len(assets))
    relations: list[dict[str, Any]] = []

    def index_by(field: str) -> dict[str, list[int]]:
        index: dict[str, list[int]] = defaultdict(list)
        for asset_index, asset in enumerate(assets):
            value = asset[field]
            if value is not None:
                index[value].append(asset_index)
        return index

    for field, relation_type in (
        ("sha256", "byte_identical"),
        ("visual_content_fingerprint", "visual_content_identical"),
        ("text_content_fingerprint", "normalized_text_identical"),
    ):
        for fingerprint, asset_indices in index_by(field).items():
            if len(asset_indices) < 2:
                continue
            for left_position, left in enumerate(asset_indices):
                for right in asset_indices[left_position + 1 :]:
                    groups.union(left, right)
                    relations.append(
                        {
                            "relation_type": relation_type,
                            "asset_paths": [assets[left]["relative_path"], assets[right]["relative_path"]],
                            "fingerprint_field": field,
                            "fingerprint": fingerprint,
                        }
                    )

    for left in range(len(assets)):
        left_pages = assets[left]["page_visual_fingerprints"]
        for right in range(left + 1, len(assets)):
            right_pages = assets[right]["page_visual_fingerprints"]
            right_positions: dict[str, list[int]] = defaultdict(list)
            for position, fingerprint in enumerate(right_pages):
                right_positions[fingerprint].append(position)
            longest_run = 0
            shared_pages = 0
            for left_position, fingerprint in enumerate(left_pages):
                positions = right_positions.get(fingerprint, [])
                shared_pages += len(positions)
                for right_position in positions[:20]:
                    run = 0
                    while (
                        left_position + run < len(left_pages)
                        and right_position + run < len(right_pages)
                        and left_pages[left_position + run] == right_pages[right_position + run]
                    ):
                        run += 1
                    longest_run = max(longest_run, run)
            if longest_run >= 3 and shared_pages < max(len(left_pages), len(right_pages)):
                relations.append(
                    {
                        "relation_type": "contiguous_page_overlap_candidate",
                        "asset_paths": [assets[left]["relative_path"], assets[right]["relative_path"]],
                        "shared_page_count_estimate": shared_pages,
                        "longest_consecutive_page_run": longest_run,
                        "comparison_basis": "exact low-resolution rendered-page fingerprints",
                    }
                )

    return groups, relations


def materialize_duplicate_groups(
    groups: UnionFind,
    assets: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    asset_records_by_path: dict[str, dict[str, Any]],
    manual_findings: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    component_by_path: dict[str, tuple[str, list[str]]] = {}
    for members in groups.components().values():
        paths = sorted(assets[index]["relative_path"] for index in members)
        group_id = f"dup-{digest(json_key(paths))[:20]}"
        for path in paths:
            component_by_path[path] = (group_id, paths)

    group_records: dict[str, dict[str, Any]] = {}
    relation_records: list[dict[str, Any]] = []
    for relation in relations:
        paths = relation["asset_paths"]
        if relation["relation_type"] in GROUP_RELATION_TYPES:
            group_id, group_paths = component_by_path.get(paths[0], (None, []))
        else:
            group_id, group_paths = None, []
        relation_status = _relation_status(relation, manual_findings, asset_records_by_path)
        record = {
            **relation,
            "asset_ids": [asset_records_by_path[path]["asset_id"] for path in paths],
            "review_status": relation_status,
        }
        if group_id:
            group_asset_ids = [asset_records_by_path[path]["asset_id"] for path in group_paths]
            record["duplicate_group_id"] = group_id
            record["duplicate_group_asset_ids"] = group_asset_ids
            record["duplicate_group_asset_paths"] = group_paths
            group = group_records.setdefault(
                group_id,
                {
                    "duplicate_group_id": group_id,
                    "asset_ids": group_asset_ids,
                    "asset_paths": group_paths,
                    "relation_types": [],
                    "review_status": "confirmed",
                    "disposition": "retain_original_and_derivative_without_double_counting",
                },
            )
            if relation["relation_type"] not in group["relation_types"]:
                group["relation_types"].append(relation["relation_type"])
            if relation_status != "confirmed":
                group["review_status"] = relation_status
        relation_records.append(record)

    return list(group_records.values()), relation_records


def json_key(paths: list[str]) -> str:
    return "|".join(paths)


def _relation_status(
    relation: dict[str, Any],
    manual_findings: dict[str, dict[str, Any]],
    asset_records_by_path: dict[str, dict[str, Any]],
) -> str:
    if relation["relation_type"] == "ocr_derivative_of":
        ocr_paths = [
            path
            for path in relation["asset_paths"]
            if asset_records_by_path[path]["asset_kind"] == "derived_ocr"
        ]
        if all(path in manual_findings for path in relation["asset_paths"]) and any(
            manual_findings[path].get("text_adapter_status") == "ocr_validated" for path in ocr_paths
        ):
            return "confirmed"
    if relation["relation_type"] == "visual_content_identical" and all(
        path in manual_findings for path in relation["asset_paths"]
    ):
        return "confirmed"
    return "open"

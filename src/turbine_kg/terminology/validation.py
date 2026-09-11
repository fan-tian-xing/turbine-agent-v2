"""Independent validation for Stage 7 artifacts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from .models import PAGE_STATUSES, validate_candidate, validate_page_record


def content_fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_input_manifest(payload: dict) -> dict:
    if payload.get("schema_version") != 1 or payload.get("stage") != "7":
        raise ValueError("invalid Stage 7 input manifest header")
    records = payload.get("pages")
    if not isinstance(records, list) or not records:
        raise ValueError("Stage 7 input manifest has no pages")
    seen = set()
    for row in records:
        validate_page_record(row)
        key = (row["document_logical_id"], row["physical_page"])
        if key in seen:
            raise ValueError("duplicate document physical page in terminology manifest")
        seen.add(key)
    if len(records) != 775:
        raise ValueError(f"Stage 7 input manifest must cover 775 pages, got {len(records)}")
    boundary = payload.get("input_boundary", {})
    excluded = boundary.get("excluded_source_classes", {})
    required_exclusions = {"formal_case_materials", "holdout_materials", "blind_test_materials"}
    if not required_exclusions <= set(excluded) or not boundary.get("exclusion_enforcement"):
        raise ValueError("Stage 7 input manifest must explicitly exclude case, holdout, and blind-test materials")
    if set(payload.get("status_counts", {})) - PAGE_STATUSES:
        raise ValueError("manifest contains an unknown status count")
    return payload


def validate_candidates(records: list[dict], accepted_page_keys: set[tuple[str, int]]) -> dict:
    ids = set()
    for row in records:
        validate_candidate(row)
        if row["candidate_id"] in ids:
            raise ValueError("duplicate terminology candidate ID")
        ids.add(row["candidate_id"])
        if row["occurrence_count"] != len(row["occurrences"]):
            raise ValueError("candidate occurrence_count does not match retained occurrences")
        if row["document_frequency"] != len({item["document_logical_id"] for item in row["occurrences"]}):
            raise ValueError("candidate document_frequency does not match occurrences")
        for occurrence in row["occurrences"]:
            key = (occurrence["document_logical_id"], occurrence["physical_page"])
            if key not in accepted_page_keys:
                raise ValueError("candidate references a non-text-accepted page")
    return {"candidate_count": len(records), "candidate_ids": ids}


def count_occurrences(records: list[dict]) -> int:
    return sum(int(row["occurrence_count"]) for row in records)

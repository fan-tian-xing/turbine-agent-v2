"""Build a read-only registry for the explicitly allowlisted PDF assets.

The registry stores metadata and fingerprints only. It never copies source text,
renames source files, writes to the source tree, or connects to Neo4j.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import pymupdf

from check_source_allowlist import load_allowlist, sha256_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = PROJECT_ROOT.parent / "Original materials"
ALLOWLIST_PATH = PROJECT_ROOT / "config" / "source_allowlist.tsv"
REGISTRY_ROOT = PROJECT_ROOT / "data" / "registry"
MANUAL_FINDINGS_PATH = REGISTRY_ROOT / "source_manual_findings.jsonl"
DOCUMENT_IDENTITY_PATH = PROJECT_ROOT / "config" / "document_identity.tsv"
ASSET_ID_PATTERN = re.compile(r"^asset-[0-9a-f]{20}$")
DOCUMENT_ID_PATTERN = re.compile(r"^doc-[0-9a-f]{20}$")
TEXT_ADAPTER_STATUSES = {
    "not_assessed",
    "native_text_available",
    "ocr_required",
    "ocr_pending_validation",
    "ocr_validated",
    "text_review_required",
}
IDENTIFIER_PATTERN = re.compile(
    r"\b(?:DL\s*[／/]\s*T|DLT|NB\s*[／/]\s*T|NBT|HAF|HAD)\s*[A-Z0-9０-９./／—–\-]*",
    flags=re.IGNORECASE,
)
YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def asset_id_for_path(relative_path: str) -> str:
    """Return a stable registration ID for one allowlisted asset path."""
    return f"asset-{digest(relative_path)[:20]}"


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", value).strip()


def jsonl_write(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def load_manual_findings() -> dict[str, dict[str, Any]]:
    """Load checked-in visual/manual adjudications without reading source text into v2."""
    if not MANUAL_FINDINGS_PATH.exists():
        return {}
    findings: dict[str, dict[str, Any]] = {}
    for line in MANUAL_FINDINGS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        path = record.get("relative_path")
        if not isinstance(path, str) or path in findings:
            raise ValueError("manual findings must have one unique relative_path per record")
        findings[path] = record
    return findings


def load_document_identity_map(path: Path = DOCUMENT_IDENTITY_PATH) -> dict[str, str]:
    """Load controlled path-to-logical-document assignments.

    Fingerprints are evidence for duplicate/derivative detection only. Logical
    document identity must come from this reviewed, versioned map so adding a
    revision or derivative cannot silently rename an existing document.
    """
    if not path.is_file():
        raise FileNotFoundError(f"controlled document identity map is missing: {path}")
    data_lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    reader = csv.DictReader(data_lines, delimiter="\t")
    expected_fields = ["relative_path", "document_logical_id"]
    if reader.fieldnames != expected_fields:
        raise ValueError(
            f"document identity fields must be {expected_fields}, got {reader.fieldnames}"
        )
    assignments: dict[str, str] = {}
    for row in reader:
        relative_path = row["relative_path"]
        document_id = row["document_logical_id"]
        if not relative_path or relative_path in assignments:
            raise ValueError(f"document identity map has duplicate or empty path: {relative_path!r}")
        if not DOCUMENT_ID_PATTERN.fullmatch(document_id):
            raise ValueError(f"invalid document logical ID for {relative_path}: {document_id}")
        assignments[relative_path] = document_id
    return assignments


def source_role(relative_path: str) -> str:
    if relative_path.startswith("OCR/"):
        return "derived_ocr_asset"
    if relative_path.startswith("汽轮机说明书/"):
        return "manufacturer_manual"
    if relative_path.startswith("标准法规/"):
        if "行业标准目录" in relative_path:
            return "standards_catalog"
        return "standard_or_regulation"
    if relative_path.startswith("2.书籍/"):
        return "book"
    return "unclassified_source"


def default_text_adapter_status(source_role_value: str, text_layer_status: str) -> str:
    if source_role_value == "derived_ocr_asset":
        return "ocr_pending_validation"
    if text_layer_status == "native_text":
        return "native_text_available"
    if text_layer_status == "mixed_or_low_quality":
        return "text_review_required"
    return "ocr_required"


def page_fingerprint(page: pymupdf.Page) -> dict[str, Any]:
    text = page.get_text("text")
    normalized = normalize_text(text)
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(0.18, 0.18),
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )
    visual_hash = hashlib.sha256(pixmap.samples).hexdigest()
    return {
        "character_count": len(text),
        "text_fingerprint": digest(normalized),
        "visual_fingerprint": visual_hash,
        "rotation": page.rotation,
        "width": round(page.rect.width, 2),
        "height": round(page.rect.height, 2),
    }


def inspect_pdf(path: Path, relative_path: str, expected_size: int, expected_sha256: str) -> dict[str, Any]:
    actual_size = path.stat().st_size
    actual_sha256 = sha256_file(path)
    document = pymupdf.open(path)
    first_page_text = ""
    last_page_text = ""
    try:
        metadata = document.metadata or {}
        pages = []
        for page_number, page in enumerate(document):
            if page_number < 2:
                first_page_text += page.get_text("text")
            if page_number == len(document) - 1:
                last_page_text = page.get_text("text")
            pages.append(page_fingerprint(page))
    finally:
        document.close()

    page_count = len(pages)
    character_counts = [page["character_count"] for page in pages]
    nonempty_pages = sum(count > 0 for count in character_counts)
    total_characters = sum(character_counts)
    average_characters = total_characters / page_count if page_count else 0
    nonempty_ratio = nonempty_pages / page_count if page_count else 0
    if nonempty_pages == 0:
        text_layer_status = "scan_only"
    elif nonempty_ratio < 0.5 or average_characters < 30:
        text_layer_status = "mixed_or_low_quality"
    else:
        text_layer_status = "native_text"

    visual_sequence = "|".join(page["visual_fingerprint"] for page in pages)
    text_sequence = "|".join(page["text_fingerprint"] for page in pages)
    visual_content_fingerprint = digest(visual_sequence)
    text_content_fingerprint = digest(text_sequence)
    flags: list[str] = []
    if text_layer_status != "native_text":
        flags.append("text_layer_requires_review")
    if any(page["rotation"] for page in pages):
        flags.append("rotated_pages")
    if page_count <= 11:
        flags.append("short_document")
    if page_count and character_counts[0] == 0:
        flags.append("cover_has_no_extractable_text")
    if actual_size != expected_size:
        flags.append("size_mismatch")
    if actual_sha256 != expected_sha256:
        flags.append("sha256_mismatch")

    metadata_title = str(metadata.get("title") or "").strip()
    title_candidate = metadata_title or Path(relative_path).stem
    identifier_candidates = sorted(
        {
            re.sub(r"\s+", " ", candidate).strip(" -")
            for candidate in IDENTIFIER_PATTERN.findall(normalize_text(first_page_text + " " + metadata_title))
            if candidate.strip(" -")
        }
    )
    year_candidates = sorted(set(YEAR_PATTERN.findall(first_page_text + " " + metadata_title)))
    if not first_page_text.strip():
        flags.append("cover_identity_requires_visual_review")

    return {
        "relative_path": relative_path,
        "file_name": path.name,
        "expected_size_bytes": expected_size,
        "size_bytes": actual_size,
        "expected_sha256": expected_sha256,
        "sha256": actual_sha256,
        "page_count": page_count,
        "total_text_characters": total_characters,
        "average_text_characters_per_page": round(average_characters, 2),
        "nonempty_text_pages": nonempty_pages,
        "text_layer_status": text_layer_status,
        "rotated_page_count": sum(page["rotation"] != 0 for page in pages),
        "page_visual_fingerprints": [page["visual_fingerprint"] for page in pages],
        "page_text_fingerprints": [page["text_fingerprint"] for page in pages],
        "visual_content_fingerprint": visual_content_fingerprint,
        "text_content_fingerprint": text_content_fingerprint,
        "pdf_metadata": {
            key: value
            for key, value in metadata.items()
            if value and key in {"title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate"}
        },
        "title_candidate": title_candidate,
        "title_source": "pdf_metadata" if metadata_title else "file_name_only",
        "identifier_candidates": identifier_candidates,
        "year_candidates": year_candidates,
        "identity_status": "review_required",
        "completeness_status": "review_required",
        "external_processing_status": "not_assessed",
        "source_role": source_role(relative_path),
        "review_flags": flags,
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


def duplicate_relations(assets: list[dict[str, Any]]) -> tuple[UnionFind, list[dict[str, Any]]]:
    groups = UnionFind(len(assets))
    relations: list[dict[str, Any]] = []

    def index_by(field: str) -> dict[str, list[int]]:
        index: dict[str, list[int]] = defaultdict(list)
        for number, asset in enumerate(assets):
            index[asset[field]].append(number)
        return index

    for field, relation_type in (
        ("sha256", "byte_identical"),
        ("visual_content_fingerprint", "visual_content_identical"),
        ("text_content_fingerprint", "normalized_text_identical"),
    ):
        for fingerprint, numbers in index_by(field).items():
            if len(numbers) < 2:
                continue
            for left_position, left in enumerate(numbers):
                for right in numbers[left_position + 1 :]:
                    groups.union(left, right)
                    relations.append(
                        {
                            "relation_type": relation_type,
                            "asset_paths": [assets[left]["relative_path"], assets[right]["relative_path"]],
                            "fingerprint_field": field,
                            "fingerprint": fingerprint,
                        }
                    )

    # Detect likely chapter/excerpt overlap using exact rendered-page fingerprints.
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


def build_registry(source_root: Path) -> dict[str, int]:
    metadata, entries = load_allowlist(ALLOWLIST_PATH)
    if int(metadata.get("expected_count", "-1")) != len(entries):
        raise ValueError("allowlist expected_count does not match its rows")
    document_identity = load_document_identity_map()
    allowlisted_paths = {entry["path"] for entry in entries}
    mapped_paths = set(document_identity)
    if mapped_paths != allowlisted_paths:
        missing = sorted(allowlisted_paths - mapped_paths)
        extra = sorted(mapped_paths - allowlisted_paths)
        details = []
        if missing:
            details.append(f"missing paths: {missing}")
        if extra:
            details.append(f"unallowlisted paths: {extra}")
        raise ValueError("document identity map does not match source allowlist (" + "; ".join(details) + ")")
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(f"SOURCE_ROOT does not exist: {source_root}")

    inspected: list[dict[str, Any]] = []
    for entry in entries:
        relative_path = entry["path"]
        path = (source_root / Path(*relative_path.split("/"))).resolve()
        if not path.is_file() or not path.is_relative_to(source_root):
            raise FileNotFoundError(f"allowlisted source is not a file inside SOURCE_ROOT: {relative_path}")
        inspected.append(inspect_pdf(path, relative_path, int(entry["size_bytes"]), entry["sha256"]))

    _groups, relations = duplicate_relations(inspected)
    manual_findings = load_manual_findings()
    relation_paths: dict[str, list[str]] = defaultdict(list)
    for relation in relations:
        for relation_path in relation["asset_paths"]:
            relation_paths[relation_path].append(relation["relation_type"])

    asset_records: list[dict[str, Any]] = []
    review_records: list[dict[str, Any]] = []
    document_members: dict[str, list[str]] = defaultdict(list)
    for number, inspected_asset in enumerate(inspected):
        path = inspected_asset["relative_path"]
        relation_types = sorted(set(relation_paths.get(path, [])))
        role = inspected_asset["source_role"]
        if role == "standards_catalog":
            admission_status = "reference_only"
        elif role == "derived_ocr_asset":
            admission_status = "duplicate_or_derivative"
        else:
            admission_status = "metadata_review_required"
        if relation_types and "visual_content_identical" in relation_types and role != "derived_ocr_asset":
            admission_status = "metadata_review_required"

        manual = manual_findings.get(path, {})
        if manual.get("admission_status"):
            admission_status = manual["admission_status"]
        identity_status = manual.get("identity_status", inspected_asset["identity_status"])
        completeness_status = manual.get("completeness_status", inspected_asset["completeness_status"])
        applicability_status = manual.get("applicability_status", "review_required")
        text_adapter_status = manual.get(
            "text_adapter_status",
            default_text_adapter_status(role, inspected_asset["text_layer_status"]),
        )
        if text_adapter_status not in TEXT_ADAPTER_STATUSES:
            raise ValueError(f"invalid text_adapter_status for {path}: {text_adapter_status}")
        authority_level = manual.get("authority_level", "unknown")
        normative_modality = manual.get("normative_modality", "unknown")
        project_adoption_status = manual.get("project_adoption_status", "unknown")
        manufacturer_approval_status = manual.get("manufacturer_approval_status", "unknown")
        validity_status = manual.get("validity_status", "unknown")
        supersedes = manual.get("supersedes", [])
        exception_basis = manual.get("exception_basis", [])
        if not isinstance(supersedes, list) or not isinstance(exception_basis, list):
            raise ValueError(f"supersedes and exception_basis must be arrays for {path}")
        flags = sorted(
            set(inspected_asset["review_flags"] + relation_types + manual.get("review_flags", []))
        )
        # Asset identity is path registration identity; revision identity is the
        # content hash. This keeps same-content files distinct while preserving
        # the logical document assignment from the controlled identity map.
        asset_id = asset_id_for_path(path)
        if not ASSET_ID_PATTERN.fullmatch(asset_id):
            raise AssertionError(f"generated invalid asset ID for {path}: {asset_id}")
        document_id = document_identity[path]
        document_members[document_id].append(asset_id)
        asset_records.append(
            {
                "asset_id": asset_id,
                "document_logical_id": document_id,
                "revision_id": f"sha256:{inspected_asset['sha256']}",
                "relative_path": path,
                "file_name": inspected_asset["file_name"],
                "size_bytes": inspected_asset["size_bytes"],
                "sha256": inspected_asset["sha256"],
                "page_count": inspected_asset["page_count"],
                "source_role": role,
                "text_layer_status": inspected_asset["text_layer_status"],
                "text_adapter_status": text_adapter_status,
                "text_character_count": inspected_asset["total_text_characters"],
                "rotated_page_count": inspected_asset["rotated_page_count"],
                "title_candidate": inspected_asset["title_candidate"],
                "title_source": inspected_asset["title_source"],
                "identifier_candidates": inspected_asset["identifier_candidates"],
                "year_candidates": inspected_asset["year_candidates"],
                "identity_status": identity_status,
                "completeness_status": completeness_status,
                "external_processing_status": inspected_asset["external_processing_status"],
                "authority_level": authority_level,
                "normative_modality": normative_modality,
                "project_adoption_status": project_adoption_status,
                "manufacturer_approval_status": manufacturer_approval_status,
                "validity_status": validity_status,
                "supersedes": supersedes,
                "exception_basis": exception_basis,
                "visual_content_fingerprint": inspected_asset["visual_content_fingerprint"],
                "text_content_fingerprint": inspected_asset["text_content_fingerprint"],
                "pdf_metadata": inspected_asset["pdf_metadata"],
                "admission_status": admission_status,
                "applicability_status": applicability_status,
                "review_flags": flags,
                "manual_findings": manual.get("findings", []),
                "manual_notes": manual.get("notes", []),
                **(
                    {"manual_follow_up": manual["follow_up_required"]}
                    if manual.get("follow_up_required")
                    else {}
                ),
            }
        )
        confirmed_flags = set(manual.get("resolved_flags", []))
        if admission_status in {"metadata_review_required", "quarantined"} or flags:
            for flag in flags or ["identity_and_applicability_review_required"]:
                item = {
                    "review_id": f"review-{digest(asset_id + '|' + flag)[:20]}",
                    "asset_id": asset_id,
                    "relative_path": path,
                    "issue_type": flag,
                    "status": "resolved" if flag in confirmed_flags else "open",
                    "question": f"Confirm {flag.replace('_', ' ')} before semantic processing.",
                }
                if flag in confirmed_flags:
                    item["resolution"] = "Resolved by checked-in manual finding; source PDF remains unchanged."
                review_records.append(item)
        if manual.get("follow_up_required"):
            review_records.append(
                {
                    "review_id": f"review-{digest(asset_id + '|manual_follow_up')[:20]}",
                    "asset_id": asset_id,
                    "relative_path": path,
                    "issue_type": "manual_follow_up",
                    "status": "open",
                    "question": manual["follow_up_required"],
                }
            )

    document_records = []
    for document_id, member_assets in sorted(document_members.items()):
        member_records = [record for record in asset_records if record["asset_id"] in member_assets]
        relation_types = sorted({flag for record in member_records for flag in record["review_flags"]})
        title_candidates = sorted({record["title_candidate"] for record in member_records})
        source_roles = sorted({record["source_role"] for record in member_records})
        document_records.append(
            {
                "document_logical_id": document_id,
                "asset_ids": member_assets,
                "canonical_asset_id": member_assets[0],
                "title_candidates": title_candidates,
                "source_roles": source_roles,
                "document_status": "metadata_review_required",
                "applicability_status": "review_required",
                "member_relation_flags": relation_types,
            }
        )

    duplicate_records = []
    for number, relation in enumerate(relations, start=1):
        confirmed_relation = (
            relation["relation_type"] == "visual_content_identical"
            and all(path in manual_findings for path in relation["asset_paths"])
        )
        duplicate_records.append(
            {
                "duplicate_group_id": f"dup-{digest(json.dumps(relation, ensure_ascii=False, sort_keys=True))[:20]}",
                **relation,
                "review_status": "confirmed" if confirmed_relation else "open",
                "disposition": (
                    "retain_original_and_derivative_without_double_counting"
                    if confirmed_relation
                    else "do_not_delete_source_file"
                ),
            }
        )

    REGISTRY_ROOT.mkdir(parents=True, exist_ok=True)
    jsonl_write(REGISTRY_ROOT / "source_assets.jsonl", asset_records)
    jsonl_write(REGISTRY_ROOT / "source_documents.jsonl", document_records)
    jsonl_write(REGISTRY_ROOT / "source_duplicate_groups.jsonl", duplicate_records)
    jsonl_write(REGISTRY_ROOT / "source_review_queue.jsonl", review_records)
    summary = {
        "schema_version": 1,
        "source_root": "../Original materials",
        "physical_asset_count": len(asset_records),
        "logical_document_count": len(document_records),
        "admitted_source_count": sum(record["admission_status"] == "admitted" for record in asset_records),
        "independent_extractable_source_count": len(
            {
                document_id
                for document_id, member_assets in document_members.items()
                if any(
                    record["admission_status"] == "admitted"
                    for record in asset_records
                    if record["asset_id"] in member_assets
                )
                and any(
                    record["text_adapter_status"] in {"native_text_available", "ocr_validated"}
                    for record in asset_records
                    if record["asset_id"] in member_assets
                )
            }
        ),
        "duplicate_relation_count": len(duplicate_records),
        "review_queue_count": len(review_records),
        "manual_finding_count": sum(bool(record["manual_findings"]) for record in asset_records),
        "admission_status_counts": {
            status: sum(record["admission_status"] == status for record in asset_records)
            for status in {
                "admitted",
                "quarantined",
                "metadata_review_required",
                "duplicate_or_derivative",
                "reference_only",
                "excluded_private_evaluation",
            }
        },
        "text_layer_status_counts": {
            status: sum(record["text_layer_status"] == status for record in asset_records)
            for status in {"native_text", "mixed_or_low_quality", "scan_only"}
        },
        "text_adapter_status_counts": {
            status: sum(record["text_adapter_status"] == status for record in asset_records)
            for status in sorted(TEXT_ADAPTER_STATUSES)
        },
        "source_role_counts": {
            role: sum(record["source_role"] == role for record in asset_records)
            for role in sorted({record["source_role"] for record in asset_records})
        },
        "processing_note": "Metadata and fingerprints only; no source text copied and no Neo4j write performed.",
    }
    (REGISTRY_ROOT / "source_registry_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "physical_assets": len(asset_records),
        "logical_documents": len(document_records),
        "duplicate_relations": len(duplicate_records),
        "review_items": len(review_records),
    }


def main() -> int:
    source_root = Path(os.environ.get("SOURCE_ROOT", DEFAULT_SOURCE_ROOT))
    try:
        summary = build_registry(source_root)
    except Exception as error:  # pragma: no cover - command-line diagnostics
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "source registry built: "
        f"{summary['physical_assets']} physical assets, "
        f"{summary['logical_documents']} logical documents, "
        f"{summary['duplicate_relations']} duplicate/overlap relations, "
        f"{summary['review_items']} review items"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

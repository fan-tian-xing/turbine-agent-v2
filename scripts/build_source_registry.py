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
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TEXT_ADAPTER_STATUSES = {
    "not_assessed",
    "native_text_available",
    "ocr_required",
    "ocr_pending_validation",
    "ocr_validated",
    "text_review_required",
}
EXTERNAL_PROCESSING_STATUSES = {"not_assessed", "allowed", "not_allowed"}
EXTERNAL_PROCESSING_STATUSES = EXTERNAL_PROCESSING_STATUSES | {"local_only"}
AUTHORITY_LEVELS = {
    "regulatory",
    "national_standard",
    "industry_standard",
    "manufacturer",
    "project_document",
    "textbook",
    "reference",
    "unknown",
}
NORMATIVE_MODALITIES = {
    "mandatory",
    "should",
    "recommended",
    "permitted",
    "prohibited",
    "informational",
    "mixed",
    "not_applicable",
    "unknown",
}
PROJECT_ADOPTION_STATUSES = {
    "adopted",
    "not_adopted",
    "candidate",
    "not_applicable",
    "unknown",
}
MANUFACTURER_APPROVAL_STATUSES = {
    "approved",
    "not_approved",
    "not_applicable",
    "unknown",
}
VALIDITY_STATUSES = {"current", "superseded", "expired", "draft", "unknown"}
RELATION_STATUSES = {"identified", "confirmed_none", "review_required", "unknown"}
DOCUMENT_CONTEXT_MIXED = "mixed"
DOCUMENT_STATUSES = {
    "admitted",
    "quarantined",
    "metadata_review_required",
    "duplicate_or_derivative",
    "reference_only",
}
ASSET_RECORD_FIELDS = {
    "asset_id",
    "document_logical_id",
    "revision_id",
    "relative_path",
    "file_name",
    "size_bytes",
    "sha256",
    "page_count",
    "source_role",
    "text_layer_status",
    "text_adapter_status",
    "text_character_count",
    "rotated_page_count",
    "title_candidate",
    "title_source",
    "identifier_candidates",
    "year_candidates",
    "identity_status",
    "completeness_status",
    "external_processing_status",
    "authority_level",
    "normative_modality",
    "project_adoption_status",
    "manufacturer_approval_status",
    "validity_status",
    "supersedes",
    "supersedes_status",
    "exception_basis",
    "exception_basis_status",
    "visual_content_fingerprint",
    "text_content_fingerprint",
    "pdf_metadata",
    "admission_status",
    "applicability_status",
    "applicability_scope",
    "review_flags",
    "manual_findings",
    "manual_notes",
    "manual_follow_up",
}
DOCUMENT_RECORD_FIELDS = {
    "document_logical_id",
    "asset_ids",
    "canonical_asset_id",
    "title_candidates",
    "source_roles",
    "document_status",
    "identity_status",
    "completeness_status",
    "applicability_status",
    "applicability_scope",
    "text_adapter_status",
    "authority_level",
    "normative_modality",
    "project_adoption_status",
    "manufacturer_approval_status",
    "validity_status",
    "supersedes",
    "supersedes_status",
    "exception_basis",
    "exception_basis_status",
    "open_review_count",
    "member_relation_flags",
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


def validate_asset_record(record: dict[str, Any]) -> None:
    """Validate the generated asset contract without relying on permissive JSON extras."""
    required = {
        "asset_id",
        "document_logical_id",
        "revision_id",
        "relative_path",
        "sha256",
        "size_bytes",
        "page_count",
        "source_role",
        "text_layer_status",
        "text_adapter_status",
        "identity_status",
        "completeness_status",
        "external_processing_status",
        "authority_level",
        "normative_modality",
        "project_adoption_status",
        "manufacturer_approval_status",
        "validity_status",
        "supersedes",
        "supersedes_status",
        "exception_basis",
        "exception_basis_status",
        "admission_status",
        "applicability_status",
        "applicability_scope",
    }
    missing = sorted(required - record.keys())
    if missing:
        raise ValueError(f"asset record is missing required fields: {missing}")
    extra = sorted(set(record) - ASSET_RECORD_FIELDS)
    if extra:
        raise ValueError(f"asset record has unexpected fields: {extra}")
    if not isinstance(record["asset_id"], str) or not ASSET_ID_PATTERN.fullmatch(record["asset_id"]):
        raise ValueError("asset_id must match the controlled asset ID pattern")
    if not isinstance(record["document_logical_id"], str) or not DOCUMENT_ID_PATTERN.fullmatch(record["document_logical_id"]):
        raise ValueError("document_logical_id must match the controlled document ID pattern")
    if not isinstance(record["revision_id"], str):
        raise ValueError("revision_id must be a string")
    if not isinstance(record["relative_path"], str) or record["relative_path"].startswith("/") or not record["relative_path"].lower().endswith(".pdf"):
        raise ValueError("relative_path must be a relative PDF path")
    if not isinstance(record["sha256"], str) or not SHA256_PATTERN.fullmatch(record["sha256"]):
        raise ValueError("asset sha256 must be a 64-character lowercase hex string")
    if isinstance(record["size_bytes"], bool) or not isinstance(record["size_bytes"], int) or record["size_bytes"] < 1:
        raise ValueError("size_bytes must be a positive integer")
    if isinstance(record["page_count"], bool) or not isinstance(record["page_count"], int) or record["page_count"] < 1:
        raise ValueError("page_count must be a positive integer")
    enum_fields = {
        "text_layer_status": {"native_text", "mixed_or_low_quality", "scan_only"},
        "text_adapter_status": TEXT_ADAPTER_STATUSES,
        "identity_status": {"review_required", "confirmed"},
        "completeness_status": {"review_required", "complete", "incomplete"},
        "external_processing_status": EXTERNAL_PROCESSING_STATUSES,
        "authority_level": AUTHORITY_LEVELS,
        "normative_modality": NORMATIVE_MODALITIES,
        "project_adoption_status": PROJECT_ADOPTION_STATUSES,
        "manufacturer_approval_status": MANUFACTURER_APPROVAL_STATUSES,
        "validity_status": VALIDITY_STATUSES,
        "supersedes_status": RELATION_STATUSES,
        "exception_basis_status": RELATION_STATUSES,
        "admission_status": {
            "admitted",
            "quarantined",
            "metadata_review_required",
            "duplicate_or_derivative",
            "reference_only",
            "excluded_private_evaluation",
        },
        "applicability_status": {"review_required", "confirmed"},
    }
    for field, allowed in enum_fields.items():
        if record[field] not in allowed:
            raise ValueError(f"invalid {field}: {record[field]!r}")
    for field in ("supersedes", "exception_basis"):
        if not isinstance(record[field], list) or not all(isinstance(item, str) for item in record[field]):
            raise ValueError(f"{field} must be an array of strings")
    if (
        not isinstance(record["applicability_scope"], list)
        or not record["applicability_scope"]
        or not all(isinstance(item, str) and item.strip() for item in record["applicability_scope"])
    ):
        raise ValueError("applicability_scope must be a non-empty array of strings")
    if not SHA256_PATTERN.fullmatch(record["sha256"]):
        raise ValueError("asset sha256 must be a 64-character lowercase hex string")
    if record["revision_id"] != f"sha256:{record['sha256']}":
        raise ValueError("revision_id must be derived from sha256")


def _collapsed_context(member_records: list[dict[str, Any]], field: str) -> str:
    values = {record[field] for record in member_records}
    return next(iter(values)) if len(values) == 1 else DOCUMENT_CONTEXT_MIXED


def _collapsed_relation_status(member_records: list[dict[str, Any]], field: str) -> str:
    values = {record[field] for record in member_records}
    return next(iter(values)) if len(values) == 1 else "review_required"


def validate_document_record(record: dict[str, Any]) -> None:
    """Validate the logical-document contract independently from asset records."""
    required = DOCUMENT_RECORD_FIELDS
    missing = sorted(required - record.keys())
    if missing:
        raise ValueError(f"document record is missing required fields: {missing}")
    extra = sorted(set(record) - DOCUMENT_RECORD_FIELDS)
    if extra:
        raise ValueError(f"document record has unexpected fields: {extra}")
    for field, pattern, label in (
        ("document_logical_id", DOCUMENT_ID_PATTERN, "document_logical_id"),
        ("canonical_asset_id", ASSET_ID_PATTERN, "canonical_asset_id"),
    ):
        if not isinstance(record[field], str) or not pattern.fullmatch(record[field]):
            raise ValueError(f"{label} has an invalid controlled ID")
    if not isinstance(record["asset_ids"], list) or not all(
        isinstance(item, str) and ASSET_ID_PATTERN.fullmatch(item) for item in record["asset_ids"]
    ):
        raise ValueError("asset_ids must be an array of controlled asset IDs")
    if record["canonical_asset_id"] not in record["asset_ids"]:
        raise ValueError("canonical_asset_id must be a member of asset_ids")
    if record["document_status"] not in DOCUMENT_STATUSES:
        raise ValueError(f"invalid document_status: {record['document_status']!r}")
    for field, allowed in (
        ("identity_status", {"review_required", "confirmed"}),
        ("completeness_status", {"review_required", "complete", "incomplete"}),
        ("applicability_status", {"review_required", "confirmed"}),
        ("text_adapter_status", TEXT_ADAPTER_STATUSES | {"mixed"}),
        ("authority_level", AUTHORITY_LEVELS | {DOCUMENT_CONTEXT_MIXED}),
        ("normative_modality", NORMATIVE_MODALITIES | {DOCUMENT_CONTEXT_MIXED}),
        ("project_adoption_status", PROJECT_ADOPTION_STATUSES | {DOCUMENT_CONTEXT_MIXED}),
        ("manufacturer_approval_status", MANUFACTURER_APPROVAL_STATUSES | {DOCUMENT_CONTEXT_MIXED}),
        ("validity_status", VALIDITY_STATUSES | {DOCUMENT_CONTEXT_MIXED}),
        ("supersedes_status", RELATION_STATUSES),
        ("exception_basis_status", RELATION_STATUSES),
    ):
        if record[field] not in allowed:
            raise ValueError(f"invalid {field}: {record[field]!r}")
    for field in ("title_candidates", "source_roles", "applicability_scope", "supersedes", "exception_basis", "member_relation_flags"):
        if not isinstance(record[field], list) or not all(isinstance(item, str) for item in record[field]):
            raise ValueError(f"{field} must be an array of strings")
    if not record["applicability_scope"]:
        raise ValueError("applicability_scope must not be empty")
    if isinstance(record["open_review_count"], bool) or not isinstance(record["open_review_count"], int) or record["open_review_count"] < 0:
        raise ValueError("open_review_count must be a non-negative integer")


def aggregate_document_status(member_records: list[dict[str, Any]]) -> str:
    """Return a logical-document status from its physical/derived asset members."""
    if all(record["admission_status"] == "duplicate_or_derivative" for record in member_records):
        return "duplicate_or_derivative"
    if all(record["admission_status"] == "reference_only" for record in member_records):
        return "reference_only"
    if any(record["admission_status"] == "quarantined" for record in member_records) and not any(
        record["admission_status"] == "admitted" for record in member_records
    ):
        return "quarantined"
    eligible = [
        record
        for record in member_records
        if record["admission_status"] not in {"duplicate_or_derivative", "reference_only"}
    ]
    if eligible and any(record["admission_status"] == "admitted" for record in eligible):
        if (
            all(record["identity_status"] == "confirmed" for record in eligible)
            and all(record["completeness_status"] == "complete" for record in eligible)
            and all(record["applicability_status"] == "confirmed" for record in eligible)
            and all(record["external_processing_status"] != "not_assessed" for record in eligible)
            and any(
                record["text_adapter_status"] in {"native_text_available", "ocr_validated"}
                for record in member_records
            )
        ):
            return "admitted"
    return "metadata_review_required"


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
    # OCR output is a derived asset even when re-rasterization makes its
    # rendered-page fingerprint differ from the source.  Record this
    # controlled relationship from the reviewed logical-document map instead
    # of misclassifying it as byte/visual identity.
    path_to_index = {asset["relative_path"]: index for index, asset in enumerate(inspected)}
    by_document: dict[str, list[str]] = defaultdict(list)
    for relative_path, document_id in document_identity.items():
        by_document[document_id].append(relative_path)
    for document_paths in by_document.values():
        originals = [
            path
            for path in document_paths
            if inspected[path_to_index[path]]["source_role"] != "derived_ocr_asset"
        ]
        derivatives = [
            path
            for path in document_paths
            if inspected[path_to_index[path]]["source_role"] == "derived_ocr_asset"
        ]
        if not originals:
            continue
        for derivative in derivatives:
            source = originals[0]
            _groups.union(path_to_index[source], path_to_index[derivative])
            relations.append(
                {
                    "relation_type": "ocr_derivative_of",
                    "asset_paths": [source, derivative],
                    "comparison_basis": "controlled logical-document identity map and derived_ocr_asset role",
                }
            )
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
        applicability_scope = manual.get("applicability_scope", ["unknown"])
        if (
            not isinstance(applicability_scope, list)
            or not applicability_scope
            or not all(isinstance(item, str) and item.strip() for item in applicability_scope)
        ):
            raise ValueError(f"applicability_scope must be a non-empty array of strings for {path}")
        year_candidates = manual.get("year_candidates", inspected_asset["year_candidates"])
        if not isinstance(year_candidates, list) or not all(isinstance(item, str) for item in year_candidates):
            raise ValueError(f"year_candidates must be an array of strings for {path}")
        title_candidate = manual.get("title_candidate", inspected_asset["title_candidate"])
        title_source = manual.get("title_source", inspected_asset["title_source"])
        identifier_candidates = manual.get("identifier_candidates", inspected_asset["identifier_candidates"])
        pdf_metadata = manual.get("pdf_metadata", inspected_asset["pdf_metadata"])
        if not isinstance(title_candidate, str) or not isinstance(title_source, str):
            raise ValueError(f"title metadata must be strings for {path}")
        if not isinstance(identifier_candidates, list) or not all(isinstance(item, str) for item in identifier_candidates):
            raise ValueError(f"identifier_candidates must be an array of strings for {path}")
        if not isinstance(pdf_metadata, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in pdf_metadata.items()
        ):
            raise ValueError(f"pdf_metadata must be a string map for {path}")
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
        external_processing_status = manual.get(
            "external_processing_status",
            inspected_asset["external_processing_status"],
        )
        if external_processing_status not in EXTERNAL_PROCESSING_STATUSES:
            raise ValueError(
                f"invalid external_processing_status for {path}: {external_processing_status}"
            )
        supersedes = manual.get("supersedes", [])
        supersedes_status = manual.get("supersedes_status", "review_required")
        exception_basis = manual.get("exception_basis", [])
        exception_basis_status = manual.get("exception_basis_status", "review_required")
        if not isinstance(supersedes, list) or not isinstance(exception_basis, list):
            raise ValueError(f"supersedes and exception_basis must be arrays for {path}")
        for field, value, allowed in (
            ("authority_level", authority_level, AUTHORITY_LEVELS),
            ("normative_modality", normative_modality, NORMATIVE_MODALITIES),
            ("project_adoption_status", project_adoption_status, PROJECT_ADOPTION_STATUSES),
            ("manufacturer_approval_status", manufacturer_approval_status, MANUFACTURER_APPROVAL_STATUSES),
            ("validity_status", validity_status, VALIDITY_STATUSES),
            ("supersedes_status", supersedes_status, RELATION_STATUSES),
            ("exception_basis_status", exception_basis_status, RELATION_STATUSES),
        ):
            if value not in allowed:
                raise ValueError(f"invalid {field} for {path}: {value}")
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
        asset_record = {
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
                "title_candidate": title_candidate,
                "title_source": title_source,
                "identifier_candidates": identifier_candidates,
                "year_candidates": year_candidates,
                "identity_status": identity_status,
                "completeness_status": completeness_status,
                "external_processing_status": external_processing_status,
                "authority_level": authority_level,
                "normative_modality": normative_modality,
                "project_adoption_status": project_adoption_status,
                "manufacturer_approval_status": manufacturer_approval_status,
                "validity_status": validity_status,
                "supersedes": supersedes,
                "supersedes_status": supersedes_status,
                "exception_basis": exception_basis,
                "exception_basis_status": exception_basis_status,
                "visual_content_fingerprint": inspected_asset["visual_content_fingerprint"],
                "text_content_fingerprint": inspected_asset["text_content_fingerprint"],
                "pdf_metadata": pdf_metadata,
                "admission_status": admission_status,
                "applicability_status": applicability_status,
                "applicability_scope": applicability_scope,
                "review_flags": flags,
                "manual_findings": manual.get("findings", []),
                "manual_notes": manual.get("notes", []),
                **(
                    {"manual_follow_up": manual["follow_up_required"]}
                    if manual.get("follow_up_required")
                    else {}
                ),
            }
        validate_asset_record(asset_record)
        asset_records.append(asset_record)
        confirmed_flags = set(manual.get("resolved_flags", []))
        queue_flags = list(flags)
        if admission_status in {"metadata_review_required", "quarantined"}:
            if identity_status != "confirmed":
                queue_flags.append("identity_review_required")
            if completeness_status == "review_required":
                queue_flags.append("completeness_review_required")
            if applicability_status != "confirmed":
                queue_flags.append("applicability_review_required")
            if applicability_scope == ["unknown"]:
                queue_flags.append("applicability_scope_review_required")
            if external_processing_status == "not_assessed":
                queue_flags.append("external_processing_review_required")
            if any(
                value in {"unknown", "review_required"}
                for value in (
                    authority_level,
                    normative_modality,
                    project_adoption_status,
                    manufacturer_approval_status,
                    validity_status,
                    supersedes_status,
                    exception_basis_status,
                )
            ):
                queue_flags.append("normative_context_review_required")
            if text_adapter_status not in {"native_text_available", "ocr_validated"}:
                queue_flags.append("text_adapter_review_required")
        if queue_flags:
            for flag in sorted(set(queue_flags)):
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
    open_review_counts: dict[str, int] = defaultdict(int)
    for item in review_records:
        if item["status"] == "open":
            open_review_counts[item["asset_id"]] += 1
    for document_id, member_assets in sorted(document_members.items()):
        member_records = [record for record in asset_records if record["asset_id"] in member_assets]
        relation_types = sorted({flag for record in member_records for flag in record["review_flags"]})
        title_candidates = sorted({record["title_candidate"] for record in member_records})
        source_roles = sorted({record["source_role"] for record in member_records})
        document_status = aggregate_document_status(member_records)
        eligible = [
            record
            for record in member_records
            if record["admission_status"] not in {"duplicate_or_derivative", "reference_only"}
        ]
        canonical = next(
            (record for record in eligible if record["admission_status"] == "admitted"),
            member_records[0],
        )
        adapter = next(
            (
                record["text_adapter_status"]
                for record in member_records
                if record["text_adapter_status"] in {"native_text_available", "ocr_validated"}
            ),
            "text_review_required",
        )
        document_records.append(
            {
                "document_logical_id": document_id,
                "asset_ids": member_assets,
                "canonical_asset_id": canonical["asset_id"],
                "title_candidates": title_candidates,
                "source_roles": source_roles,
                "document_status": document_status,
                "identity_status": "confirmed" if eligible and all(record["identity_status"] == "confirmed" for record in eligible) else "review_required",
                "completeness_status": "complete" if eligible and all(record["completeness_status"] == "complete" for record in eligible) else "review_required",
                "applicability_status": "confirmed" if eligible and all(record["applicability_status"] == "confirmed" for record in eligible) else "review_required",
                "applicability_scope": sorted(
                    {
                        scope
                        for record in member_records
                        for scope in record["applicability_scope"]
                        if scope != "unknown"
                    }
                ) or ["unknown"],
                "text_adapter_status": adapter,
                "authority_level": _collapsed_context(member_records, "authority_level"),
                "normative_modality": _collapsed_context(member_records, "normative_modality"),
                "project_adoption_status": _collapsed_context(member_records, "project_adoption_status"),
                "manufacturer_approval_status": _collapsed_context(member_records, "manufacturer_approval_status"),
                "validity_status": _collapsed_context(member_records, "validity_status"),
                "supersedes": sorted({item for record in member_records for item in record["supersedes"]}),
                "supersedes_status": _collapsed_relation_status(member_records, "supersedes_status"),
                "exception_basis": sorted({item for record in member_records for item in record["exception_basis"]}),
                "exception_basis_status": _collapsed_relation_status(member_records, "exception_basis_status"),
                "open_review_count": sum(open_review_counts[record["asset_id"]] for record in member_records),
                "member_relation_flags": relation_types,
            }
        )
        validate_document_record(document_records[-1])

    duplicate_records = []
    for number, relation in enumerate(relations, start=1):
        confirmed_relation = (
            relation["relation_type"] == "visual_content_identical"
            and all(path in manual_findings for path in relation["asset_paths"])
        ) or (
            relation["relation_type"] == "ocr_derivative_of"
            and all(path in manual_findings for path in relation["asset_paths"])
            and any(
                manual_findings[path].get("text_adapter_status") == "ocr_validated"
                for path in relation["asset_paths"]
                if path.startswith("OCR/")
            )
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
        "independent_extractable_source_count": sum(
            document["document_status"] == "admitted"
            and document["text_adapter_status"] in {"native_text_available", "ocr_validated"}
            for document in document_records
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

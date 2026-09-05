"""Registry contracts, controlled values, and deterministic validation."""

from __future__ import annotations

import re
from typing import Any


ASSET_ID_PATTERN = re.compile(r"^asset-[0-9a-f]{20}$")
DOCUMENT_ID_PATTERN = re.compile(r"^doc-[0-9a-f]{20}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVISION_ID_PATTERN = re.compile(r"^rev-[0-9a-f]{20}$")
SOURCE_ROLES = {
    "book",
    "manufacturer_manual",
    "standard_or_regulation",
    "standards_catalog",
    "unclassified_source",
}
ASSET_KINDS = {"original", "derived_ocr"}

TEXT_ADAPTER_STATUSES = {
    "not_assessed",
    "native_text_available",
    "ocr_required",
    "ocr_pending_validation",
    "ocr_validated",
    "text_review_required",
}
EXTERNAL_PROCESSING_STATUSES = {
    "not_assessed",
    "allowed",
    "not_allowed",
    "local_only",
}
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
    "source_root_id",
    "asset_kind",
    "source_profile_id",
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
    "revision_ids",
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


def validate_asset_record(record: dict[str, Any]) -> None:
    required = {
        "asset_id",
        "document_logical_id",
        "revision_id",
        "source_root_id",
        "asset_kind",
        "source_profile_id",
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
    if not isinstance(record["revision_id"], str) or not REVISION_ID_PATTERN.fullmatch(record["revision_id"]):
        raise ValueError("revision_id must match the controlled revision ID pattern")
    if not isinstance(record["source_profile_id"], str) or not record["source_profile_id"].strip():
        raise ValueError("source_profile_id must be a non-empty string")
    if not isinstance(record["relative_path"], str) or record["relative_path"].startswith("/") or not record["relative_path"].lower().endswith(".pdf"):
        raise ValueError("relative_path must be a relative PDF path")
    if not isinstance(record["sha256"], str) or not SHA256_PATTERN.fullmatch(record["sha256"]):
        raise ValueError("asset sha256 must be a 64-character lowercase hex string")
    if record["source_root_id"] not in {"source", "ocr_derived"}:
        raise ValueError(f"invalid source_root_id: {record['source_root_id']!r}")
    if isinstance(record["size_bytes"], bool) or not isinstance(record["size_bytes"], int) or record["size_bytes"] < 1:
        raise ValueError("size_bytes must be a positive integer")
    if isinstance(record["page_count"], bool) or not isinstance(record["page_count"], int) or record["page_count"] < 1:
        raise ValueError("page_count must be a positive integer")
    enum_fields = {
        "text_layer_status": {"native_text", "mixed_or_low_quality", "scan_only"},
        "text_adapter_status": TEXT_ADAPTER_STATUSES,
        "source_role": SOURCE_ROLES,
        "asset_kind": ASSET_KINDS,
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


def collapsed_context(member_records: list[dict[str, Any]], field: str) -> str:
    values = {record[field] for record in member_records}
    return next(iter(values)) if len(values) == 1 else DOCUMENT_CONTEXT_MIXED


def collapsed_relation_status(member_records: list[dict[str, Any]], field: str) -> str:
    values = {record[field] for record in member_records}
    return next(iter(values)) if len(values) == 1 else "review_required"


def validate_document_record(record: dict[str, Any]) -> None:
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
    if not isinstance(record["revision_ids"], list) or not record["revision_ids"] or not all(
        isinstance(item, str) and REVISION_ID_PATTERN.fullmatch(item) for item in record["revision_ids"]
    ):
        raise ValueError("revision_ids must be a non-empty array of controlled revision IDs")
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

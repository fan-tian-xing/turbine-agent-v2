"""Small contracts for Stage 7 terminology discovery artifacts."""

from __future__ import annotations

PAGE_STATUSES = frozenset({"text_accepted", "visual_only", "quarantined", "excluded_non_content"})
CANDIDATE_TYPES = frozenset({
    "equipment", "component", "action", "parameter", "phenomenon", "process",
    "verification", "requirement", "applicability_condition", "synonym_candidate",
    "abbreviation_candidate", "old_name_candidate", "ocr_variant_candidate",
})
DISCOVERY_METHODS = frozenset({"lexical_pattern", "numeric_unit_pattern", "document_metadata", "explicit_relation_pattern"})


def validate_page_record(record: dict) -> None:
    required = {
        "page_id", "document_key", "document_logical_id", "revision_id",
        "processing_asset_id", "authority_asset_id", "physical_page", "page_status",
        "text_source", "processing_relative_path", "authority_relative_path",
        "processing_text_sha256", "exclusion_reason",
        "analysis_text_sha256",
    }
    missing = required - record.keys()
    if missing:
        raise ValueError(f"terminology page record is missing: {', '.join(sorted(missing))}")
    if record["page_status"] not in PAGE_STATUSES:
        raise ValueError(f"invalid terminology page status: {record['page_status']}")
    if not isinstance(record["physical_page"], int) or record["physical_page"] < 1:
        raise ValueError("physical_page must be a positive integer")
    if record["page_status"] == "text_accepted" and not record["processing_text_sha256"]:
        raise ValueError("text_accepted page must have a processing text fingerprint")
    if record["page_status"] == "text_accepted" and not record["analysis_text_sha256"]:
        raise ValueError("text_accepted page must have an analysis text fingerprint")
    if record["page_status"] != "text_accepted" and not record["exclusion_reason"]:
        raise ValueError("non-accepted page must explain its exclusion")


def validate_candidate(record: dict) -> None:
    required = {
        "candidate_id", "surface_form", "normalized_form", "candidate_type",
        "occurrence_count", "document_frequency", "occurrences", "text_origins",
        "is_ocr_variant", "review_status", "capability_question_ids",
        "discovery_method", "content_fingerprint", "requires_original_confirmation",
        "document_occurrence_counts", "family_weighted_score",
    }
    missing = required - record.keys()
    if missing:
        raise ValueError(f"terminology candidate is missing: {', '.join(sorted(missing))}")
    if record["candidate_type"] not in CANDIDATE_TYPES:
        raise ValueError(f"invalid terminology candidate type: {record['candidate_type']}")
    if record["discovery_method"] not in DISCOVERY_METHODS:
        raise ValueError(f"invalid terminology discovery method: {record['discovery_method']}")
    if record["review_status"] not in {"candidate_only", "accepted", "rejected", "deferred"}:
        raise ValueError("invalid terminology review status")
    if not record["occurrences"] or not record["capability_question_ids"]:
        raise ValueError("candidate must retain an occurrence and a capability question")
    if not isinstance(record["requires_original_confirmation"], bool):
        raise ValueError("requires_original_confirmation must be boolean")
    if not isinstance(record["document_occurrence_counts"], dict) or not record["document_occurrence_counts"]:
        raise ValueError("candidate must retain document occurrence counts")
    if not 0 <= float(record["family_weighted_score"]) <= 1:
        raise ValueError("family_weighted_score must be between 0 and 1")
    if record["candidate_type"] in {"synonym_candidate", "old_name_candidate"}:
        relation = record.get("relation")
        if not isinstance(relation, dict) or relation.get("relation_type") != record["candidate_type"] or not relation.get("left") or not relation.get("right"):
            raise ValueError("relationship candidate must retain its explicit left/right terms")

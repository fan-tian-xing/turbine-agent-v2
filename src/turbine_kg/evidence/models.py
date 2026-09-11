"""Ontology-independent Evidence models for Stage 6.

Evidence is a reviewed, citable wrapper around one or more Document IR
SourceSpan objects.  It does not contain engineering semantics or statements.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


EVIDENCE_ROLES = {
    "requirement_source",
    "observation_source",
    "calculation_basis",
    "approved_answer",
    "verification_record",
    "background",
}
EVIDENCE_CONTENT_KINDS = {"text", "paragraph", "heading", "list", "table", "caption", "mixed"}
EVIDENCE_DISPOSITIONS = {
    "structured",
    "region_scoped",
    "visual_only",
    "quarantined",
    "metadata_only",
}
EVIDENCE_REVIEW_STATUSES = {"accepted", "needs_review", "rejected"}
FIGURE_CONTEXT_KINDS = {"whole_figure", "caption", "figure_text"}
TABLE_REVIEW_SCOPES = {"cell", "table_region"}


@dataclass(frozen=True, slots=True)
class EvidenceLocation:
    """A redundant, checked location snapshot for one SourceSpan."""

    source_span_id: str
    processing_asset_id: str
    original_asset_id: str
    physical_page: int
    logical_page: str | None
    bbox: Any
    rotation_deg: int
    authority_rotation_deg: int = 0
    coordinate_transform: str = "document_ir_canonical_pdf_points_v1"


@dataclass(frozen=True, slots=True)
class TableContext:
    """Reviewed table coordinates needed to interpret a value in context."""

    table_id: str
    row_indices: tuple[int, ...]
    column_indices: tuple[int, ...]
    header_cell_ids: tuple[str, ...] = ()
    value_cell_ids: tuple[str, ...] = ()
    unit_text: str | None = None
    table_label: str | None = None
    continuation_from_physical_page: int | None = None
    continuation_to_physical_page: int | None = None
    inherited_header_text: tuple[str, ...] = ()
    review_scope: str = "cell"
    leaf_column_count: int | None = None
    header_hierarchy: tuple[str, ...] = ()
    partition_note: str | None = None


@dataclass(frozen=True, slots=True)
class FigureContext:
    """A reviewed link to a whole figure, its caption or its visible text."""

    figure_id: str
    context_kind: str
    caption_source_span_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Evidence:
    """A citable source unit with no inferred engineering meaning."""

    evidence_id: str
    evidence_version_id: str
    document_logical_id: str
    revision_id: str
    parsing_run_id: str
    authority_asset_id: str
    processing_asset_id: str | None
    authority_basis: str
    source_span_ids: tuple[str, ...]
    source_text: str
    source_text_sha256: str
    evidence_role: str
    content_kind: str
    text_origin: str
    effective_text: str
    effective_text_origin: str
    locations: tuple[EvidenceLocation, ...]
    disposition: str = "structured"
    review_status: str = "needs_review"
    reviewer: str | None = None
    reviewed_at: str | None = None
    review_reason: str | None = None
    correction_ids: tuple[str, ...] = ()
    table_context: tuple[TableContext, ...] = ()
    figure_context: tuple[FigureContext, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """Validated Stage 6 evidence records for one Document IR revision."""

    schema_version: int
    revision_id: str
    evidence: tuple[Evidence, ...]
    source_ir_output_fingerprint: str

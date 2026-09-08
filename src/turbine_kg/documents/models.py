"""Ontology-independent Document IR models for Stage 4.

This module deliberately contains document structure only.  Engineering
semantics remain in the Stage 3 and later semantic layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PAGE_MODES = {"native_text", "scan_only", "mixed", "review_required"}
TEXT_ORIGINS = {"native_text", "ocr_text", "manual_correction"}
BLOCK_TYPES = {"text", "heading", "paragraph", "list", "table", "image", "caption", "header", "footer"}


@dataclass(frozen=True, slots=True)
class BBox:
    """Canonical page coordinates: top-left origin, PDF points."""

    x0: float
    y0: float
    x1: float
    y1: float

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


@dataclass(frozen=True, slots=True)
class Document:
    document_logical_id: str
    title: str
    registry_document_ref: str


@dataclass(frozen=True, slots=True)
class DocumentRevision:
    revision_id: str
    document_logical_id: str
    revision_label: str
    revision_basis: str
    content_fingerprint: str | None = None
    effective_date: str | None = None
    status: str = "current"


@dataclass(frozen=True, slots=True)
class AssetRef:
    asset_id: str
    document_logical_id: str
    revision_id: str
    asset_kind: str
    relative_path: str
    sha256: str
    source_root_id: str
    derived_from_asset_id: str | None = None
    derivation_type: str | None = None
    derivation_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class AssetPageRef:
    asset_id: str
    page_index: int
    alignment_status: str = "confirmed"


@dataclass(frozen=True, slots=True)
class Page:
    page_id: str
    revision_id: str
    pdf_page_index: int
    display_page_number: int
    printed_page_label: str | None
    width_pt: float
    height_pt: float
    rotation_deg: int
    page_mode: str
    text_layer_status: str
    visual_fingerprint: str | None
    asset_page_refs: tuple[AssetPageRef, ...]


@dataclass(frozen=True, slots=True)
class ParsingRun:
    parsing_run_id: str
    input_asset_ids: tuple[str, ...]
    input_revision_ids: tuple[str, ...]
    parser_profile_id: str
    parser_version: str
    config_fingerprint: str
    input_fingerprint: str
    output_fingerprint: str | None = None
    status: str = "succeeded"
    started_at: str | None = None
    finished_at: str | None = None
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class BlockVersion:
    block_version_id: str
    page_id: str
    parsing_run_id: str
    block_ordinal: int
    block_type: str
    text: str
    bbox: BBox | None
    reading_order: int
    parent_block_version_id: str | None = None
    confidence: float | None = None
    text_origin: str = "native_text"


@dataclass(frozen=True, slots=True)
class TableCell:
    cell_id: str
    table_id: str
    block_version_id: str
    row_index: int
    column_index: int
    row_span: int = 1
    column_span: int = 1


@dataclass(frozen=True, slots=True)
class Table:
    table_id: str
    block_version_id: str
    caption_block_version_id: str | None
    row_count: int
    column_count: int
    cell_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Figure:
    figure_id: str
    block_version_id: str
    caption_block_version_id: str | None
    figure_label: str | None


@dataclass(frozen=True, slots=True)
class SourceSpan:
    source_span_id: str
    page_id: str
    block_version_ids: tuple[str, ...]
    quote: str
    content_kind: str
    char_start: int | None
    char_end: int | None
    bbox: BBox | None
    text_origin: str
    table_id: str | None = None
    row_index: int | None = None
    column_index: int | None = None


@dataclass(frozen=True, slots=True)
class DocumentIR:
    schema_version: int
    document: Document
    revision: DocumentRevision
    assets: tuple[AssetRef, ...]
    parsing_run: ParsingRun
    pages: tuple[Page, ...]
    blocks: tuple[BlockVersion, ...]
    tables: tuple[Table, ...] = ()
    table_cells: tuple[TableCell, ...] = ()
    figures: tuple[Figure, ...] = ()
    source_spans: tuple[SourceSpan, ...] = ()


def record_value(value: Any) -> Any:
    """Convert an IR dataclass tree to JSON-compatible values."""
    if hasattr(value, "__dataclass_fields__"):
        return {field: record_value(getattr(value, field)) for field in value.__dataclass_fields__}
    if isinstance(value, tuple):
        return [record_value(item) for item in value]
    if isinstance(value, list):
        return [record_value(item) for item in value]
    if isinstance(value, dict):
        return {key: record_value(item) for key, item in value.items()}
    return value

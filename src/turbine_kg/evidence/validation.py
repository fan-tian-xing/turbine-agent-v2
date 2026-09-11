"""Deterministic validation for the Stage 6 Evidence contract."""

from __future__ import annotations

import hashlib
import re

from turbine_kg.documents.models import DocumentIR
from turbine_kg.documents.ids import evidence_id, evidence_version_id
from turbine_kg.documents.validation import validate_document_ir

from .models import (
    EVIDENCE_CONTENT_KINDS,
    EVIDENCE_DISPOSITIONS,
    EVIDENCE_REVIEW_STATUSES,
    EVIDENCE_ROLES,
    FIGURE_CONTEXT_KINDS,
    TABLE_REVIEW_SCOPES,
    Evidence,
    EvidenceBundle,
)


_ID = re.compile(r"^evidence-[0-9a-f]{20}$")
_VERSION_ID = re.compile(r"^evver-[0-9a-f]{20}$")
_AUTHORITY_BASES = {"original_pdf_visual_review", "original_pdf_native_text_review"}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_evidence_bundle(bundle: EvidenceBundle, ir: DocumentIR) -> EvidenceBundle:
    """Validate Evidence and re-check every location against the source IR."""

    validate_document_ir(ir)
    if bundle.schema_version != 1:
        raise ValueError("unsupported Evidence schema_version")
    if bundle.revision_id != ir.revision.revision_id:
        raise ValueError("Evidence bundle revision does not match Document IR")
    if bundle.source_ir_output_fingerprint != ir.parsing_run.output_fingerprint:
        raise ValueError("Evidence bundle was built from a different Document IR output")

    spans = {span.source_span_id: span for span in ir.source_spans}
    pages = {page.page_id: page for page in ir.pages}
    blocks = {block.block_version_id: block for block in ir.blocks}
    corrections = {item.correction_id: item for item in ir.manual_corrections}
    tables = {table.table_id: table for table in ir.tables}
    cells = {cell.cell_id: cell for cell in ir.table_cells}
    figures = {figure.figure_id: figure for figure in ir.figures}
    evidence_ids: list[str] = []
    for item in bundle.evidence:
        if not _ID.fullmatch(item.evidence_id):
            raise ValueError("Evidence has an invalid controlled ID")
        if not _VERSION_ID.fullmatch(item.evidence_version_id):
            raise ValueError("Evidence has an invalid version ID")
        if item.evidence_id in evidence_ids:
            raise ValueError("duplicate Evidence IDs")
        evidence_ids.append(item.evidence_id)
        if item.document_logical_id != ir.document.document_logical_id:
            raise ValueError("Evidence document does not match Document IR")
        if item.revision_id != ir.revision.revision_id:
            raise ValueError("Evidence revision does not match Document IR")
        if item.parsing_run_id != ir.parsing_run.parsing_run_id:
            raise ValueError("Evidence parsing run does not match Document IR")
        if item.authority_basis not in _AUTHORITY_BASES:
            raise ValueError("Evidence authority basis must name original PDF review")
        asset_by_id = {asset.asset_id: asset for asset in ir.assets}
        if item.authority_asset_id not in asset_by_id:
            raise ValueError("Evidence authority asset is missing from Document IR")
        if asset_by_id[item.authority_asset_id].asset_kind != "original":
            raise ValueError("Evidence authority asset must be an original asset")
        if item.processing_asset_id is not None and item.processing_asset_id not in asset_by_id:
            raise ValueError("Evidence processing asset is missing from Document IR")
        if not item.source_span_ids:
            raise ValueError("Evidence requires at least one SourceSpan")
        if any(span_id not in spans for span_id in item.source_span_ids):
            raise ValueError("Evidence references a missing SourceSpan")
        if item.evidence_role not in EVIDENCE_ROLES:
            raise ValueError(f"unsupported evidence role: {item.evidence_role}")
        if item.content_kind not in EVIDENCE_CONTENT_KINDS:
            raise ValueError(f"unsupported Evidence content kind: {item.content_kind}")
        if item.disposition not in EVIDENCE_DISPOSITIONS:
            raise ValueError(f"unsupported Evidence disposition: {item.disposition}")
        if item.review_status not in EVIDENCE_REVIEW_STATUSES:
            raise ValueError(f"unsupported Evidence review status: {item.review_status}")
        if not item.source_text or item.source_text_sha256 != _sha256(item.source_text):
            raise ValueError("Evidence source text hash does not match source text")
        if not item.effective_text or item.effective_text_origin not in {"native_text", "ocr_text", "manual_correction", "mixed"}:
            raise ValueError("Evidence effective text and origin are required")

        selected = [spans[span_id] for span_id in item.source_span_ids]
        expected_evidence_id = evidence_id(
            item.revision_id,
            tuple(pages[span.page_id].display_page_number for span in selected),
            tuple(span.bbox for span in selected),
            item.source_text,
            item.evidence_role,
        )
        if item.evidence_id != expected_evidence_id:
            raise ValueError("Evidence ID does not match its stable identity fields")
        expected_version_id = evidence_version_id(
            ir.parsing_run.output_fingerprint or "",
            item.source_span_ids,
        )
        if item.evidence_version_id != expected_version_id:
            raise ValueError("Evidence version ID does not match its Document IR inputs")
        order_keys = [
            (
                pages[span.page_id].display_page_number,
                min(blocks[block_id].reading_order for block_id in span.block_version_ids),
            )
            for span in selected
        ]
        if order_keys != sorted(order_keys):
            raise ValueError("Evidence SourceSpans must follow document reading order")
        selected_pages = sorted({pages[span.page_id].display_page_number for span in selected})
        if any(right - left > 1 for left, right in zip(selected_pages, selected_pages[1:])):
            raise ValueError("Evidence SourceSpans must be on the same or adjacent pages")
        expected_text = "\n".join(span.quote for span in selected)
        if item.source_text != expected_text:
            raise ValueError("Evidence source text must equal the ordered SourceSpan quotes")
        expected_text_origins = {span.text_origin for span in selected}
        expected_text_origin = next(iter(expected_text_origins)) if len(expected_text_origins) == 1 else "mixed"
        if item.text_origin != expected_text_origin:
            raise ValueError("Evidence text_origin does not match its SourceSpans")
        if item.effective_text_origin != "manual_correction" and item.effective_text != item.source_text:
            raise ValueError("uncorrected effective text must equal source text")
        if item.effective_text_origin == "manual_correction":
            if not item.correction_ids or any(value not in corrections for value in item.correction_ids):
                raise ValueError("manual effective text requires traceable correction IDs")
            accepted_corrections = [corrections[value] for value in item.correction_ids]
            if any(value.status != "accepted" for value in accepted_corrections):
                raise ValueError("manual effective text requires accepted corrections")
            selected_block_ids = {block_id for span in selected for block_id in span.block_version_ids}
            if any(value.block_version_id not in selected_block_ids for value in accepted_corrections):
                raise ValueError("manual correction is not linked to the selected SourceSpans")
            if item.effective_text != "\n".join(value.corrected_text for value in accepted_corrections):
                raise ValueError("effective text does not match its accepted corrections")
        elif item.correction_ids:
            raise ValueError("correction IDs require manual_correction effective text origin")
        if len(item.locations) != len(selected):
            raise ValueError("Evidence must carry one checked location per SourceSpan")
        for location, span in zip(item.locations, selected):
            page = pages[span.page_id]
            page_assets = {ref.asset_id for ref in page.asset_page_refs}
            if location.source_span_id != span.source_span_id:
                raise ValueError("Evidence location order does not match SourceSpan order")
            if location.processing_asset_id not in page_assets:
                raise ValueError("Evidence processing asset is not attached to the source page")
            if location.original_asset_id not in page_assets:
                raise ValueError("Evidence original asset is not attached to the source page")
            if asset_by_id[location.original_asset_id].asset_kind != "original":
                raise ValueError("Evidence original_asset_id must identify an original asset")
            if location.original_asset_id != item.authority_asset_id:
                raise ValueError("Evidence location authority asset differs from Evidence authority asset")
            if item.processing_asset_id is not None and location.processing_asset_id != item.processing_asset_id:
                raise ValueError("Evidence location processing asset differs from Evidence processing asset")
            if location.physical_page != page.display_page_number:
                raise ValueError("Evidence physical page does not match the PDF page index")
            if location.logical_page != page.printed_page_label:
                raise ValueError("Evidence logical page does not match the Document IR")
            if location.bbox != span.bbox:
                raise ValueError("Evidence bbox does not match the SourceSpan bbox")
            if location.rotation_deg != page.rotation_deg:
                raise ValueError("Evidence rotation does not match the source page")
            if location.authority_rotation_deg not in {0, 90, 180, 270}:
                raise ValueError("Evidence authority rotation is unsupported")
            if (
                location.authority_rotation_deg != location.rotation_deg
                and location.coordinate_transform != "aligned_display_pdf_points_with_explicit_authority_rotation_v1"
            ):
                raise ValueError("different processing/original rotation requires an explicit coordinate transform")
            if item.review_status == "accepted" and item.disposition in {"structured", "region_scoped"} and location.bbox is None:
                raise ValueError("accepted citable Evidence requires a bbox")
            if item.disposition == "structured" and location.bbox is not None:
                bbox_area = (location.bbox.x1 - location.bbox.x0) * (location.bbox.y1 - location.bbox.y0)
                if bbox_area >= page.width_pt * page.height_pt * 0.95:
                    raise ValueError("structured Evidence cannot use a whole-page bbox")
        location_processing_assets = {location.processing_asset_id for location in item.locations}
        location_original_assets = {location.original_asset_id for location in item.locations}
        if len(location_processing_assets) != 1 or len(location_original_assets) != 1:
            raise ValueError("one Evidence item cannot cross processing or original assets")
        only_processing_asset = next(iter(location_processing_assets))
        only_original_asset = next(iter(location_original_assets))
        expected_processing_asset = None if only_processing_asset == only_original_asset else only_processing_asset
        if item.processing_asset_id != expected_processing_asset:
            raise ValueError("Evidence processing_asset_id does not match location provenance")
        if item.disposition == "structured" and item.content_kind == "mixed":
            raise ValueError("mixed content cannot enter structured Evidence")
        if item.review_status == "accepted" and not (item.reviewer and item.reviewed_at and item.review_reason):
            raise ValueError("accepted Evidence requires reviewer, reviewed_at and review_reason")
        for context in item.table_context:
            if context.table_id not in tables:
                raise ValueError("table context requires a Document IR table")
            if context.table_id not in {span.table_id for span in selected if span.table_id}:
                raise ValueError("table context is not linked to the Evidence SourceSpan")
            table = tables[context.table_id]
            if context.review_scope not in TABLE_REVIEW_SCOPES:
                raise ValueError("unsupported table review scope")
            if context.leaf_column_count != table.column_count:
                raise ValueError("reviewed leaf-column count differs from the Document IR table")
            if not context.header_hierarchy or not context.header_cell_ids:
                raise ValueError("table context requires reviewed header structure")
            if context.review_scope == "table_region":
                if item.disposition != "region_scoped":
                    raise ValueError("table-region review must remain region-scoped")
                if context.row_indices or context.column_indices or context.value_cell_ids:
                    raise ValueError("table-region review cannot claim unreviewed data cells")
            elif not context.row_indices or not context.column_indices:
                raise ValueError("cell table context requires row and column coordinates")
            if any(value < 0 or value >= table.row_count for value in context.row_indices):
                raise ValueError("table context row exceeds table dimensions")
            if any(value < 0 or value >= table.column_count for value in context.column_indices):
                raise ValueError("table context column exceeds table dimensions")
            linked_cells = set(context.header_cell_ids + context.value_cell_ids)
            if any(value not in cells for value in linked_cells):
                raise ValueError("table context references an invalid cell")
            if context.review_scope == "cell" and not context.value_cell_ids:
                raise ValueError("cell table context requires valid value cells")
            if any(cells[value].table_id != context.table_id for value in linked_cells):
                raise ValueError("table context cell belongs to another table")
            value_rows = {cells[value].row_index for value in context.value_cell_ids}
            value_columns = {cells[value].column_index for value in context.value_cell_ids}
            if value_rows != set(context.row_indices) or value_columns != set(context.column_indices):
                raise ValueError("table value cells do not match declared row and column coordinates")
            table_page_id = blocks[table.block_version_id].page_id
            table_page_number = pages[table_page_id].display_page_number
            for page_number in (context.continuation_from_physical_page, context.continuation_to_physical_page):
                if page_number is not None and page_number < 1:
                    raise ValueError("table continuation pages must be one-based")
                if page_number == table_page_number:
                    raise ValueError("table continuation cannot point to its own page")
        for context in item.figure_context:
            if context.figure_id not in figures:
                raise ValueError("figure context requires a Document IR figure")
            if context.context_kind not in FIGURE_CONTEXT_KINDS:
                raise ValueError("unsupported figure context kind")
            if any(span_id not in spans for span_id in context.caption_source_span_ids):
                raise ValueError("figure caption context references a missing SourceSpan")
            figure_page_id = blocks[figures[context.figure_id].block_version_id].page_id
            for span_id in context.caption_source_span_ids:
                caption = spans[span_id]
                if caption.page_id != figure_page_id or caption.content_kind != "caption":
                    raise ValueError("figure caption context must use a caption SourceSpan on the figure page")

    return bundle

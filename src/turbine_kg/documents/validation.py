"""Runtime validation for the ontology-independent Document IR."""

from __future__ import annotations

import re

from .ids import block_version_id, page_id, source_span_id
from .models import BLOCK_TYPES, PAGE_MODES, TEXT_ORIGINS, BBox, DocumentIR


ID_PATTERNS = {
    "document_logical_id": re.compile(r"^doc-[0-9a-f]{20}$"),
    "revision_id": re.compile(r"^rev-[0-9a-f]{20}$"),
    "asset_id": re.compile(r"^asset-[0-9a-f]{20}$"),
    "page_id": re.compile(r"^page-[0-9a-f]{20}$"),
    "block_version_id": re.compile(r"^blockv-[0-9a-f]{20}$"),
    "source_span_id": re.compile(r"^span-[0-9a-f]{20}$"),
    "parsing_run_id": re.compile(r"^run-[0-9a-f]{20}$"),
    "table_id": re.compile(r"^table-[0-9a-f]{20}$"),
    "cell_id": re.compile(r"^cell-[0-9a-f]{20}$"),
    "figure_id": re.compile(r"^figure-[0-9a-f]{20}$"),
    "correction_id": re.compile(r"^correction-[0-9a-f]{20}$"),
}


def _id(value: str, kind: str) -> None:
    if not isinstance(value, str) or not ID_PATTERNS[kind].fullmatch(value):
        raise ValueError(f"{kind} has an invalid controlled ID: {value!r}")


def _unique(values: list[str], kind: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {kind} IDs")


def _bbox(value: BBox | None, width: float, height: float) -> None:
    if value is None:
        return
    if not all(isinstance(item, (int, float)) for item in (value.x0, value.y0, value.x1, value.y1)):
        raise ValueError("bbox coordinates must be numeric")
    if value.x0 < 0 or value.y0 < 0 or value.x1 < value.x0 or value.y1 < value.y0:
        raise ValueError("bbox must be ordered and non-negative")
    if value.x1 > width or value.y1 > height:
        raise ValueError("bbox must stay within the page")


def validate_document_ir(ir: DocumentIR) -> DocumentIR:
    if ir.schema_version != 1:
        raise ValueError("unsupported Document IR schema_version")
    _id(ir.document.document_logical_id, "document_logical_id")
    _id(ir.revision.revision_id, "revision_id")
    if ir.revision.document_logical_id != ir.document.document_logical_id:
        raise ValueError("revision does not belong to document")
    _id(ir.parsing_run.parsing_run_id, "parsing_run_id")
    if ir.parsing_run.input_revision_ids != (ir.revision.revision_id,):
        raise ValueError("parsing run must name the IR revision exactly once")

    asset_ids = [asset.asset_id for asset in ir.assets]
    _unique(asset_ids, "asset")
    for asset in ir.assets:
        _id(asset.asset_id, "asset_id")
        if asset.document_logical_id != ir.document.document_logical_id or asset.revision_id != ir.revision.revision_id:
            raise ValueError("asset is bound to a different document or revision")
        if asset.derived_from_asset_id and asset.derived_from_asset_id not in asset_ids:
            raise ValueError("derived asset source is missing from the IR")
    if set(ir.parsing_run.input_asset_ids) - set(asset_ids):
        raise ValueError("parsing run references an asset outside the IR")

    page_ids = [page.page_id for page in ir.pages]
    _unique(page_ids, "page")
    pages = {page.page_id: page for page in ir.pages}
    for page in ir.pages:
        _id(page.page_id, "page_id")
        if (
            page.revision_id != ir.revision.revision_id
            or page.pdf_page_index < 0
            or page.display_page_number != page.pdf_page_index + 1
        ):
            raise ValueError("page has an invalid revision or page number")
        if page.page_id != page_id(ir.revision.revision_id, page.pdf_page_index):
            raise ValueError("page_id is not derived from revision and physical page index")
        if page.width_pt <= 0 or page.height_pt <= 0:
            raise ValueError("page dimensions must be positive")
        if page.rotation_deg not in {0, 90, 180, 270}:
            raise ValueError("page rotation must be 0, 90, 180 or 270")
        if page.page_mode not in PAGE_MODES:
            raise ValueError(f"unsupported page mode: {page.page_mode}")
        for asset_page_ref in page.asset_page_refs:
            if asset_page_ref.asset_id not in asset_ids:
                raise ValueError("page references an asset outside the IR")
            if asset_page_ref.page_index < 0:
                raise ValueError("asset page reference must use a non-negative page index")

    block_ids = [block.block_version_id for block in ir.blocks]
    _unique(block_ids, "block version")
    blocks = {block.block_version_id: block for block in ir.blocks}
    for block in ir.blocks:
        _id(block.block_version_id, "block_version_id")
        if block.page_id not in pages or block.parsing_run_id != ir.parsing_run.parsing_run_id:
            raise ValueError("block is not attached to this page or parsing run")
        if block.block_version_id != block_version_id(block.parsing_run_id, block.page_id, block.block_ordinal):
            raise ValueError("block_version_id is not derived from parsing run, page and ordinal")
        if block.block_type not in BLOCK_TYPES or block.text_origin not in TEXT_ORIGINS:
            raise ValueError("block type or text origin is not supported")
        _bbox(block.bbox, pages[block.page_id].width_pt, pages[block.page_id].height_pt)
        if block.parent_block_version_id and block.parent_block_version_id not in blocks:
            raise ValueError("block parent is missing")

    table_ids = [table.table_id for table in ir.tables]
    _unique(table_ids, "table")
    for table in ir.tables:
        _id(table.table_id, "table_id")
        if table.block_version_id not in blocks or table.row_count < 1 or table.column_count < 1:
            raise ValueError("table has invalid structure")
    cell_ids = [cell.cell_id for cell in ir.table_cells]
    _unique(cell_ids, "table cell")
    for cell in ir.table_cells:
        _id(cell.cell_id, "cell_id")
        if cell.table_id not in table_ids or cell.block_version_id not in blocks:
            raise ValueError("table cell references a missing object")
        if cell.row_index < 0 or cell.column_index < 0 or cell.row_span < 1 or cell.column_span < 1:
            raise ValueError("table cell indexes and spans must be valid")
        table = next(table for table in ir.tables if table.table_id == cell.table_id)
        if cell.row_index + cell.row_span > table.row_count or cell.column_index + cell.column_span > table.column_count:
            raise ValueError("table cell span exceeds table dimensions")
    cell_id_set = set(cell_ids)
    for table in ir.tables:
        if any(cell_id not in cell_id_set for cell_id in table.cell_ids):
            raise ValueError("table references a missing cell")

    figure_ids = [figure.figure_id for figure in ir.figures]
    _unique(figure_ids, "figure")
    for figure in ir.figures:
        _id(figure.figure_id, "figure_id")
        if figure.block_version_id not in blocks:
            raise ValueError("figure references a missing block")

    span_ids = [span.source_span_id for span in ir.source_spans]
    _unique(span_ids, "source span")
    for span in ir.source_spans:
        _id(span.source_span_id, "source_span_id")
        if span.page_id not in pages or not span.block_version_ids:
            raise ValueError("source span must reference a page and at least one block")
        if any(block_id not in blocks for block_id in span.block_version_ids):
            raise ValueError("source span references a missing block")
        if any(blocks[block_id].page_id != span.page_id for block_id in span.block_version_ids):
            raise ValueError("source span blocks must belong to its page")
        first_block = blocks[span.block_version_ids[0]]
        if span.source_span_id != source_span_id(first_block.block_version_id, 0, span.quote):
            raise ValueError("source_span_id is not derived from its block and quote")
        if span.text_origin not in TEXT_ORIGINS:
            raise ValueError("source span text origin is not supported")
        if span.content_kind == "table" and span.table_id is None:
            raise ValueError("table source span must reference a table")
        if (span.row_index is None) != (span.column_index is None):
            raise ValueError("table source span row and column must be provided together")
        if span.row_index is not None and (span.row_index < 0 or span.column_index < 0):
            raise ValueError("table source span row and column must be non-negative")
        if span.char_start is not None and span.char_start < 0:
            raise ValueError("source span char_start must be non-negative")
        if span.char_end is not None and (span.char_end < 0 or (span.char_start is not None and span.char_end < span.char_start)):
            raise ValueError("source span char_end is invalid")
        page = pages[span.page_id]
        _bbox(span.bbox, page.width_pt, page.height_pt)
        if span.table_id and span.table_id not in table_ids:
            raise ValueError("source span table reference is missing")

    correction_ids = [correction.correction_id for correction in ir.manual_corrections]
    _unique(correction_ids, "manual correction")
    for correction in ir.manual_corrections:
        _id(correction.correction_id, "correction_id")
        target = blocks.get(correction.block_version_id)
        if target is None:
            raise ValueError("manual correction references a missing block")
        if correction.original_text != target.text:
            raise ValueError("manual correction original_text must match the parser output")
        if not correction.reason.strip() or not correction.reviewer.strip() or not correction.reviewed_at.strip():
            raise ValueError("manual correction requires reason, reviewer and reviewed_at")
        if correction.status not in {"accepted", "rejected"}:
            raise ValueError("manual correction status is unsupported")
    return ir

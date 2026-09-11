"""Add original-page-reviewed table regions to a one-page Document IR."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from .ids import block_version_id, source_span_id, table_cell_id, table_id
from .models import BBox, BlockVersion, DocumentIR, SourceSpan, Table, TableCell, record_value
from .validation import validate_document_ir


def apply_reviewed_table_regions(ir: DocumentIR, regions: list[dict[str, object]]) -> DocumentIR:
    """Append header-only table structures whose regions were checked on the original PDF.

    The adapter deliberately does not create data-cell text.  It records a table
    region and its reviewed leaf headers so Stage 6 can cite the table without
    promoting unreviewed OCR cell values into structured Evidence.
    """

    validate_document_ir(ir)
    if len(ir.pages) != 1:
        raise ValueError("reviewed table adapter requires exactly one page")
    if not regions:
        raise ValueError("at least one reviewed table region is required")

    page = ir.pages[0]
    blocks = list(ir.blocks)
    spans = list(ir.source_spans)
    tables = list(ir.tables)
    cells = list(ir.table_cells)
    next_ordinal = max((block.block_ordinal for block in blocks), default=-1) + 1
    next_order = max((block.reading_order for block in blocks), default=-1) + 1

    for region in regions:
        quote = str(region["source_text"]).strip()
        leaf_headers = tuple(str(value).strip() for value in region["leaf_headers"])
        bbox_value = region["bbox"]
        bbox = BBox(
            float(bbox_value["x0"]),
            float(bbox_value["y0"]),
            float(bbox_value["x1"]),
            float(bbox_value["y1"]),
        )
        if not quote or not leaf_headers:
            raise ValueError("reviewed table region requires source text and leaf headers")

        block_id = block_version_id(ir.parsing_run.parsing_run_id, page.page_id, next_ordinal)
        block = BlockVersion(
            block_version_id=block_id,
            page_id=page.page_id,
            parsing_run_id=ir.parsing_run.parsing_run_id,
            block_ordinal=next_ordinal,
            block_type="table",
            text=quote,
            bbox=bbox,
            reading_order=next_order,
            text_origin=str(region.get("text_origin", "ocr_text")),
        )
        current_table_id = table_id(block_id)
        header_cells = tuple(
            TableCell(
                cell_id=table_cell_id(current_table_id, 0, column_index),
                table_id=current_table_id,
                block_version_id=block_id,
                row_index=0,
                column_index=column_index,
            )
            for column_index in range(len(leaf_headers))
        )
        table = Table(
            table_id=current_table_id,
            block_version_id=block_id,
            caption_block_version_id=None,
            row_count=1,
            column_count=len(leaf_headers),
            cell_ids=tuple(cell.cell_id for cell in header_cells),
        )
        span = SourceSpan(
            source_span_id=source_span_id(block_id, 0, quote),
            page_id=page.page_id,
            block_version_ids=(block_id,),
            quote=quote,
            content_kind="table",
            char_start=0,
            char_end=len(quote),
            bbox=bbox,
            text_origin=block.text_origin,
            table_id=current_table_id,
        )
        blocks.append(block)
        cells.extend(header_cells)
        tables.append(table)
        spans.append(span)
        next_ordinal += 1
        next_order += 1

    payload = {
        "pages": [record_value(value) for value in ir.pages],
        "blocks": [record_value(value) for value in blocks],
        "tables": [record_value(value) for value in tables],
        "table_cells": [record_value(value) for value in cells],
        "figures": [record_value(value) for value in ir.figures],
        "source_spans": [record_value(value) for value in spans],
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return validate_document_ir(replace(
        ir,
        blocks=tuple(blocks),
        tables=tuple(tables),
        table_cells=tuple(cells),
        source_spans=tuple(spans),
        parsing_run=replace(
            ir.parsing_run,
            parser_version=ir.parsing_run.parser_version + "+original-reviewed-table-regions-v1",
            output_fingerprint=fingerprint,
        ),
    ))

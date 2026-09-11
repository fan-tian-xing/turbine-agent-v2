"""Apply reviewed OCR line boxes as a deterministic Document IR text adapter."""

from __future__ import annotations

from dataclasses import replace

from .ids import block_version_id, figure_id, source_span_id
from .models import BBox, BlockVersion, DocumentIR, Figure, SourceSpan
from .page_identity import apply_page_identity
from .validation import validate_document_ir


def apply_reviewed_ocr_boxes(
    ir: DocumentIR,
    boxes: list[dict[str, object]],
    *,
    dpi: int,
    logical_page: str | None,
    authority_width_pt: float | None = None,
    authority_height_pt: float | None = None,
    authority_rotation_deg: int | None = None,
) -> DocumentIR:
    """Replace a page-level OCR text layer with reviewed line text and boxes.

    The OCR text remains processing provenance. Evidence built from the result
    must still name the Original materials asset as its authority.
    """

    validate_document_ir(ir)
    if len(ir.pages) != 1:
        raise ValueError("reviewed OCR box adapter requires exactly one parsed page")
    page = ir.pages[0]
    if ir.tables or ir.table_cells or ir.manual_corrections:
        raise ValueError("reviewed OCR box adapter refuses an already enhanced Document IR")
    if page.rotation_deg not in {0, 360}:
        raise ValueError("rotated OCR pages require an explicit original-page coordinate mapping")
    if dpi <= 0 or not boxes:
        raise ValueError("reviewed OCR boxes and a positive dpi are required")
    authority_width = page.width_pt if authority_width_pt is None else authority_width_pt
    authority_height = page.height_pt if authority_height_pt is None else authority_height_pt
    authority_rotation = page.rotation_deg if authority_rotation_deg is None else authority_rotation_deg
    if authority_rotation not in {0, 90, 180, 270}:
        raise ValueError("authority rotation must be 0, 90, 180 or 270")
    if abs(authority_width - page.width_pt) > 0.5 or abs(authority_height - page.height_pt) > 0.5:
        raise ValueError("processing and original displayed page dimensions are not aligned")

    scale = 72.0 / float(dpi)
    blocks: list[BlockVersion] = []
    spans: list[SourceSpan] = []
    for ordinal, row in enumerate(boxes):
        text = str(row.get("text", "")).strip()
        if not text:
            continue
        bbox = BBox(
            float(row["x0"]) * scale,
            float(row["y0"]) * scale,
            float(row["x1"]) * scale,
            float(row["y1"]) * scale,
        )
        if bbox.x0 < 0 or bbox.y0 < 0 or bbox.x1 > authority_width or bbox.y1 > authority_height:
            raise ValueError("reviewed OCR bbox falls outside the aligned Original materials display page")
        block_id = block_version_id(ir.parsing_run.parsing_run_id, page.page_id, len(blocks))
        block = BlockVersion(
            block_version_id=block_id,
            page_id=page.page_id,
            parsing_run_id=ir.parsing_run.parsing_run_id,
            block_ordinal=len(blocks),
            block_type="paragraph",
            text=text,
            bbox=bbox,
            reading_order=len(blocks),
            text_origin="ocr_text",
        )
        blocks.append(block)
        spans.append(SourceSpan(
            source_span_id=source_span_id(block_id, 0, text),
            page_id=page.page_id,
            block_version_ids=(block_id,),
            quote=text,
            content_kind="paragraph",
            char_start=0,
            char_end=len(text),
            bbox=bbox,
            text_origin="ocr_text",
        ))

    figures: tuple[Figure, ...] = ()
    image_blocks = [block for block in ir.blocks if block.block_type == "image"]
    if image_blocks:
        source_image = image_blocks[0]
        image_id = block_version_id(ir.parsing_run.parsing_run_id, page.page_id, len(blocks))
        image_block = replace(
            source_image,
            block_version_id=image_id,
            block_ordinal=len(blocks),
            reading_order=len(blocks),
        )
        blocks.append(image_block)
        figures = (Figure(figure_id(image_id), image_id, None, None),)

    adapted = replace(
        ir,
        blocks=tuple(blocks),
        source_spans=tuple(spans),
        figures=figures,
        tables=(),
        table_cells=(),
        manual_corrections=(),
        parsing_run=replace(
            ir.parsing_run,
            parser_version=ir.parsing_run.parser_version + "+reviewed-rapidocr-lines-v1",
            config_fingerprint=(
                ir.parsing_run.config_fingerprint
                + f"|authority-display-alignment:{page.rotation_deg}->{authority_rotation}"
            ),
        ),
    )
    return apply_page_identity(adapted, {page.display_page_number: logical_page})

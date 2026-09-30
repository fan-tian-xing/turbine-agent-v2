"""Apply reviewed OCR line boxes as a deterministic Document IR text adapter."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json

from .ids import block_version_id, figure_id, source_span_id
from .models import BBox, BlockVersion, DocumentIR, Figure, SourceSpan, record_value
from .page_identity import apply_page_identity
from .validation import validate_document_ir


def vertical_margin_text_runs(
    boxes: list[dict[str, object]], *, page_width_px: float
) -> tuple[tuple[int, ...], ...]:
    """Flag likely vertical navigation text before it becomes body Evidence.

    This is a review signal, not an automatic deletion rule: a table can also
    contain vertical labels. Callers must compare flagged boxes with the
    original page and explicitly keep or exclude them.
    """

    if page_width_px <= 0:
        raise ValueError("page width must be positive")
    candidates: list[tuple[int, float, float, float]] = []
    for index, row in enumerate(boxes):
        value = str(row.get("text", "")).strip()
        if len(value) != 1 or not ("\u3400" <= value <= "\u9fff"):
            continue
        x0, x1 = float(row["x0"]), float(row["x1"])
        y0, y1 = float(row["y0"]), float(row["y1"])
        if x0 < page_width_px * 0.8 or x1 <= x0 or y1 <= y0:
            continue
        candidates.append((index, (x0 + x1) / 2, y0, y1))

    runs: list[tuple[int, ...]] = []
    for seed in candidates:
        if any(seed[0] in run for run in runs):
            continue
        same_column = sorted(
            (item for item in candidates if abs(item[1] - seed[1]) <= 18),
            key=lambda item: item[2],
        )
        run: list[int] = []
        previous_bottom: float | None = None
        for index, _, top, bottom in same_column:
            if previous_bottom is not None and top - previous_bottom > 40:
                if len(run) >= 3:
                    runs.append(tuple(run))
                run = []
            run.append(index)
            previous_bottom = bottom
        if len(run) >= 3:
            runs.append(tuple(run))
    return tuple(dict.fromkeys(runs))


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


def apply_reviewed_ocr_page_set(
    ir: DocumentIR,
    boxes_by_physical_page: dict[int, list[dict[str, object]]],
    *,
    dpi: int,
    logical_pages: dict[int, str | None],
    authority_geometry: dict[int, tuple[float, float, int]] | None = None,
) -> DocumentIR:
    """Adapt a reviewed multi-page OCR source without losing page identity.

    This permits one Evidence to cite spans on both sides of an actual page
    break. Each page's OCR boxes and geometry are checked independently.
    """

    validate_document_ir(ir)
    if ir.tables or ir.table_cells or ir.manual_corrections:
        raise ValueError("reviewed OCR page-set adapter requires an unenhanced Document IR")
    physical_pages = {page.display_page_number for page in ir.pages}
    if len(ir.pages) < 2 or set(boxes_by_physical_page) != physical_pages or set(logical_pages) != physical_pages:
        raise ValueError("reviewed OCR page set must cover every parsed page exactly once")
    if authority_geometry is not None and set(authority_geometry) != physical_pages:
        raise ValueError("authority geometry must cover every parsed page")

    adapted_parts: list[DocumentIR] = []
    for page in ir.pages:
        page_blocks = tuple(block for block in ir.blocks if block.page_id == page.page_id)
        page_block_ids = {block.block_version_id for block in page_blocks}
        page_ir = replace(
            ir,
            pages=(page,),
            blocks=page_blocks,
            source_spans=tuple(span for span in ir.source_spans if span.page_id == page.page_id),
            figures=tuple(figure for figure in ir.figures if figure.block_version_id in page_block_ids),
        )
        geometry = (authority_geometry or {}).get(page.display_page_number)
        adapted_parts.append(apply_reviewed_ocr_boxes(
            page_ir,
            boxes_by_physical_page[page.display_page_number],
            dpi=dpi,
            logical_page=logical_pages[page.display_page_number],
            authority_width_pt=geometry[0] if geometry else None,
            authority_height_pt=geometry[1] if geometry else None,
            authority_rotation_deg=geometry[2] if geometry else None,
        ))

    pages = tuple(part.pages[0] for part in adapted_parts)
    blocks = tuple(block for part in adapted_parts for block in part.blocks)
    spans = tuple(span for part in adapted_parts for span in part.source_spans)
    figures = tuple(figure for part in adapted_parts for figure in part.figures)
    payload = {
        "pages": [record_value(value) for value in pages],
        "blocks": [record_value(value) for value in blocks],
        "source_spans": [record_value(value) for value in spans],
        "figures": [record_value(value) for value in figures],
    }
    output_fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    merged = replace(
        ir,
        pages=pages,
        blocks=blocks,
        source_spans=spans,
        figures=figures,
        parsing_run=replace(
            ir.parsing_run,
            parser_version=ir.parsing_run.parser_version + "+reviewed-rapidocr-pages-v1",
            config_fingerprint=ir.parsing_run.config_fingerprint + "|reviewed-multipage-ocr-v1",
            output_fingerprint=output_fingerprint,
        ),
    )
    return validate_document_ir(merged)

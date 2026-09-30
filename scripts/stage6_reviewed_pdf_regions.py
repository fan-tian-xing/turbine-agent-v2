"""Bind reviewed region transcriptions to the current PDF's displayed text layer."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re

import pymupdf

from turbine_kg.documents.ids import block_version_id, source_span_id
from turbine_kg.documents.models import BBox, BlockVersion, SourceSpan, record_value
from turbine_kg.documents.validation import validate_document_ir


def region_text(page, displayed_bbox) -> str:
    """Select character centers in display PDF points, including rotated crops."""
    region = pymupdf.Rect(displayed_bbox)
    pieces = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            chars = []
            for span in line["spans"]:
                for char in span["chars"]:
                    box = pymupdf.Rect(char["bbox"]) * page.rotation_matrix
                    if region.contains((box.tl + box.br) / 2):
                        chars.append(char["c"])
            if chars:
                pieces.append("".join(chars))
    return "\n".join(pieces)


def verify_region_text(page, bbox, expected: str, label: str) -> str:
    actual = region_text(page, bbox)
    if re.sub(r"\s+", "", actual) != re.sub(r"\s+", "", expected):
        raise ValueError(f"current reviewed PDF text/region differs; source review required: {label}")
    return actual


def append_pdf_regions(ir, pdf, fragments: list[dict]):
    """Append current extracted regions; old OCR coordinates only locate reviewed regions."""
    blocks, spans, links = list(ir.blocks), list(ir.source_spans), {}
    for fragment in fragments:
        number = fragment["physical_page"]
        page = next(page for page in ir.pages if page.display_page_number == number)
        ordinal = max((block.block_ordinal for block in blocks if block.page_id == page.page_id), default=-1) + 1
        order = max((block.reading_order for block in blocks if block.page_id == page.page_id), default=-1) + 1
        for box in fragment["source_boxes"]:
            # Existing review manifests declare either display PDF points or
            # display pixels at 170 DPI. Never mix that locator with PDF points.
            bbox = box.get("bbox_pdf_pt")
            if bbox is None:
                bbox = [float(value) * 72 / 170 for value in box["bbox_ocr_px"]]
            text = verify_region_text(pdf[number - 1], bbox, box["text"], box["box_id"])
            bid = block_version_id(ir.parsing_run.parsing_run_id, page.page_id, ordinal)
            origin = "ocr_text" if page.text_layer_status == "ocr" else "native_text"
            blocks.append(BlockVersion(bid, page.page_id, ir.parsing_run.parsing_run_id, ordinal,
                                       "paragraph", text, BBox(*bbox), order, text_origin=origin))
            sid = source_span_id(bid, 0, text)
            spans.append(SourceSpan(sid, page.page_id, (bid,), text, "paragraph", 0, len(text), BBox(*bbox), origin))
            if box["box_id"] in links:
                raise ValueError("duplicate reviewed cross-page source box ID")
            links[box["box_id"]] = sid
            ordinal += 1
            order += 1
    material = {"blocks": [record_value(value) for value in blocks],
                "source_spans": [record_value(value) for value in spans],
                "parent_output_fingerprint": ir.parsing_run.output_fingerprint}
    fingerprint = hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
    adapted = replace(ir, blocks=tuple(blocks), source_spans=tuple(spans),
                      parsing_run=replace(ir.parsing_run,
                                          parser_version=ir.parsing_run.parser_version + "+current-reviewed-regions-v1",
                                          output_fingerprint=fingerprint))
    return validate_document_ir(adapted), links

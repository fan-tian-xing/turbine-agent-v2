"""Bind reviewed drawing rectangles and printed captions as visual-only Evidence.

The caption is the only Evidence text.  The drawing remains a Figure with an
original-page rectangle; no geometry or value is inferred from its pixels.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re

import pymupdf

from turbine_kg.documents.ids import block_version_id, figure_id, source_span_id, stable_id
from turbine_kg.documents.models import BBox, BlockVersion, Figure, SourceSpan, record_value
from turbine_kg.documents.page_identity import apply_page_identity
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.documents.validation import validate_document_ir
from turbine_kg.evidence import build_evidence
from turbine_kg.evidence.models import FigureContext
from turbine_kg.settings import Settings

from scripts.stage6_reviewed_pdf_regions import region_text, verify_region_text


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data/stage6/stage6_visual_regions.json"
REVIEWER = "Codex_original_pdf_page_review"
REVIEWED_AT = "2026-09-29T00:00:00+08:00"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _bbox(box: list[float], page, label: str) -> BBox:
    if (len(box) != 4 or not all(isinstance(value, (int, float)) for value in box)
            or not 0 <= box[0] < box[2] <= page.rect.width
            or not 0 <= box[1] < box[3] <= page.rect.height):
        raise ValueError(f"reviewed visual rectangle is outside its source page: {label}")
    return BBox(*(float(value) for value in box))


def regions_for_page(manifest: dict, document_key: str, physical_page: int) -> list[dict]:
    return [row for row in manifest["regions"]
            if (row["document_key"], row["physical_page"]) == (document_key, physical_page)]


def load_regions(path: Path = MANIFEST_PATH, *, settings: Settings | None = None) -> dict:
    """Reject a changed PDF, caption, page, or drawing rectangle before use."""

    manifest = json.loads(path.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != 1
            or manifest.get("artifact_kind") != "stage6_reviewed_visual_regions"):
        raise ValueError("unsupported Stage 6 visual-region manifest")
    settings = Settings.from_environment() if settings is None else settings
    documents = manifest["documents"]
    if set(documents) != {"DL5190.3", "D300N", "auxiliary_installation_book"}:
        raise ValueError("visual-region document scope changed")
    if len(manifest["regions"]) != 8 or len({r["region_id"] for r in manifest["regions"]}) != 8:
        raise ValueError("visual-region ID coverage changed")
    expected_pages = {("DL5190.3", 25), ("DL5190.3", 86),
                      ("DL5190.3", 113), ("D300N", 38),
                      ("auxiliary_installation_book", 300)}
    if {(r["document_key"], r["physical_page"]) for r in manifest["regions"]} != expected_pages:
        raise ValueError("visual-region sample-page coverage changed")
    by_document: dict[str, list[dict]] = {key: [] for key in documents}
    for region in manifest["regions"]:
        if region["document_key"] not in by_document:
            raise ValueError("unknown visual-region document")
        by_document[region["document_key"]].append(region)

    for key, record in documents.items():
        original = settings.source_root / record["original_relative_path"]
        processed = settings.ocr_derived_root / record["processing_relative_path"]
        if _sha(original) != record["original_sha256"] or _sha(processed) != record["processing_sha256"]:
            raise ValueError(f"visual-region PDF fingerprint changed: {key}")
        with pymupdf.open(original) as authority, pymupdf.open(processed) as processing:
            for region in by_document[key]:
                number = region["physical_page"]
                caption = region["caption"]
                caption_number = caption["physical_page"]
                if (not 1 <= number <= len(authority)
                        or caption_number not in {number, number + 1}
                        or caption_number > len(authority)
                        or not region["figure_label"] in caption["text"]):
                    raise ValueError(f"invalid visual figure/caption page: {region['region_id']}")
                figure_page, caption_page = authority[number - 1], authority[caption_number - 1]
                current_figure, current_caption = processing[number - 1], processing[caption_number - 1]
                for old, current in ((figure_page, current_figure), (caption_page, current_caption)):
                    if (old.mediabox != current.mediabox or old.cropbox != current.cropbox
                            or old.rotation != current.rotation):
                        raise ValueError(f"visual-region original/processing geometry changed: {region['region_id']}")
                _bbox(region["figure_bbox_pdf_pt"], figure_page, region["region_id"])
                _bbox(caption["bbox_pdf_pt"], caption_page, region["region_id"] + " caption")
                verify_region_text(current_caption, caption["bbox_pdf_pt"], caption["text"], region["region_id"])
                if key == "DL5190.3":
                    verify_region_text(caption_page, caption["bbox_pdf_pt"], caption["text"],
                                       region["region_id"] + " original")
                if "figure_text_anchor" in region:
                    anchor = region["figure_text_anchor"]
                    _bbox(anchor["bbox_pdf_pt"], figure_page, region["region_id"] + " anchor")
                    if _compact(anchor["text"]) not in _compact(region_text(current_figure, anchor["bbox_pdf_pt"])):
                        raise ValueError(f"visible figure text changed: {region['region_id']}")
    return manifest


def build_visual_evidence(region: dict, *, catalog, documents: dict,
                          settings: Settings | None = None):
    """Return (visual-only Evidence, IR, rectangle metadata) for one reviewed figure."""

    settings = Settings.from_environment() if settings is None else settings
    definition = documents[region["document_key"]]
    figure_page_number = region["physical_page"]
    caption = region["caption"]
    caption_page_number = caption["physical_page"]
    physical_pages = tuple(dict.fromkeys((figure_page_number, caption_page_number)))
    original_path = settings.source_root / definition["original"]
    processing_path = (settings.ocr_derived_root / definition["processing"]
                       if definition["processing"] is not None else original_path)
    with pymupdf.open(original_path) as original, pymupdf.open(processing_path) as processed:
        for number in physical_pages:
            authority, current = original[number - 1], processed[number - 1]
            if (authority.mediabox != current.mediabox or authority.cropbox != current.cropbox
                    or authority.rotation != current.rotation):
                raise ValueError("visual Evidence source page differs from original geometry")
        verify_region_text(processed[caption_page_number - 1], caption["bbox_pdf_pt"],
                           caption["text"], region["region_id"])

    ir = parse_registered_pdf(
        processing_path, definition["registered"], catalog, title=definition["title"],
        page_indices=tuple(number - 1 for number in physical_pages),
        parser_version="stage6-visual-caption-current-pdf-v1",
        parsing_run_id=stable_id("run", "stage6-visual-caption-current-pdf-v1",
                                 definition["registered"], region["region_id"]),
    )
    ir = apply_page_identity(ir, {
        figure_page_number: region["logical_page"], caption_page_number: caption["logical_page"],
    })
    pages = {page.display_page_number: page for page in ir.pages}
    blocks, spans, figures = list(ir.blocks), list(ir.source_spans), list(ir.figures)

    def next_block(number: int, kind: str, text: str, box: list[float], origin: str) -> BlockVersion:
        page = pages[number]
        ordinal = max((block.block_ordinal for block in blocks if block.page_id == page.page_id), default=-1) + 1
        order = max((block.reading_order for block in blocks if block.page_id == page.page_id), default=-1) + 1
        block = BlockVersion(
            block_version_id(ir.parsing_run.parsing_run_id, page.page_id, ordinal),
            page.page_id, ir.parsing_run.parsing_run_id, ordinal, kind, text,
            BBox(*(float(value) for value in box)), order, text_origin=origin,
        )
        blocks.append(block)
        return block

    image_origin = "ocr_text" if region["document_key"] in {"D300N", "auxiliary_installation_book"} else "native_text"
    image_block = next_block(figure_page_number, "image", "",
                             region["figure_bbox_pdf_pt"], image_origin)
    caption_block = next_block(caption_page_number, "caption", caption["text"],
                               caption["bbox_pdf_pt"], image_origin)
    caption_sid = source_span_id(caption_block.block_version_id, 0, caption["text"])
    spans.append(SourceSpan(
        caption_sid, pages[caption_page_number].page_id,
        (caption_block.block_version_id,), caption["text"], "caption", 0, len(caption["text"]),
        BBox(*(float(value) for value in caption["bbox_pdf_pt"])), image_origin,
    ))
    figure = Figure(figure_id(image_block.block_version_id), image_block.block_version_id,
                    caption_block.block_version_id, region["figure_label"])
    figures.append(figure)
    payload = {
        "pages": [record_value(value) for value in ir.pages],
        "blocks": [record_value(value) for value in blocks],
        "tables": [record_value(value) for value in ir.tables],
        "table_cells": [record_value(value) for value in ir.table_cells],
        "figures": [record_value(value) for value in figures],
        "source_spans": [record_value(value) for value in spans],
        "manual_corrections": [record_value(value) for value in ir.manual_corrections],
    }
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
    adapted = replace(ir, blocks=tuple(blocks), source_spans=tuple(spans), figures=tuple(figures),
                      parsing_run=replace(ir.parsing_run, output_fingerprint=fingerprint))
    adapted = validate_document_ir(adapted)
    same_page = figure_page_number == caption_page_number
    item = build_evidence(
        adapted, (caption_sid,), evidence_role=definition["role"], disposition="visual_only",
        review_status="accepted", reviewer=REVIEWER, reviewed_at=REVIEWED_AT,
        review_reason="Original PDF drawing rectangle and printed caption visually checked; "
                      "drawing geometry, numeric values and spatial relationships are not asserted.",
        figure_context=(FigureContext(figure.figure_id, "whole_figure",
                                      (caption_sid,) if same_page else ()),),
    )
    metadata = {
        "source_supplement_kind": "reviewed_visual_only",
        "visual_region_id": region["region_id"],
        "figure_physical_page": figure_page_number,
        "figure_bbox_pdf_pt": region["figure_bbox_pdf_pt"],
        "caption_physical_page": caption_page_number,
        "cross_page_caption": not same_page,
        "semantic_use": "visual_context_only",
        "stage12_extractability": "context_only",
        "extractability_reason": "Only the printed figure caption is transcribed; drawing geometry and numeric relationships are unverified.",
    }
    return item, adapted, metadata

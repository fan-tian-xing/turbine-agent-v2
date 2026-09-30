"""Reviewed Stage 6 page boundaries must remain anchored to the original PDFs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf

from scripts.stage6_page_evidence_boundaries import body_spans_for_sample
from scripts.stage6_reviewed_pdf_regions import region_text
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[2]
OVERRIDES = ROOT / "data/stage6/stage6_source_review_overrides.json"
VISUAL = ROOT / "data/stage6/stage6_visual_regions.json"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_reviewed_segment_boxes_match_original() -> None:
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    documents = json.loads(VISUAL.read_text(encoding="utf-8"))["documents"]
    settings = Settings.from_environment()
    for rule in overrides["reviewed_span_splits"]:
        key, number = rule["document_key"], rule["physical_page"]
        original = settings.source_root / documents[key]["original_relative_path"]
        processing = settings.ocr_derived_root / documents[key]["processing_relative_path"]
        with pymupdf.open(original) as source, pymupdf.open(processing) as current:
            for segment in rule["segments"]:
                actual = region_text(current[number - 1], segment["bbox_pdf_pt"])
                authority = region_text(source[number - 1], segment["bbox_pdf_pt"])
                assert actual == authority
                assert _sha(actual) == segment["text_sha256"]


def test_page_bounds_exclude_navigation_without_clipping_body() -> None:
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    class Span:
        def __init__(self, y0: float, y1: float):
            self.bbox = type("Box", (), {"y0": y0, "y1": y1})()

    spans = [Span(39, 55), Span(60, 87), Span(760, 775), Span(793, 805)]
    result = body_spans_for_sample(
        {"document_key": "DL5190.3", "physical_page": 14}, spans, overrides,
    )
    assert result == spans[1:3]

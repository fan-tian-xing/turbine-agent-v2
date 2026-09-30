"""A missing parent clause must not become an independent OCR source unit."""

import copy
import json
from pathlib import Path

import numpy as np
import pymupdf
import pytest
from rapidocr_onnxruntime import RapidOCR

from turbine_kg.documents.incomplete_source import partition_incomplete_source_ocr
from turbine_kg.settings import PROJECT_ROOT, Settings


MANIFEST_PATH = PROJECT_ROOT / "data/stage6/stage6_incomplete_source_exclusions.json"


@pytest.fixture(scope="module")
def reviewed_pages():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    pdf_path = Settings.from_environment().source_root / manifest["source_pdf"]["relative_path"]
    dpi = manifest["ocr_dpi"]
    engine = RapidOCR()
    boxes_by_page = {}
    with pymupdf.open(pdf_path) as pdf:
        for physical_page in (17, 18, 19):
            pixmap = pdf[physical_page - 1].get_pixmap(
                matrix=pymupdf.Matrix(dpi / 72.0, dpi / 72.0), alpha=False
            )
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
            raw, _ = engine(image)
            boxes = []
            for item in raw or []:
                points, text = item[0], str(item[1])
                xs = [float(point[0]) for point in points]
                ys = [float(point[1]) for point in points]
                boxes.append({
                    "text": text,
                    "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys),
                })
            boxes_by_page[physical_page] = boxes
    return manifest, pdf_path, boxes_by_page


def _partition(reviewed_pages, physical_page, *, boxes=None, manifest=None, dpi=None):
    source_manifest, pdf_path, by_page = reviewed_pages
    return partition_incomplete_source_ocr(
        by_page[physical_page] if boxes is None else boxes,
        document_key="DLT863",
        physical_page=physical_page,
        original_pdf_path=pdf_path,
        dpi=source_manifest["ocr_dpi"] if dpi is None else dpi,
        manifest=source_manifest if manifest is None else manifest,
    )


@pytest.mark.parametrize("page,excluded_count,anchor", [
    (17, 6, "6.7.1.7"),
    (18, 34, "6.10汽轮机停机"),
    (19, 24, "7.3.2轴瓦烧损预防措施"),
])
def test_missing_parent_fragments_are_excluded_and_next_clause_survives(
    reviewed_pages, page, excluded_count, anchor
):
    partition = _partition(reviewed_pages, page)
    assert len(partition.excluded_boxes) == excluded_count
    assert len(partition.region_ids) == 1
    assert partition.region_ids[0] == f"DLT863-p{page}-missing-parent"
    assert any(str(box["text"]).startswith(anchor) for box in partition.retained_boxes)
    assert len(partition.retained_boxes) + len(partition.excluded_boxes) == len(reviewed_pages[2][page])


def test_ocr_drift_refuses_to_release_fragment(reviewed_pages):
    boxes = copy.deepcopy(reviewed_pages[2][18])
    boxes[2]["text"] += "误"
    with pytest.raises(ValueError, match="OCR text SHA mismatch"):
        _partition(reviewed_pages, 18, boxes=boxes)


def test_missing_following_clause_anchor_refuses_partition(reviewed_pages):
    boxes = copy.deepcopy(reviewed_pages[2][19])
    anchor = next(box for box in boxes if str(box["text"]).startswith("7.3.2"))
    anchor["text"] = "7.3.2"
    with pytest.raises(ValueError, match="following clause anchor mismatch"):
        _partition(reviewed_pages, 19, boxes=boxes)


def test_original_pdf_and_ocr_dpi_are_pinned(reviewed_pages):
    manifest = copy.deepcopy(reviewed_pages[0])
    manifest["source_pdf"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="original PDF SHA mismatch"):
        _partition(reviewed_pages, 17, manifest=manifest)
    with pytest.raises(ValueError, match="OCR DPI mismatch"):
        _partition(reviewed_pages, 17, dpi=160)


def test_unlisted_page_passes_through_without_mutation(reviewed_pages):
    boxes = reviewed_pages[2][18]
    partition = partition_incomplete_source_ocr(
        boxes,
        document_key="DLT863",
        physical_page=20,
        original_pdf_path=reviewed_pages[1],
        dpi=reviewed_pages[0]["ocr_dpi"],
        manifest=reviewed_pages[0],
    )
    assert partition.retained_boxes == tuple(boxes)
    assert partition.excluded_boxes == ()

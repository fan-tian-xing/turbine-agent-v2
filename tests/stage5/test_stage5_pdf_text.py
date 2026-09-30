import cv2
import numpy as np
import pymupdf

from generate_ocr_pdf import (
    Block,
    _insert_positioned_text,
    _inset_region,
    _is_ruled_table,
    _ordered_boxes,
    default_font_file,
)


def test_positioned_text_falls_back_for_symbols_missing_from_cjk_font():
    document = pymupdf.open()
    page = document.new_page(width=300, height=120)
    boxes = [{
        "text": "mg/m³ SO₂ NOₓ ℃ 中文",
        "bbox_pt": {"x0": 10, "y0": 20, "x1": 280, "y1": 50},
    }]

    failures = _insert_positioned_text(page, boxes, default_font_file())
    extracted = page.get_text()

    assert failures == 0
    assert extracted.strip() == boxes[0]["text"]
    assert "\x00" not in extracted
    assert "\ufffd" not in extracted


def test_positioned_text_preserves_recognized_symbols_and_identifiers_verbatim():
    document = pymupdf.open()
    page = document.new_page(width=500, height=120)
    text = "ICS 27.100 ISO11171 SAEAS4059F SO2 NOx 土5% 30°℃"
    boxes = [{
        "text": text,
        "bbox_pt": {"x0": 10, "y0": 20, "x1": 490, "y1": 50},
    }]

    failures = _insert_positioned_text(page, boxes, default_font_file())

    assert failures == 0
    assert page.get_text().strip() == text


def test_table_router_selects_wired_model_only_when_rules_cross_both_axes():
    ruled = np.full((400, 800, 3), 255, dtype=np.uint8)
    for y in (20, 120, 220, 320, 380):
        cv2.line(ruled, (20, y), (780, y), (0, 0, 0), 2)
    for x in (20, 210, 400, 590, 780):
        cv2.line(ruled, (x, 20), (x, 380), (0, 0, 0), 2)

    borderless = np.full((400, 800, 3), 255, dtype=np.uint8)
    for y in (60, 100, 140, 180, 220, 260, 300, 340):
        cv2.line(borderless, (40, y), (150, y), (0, 0, 0), 2)

    assert _is_ruled_table(ruled)
    assert not _is_ruled_table(borderless)


def test_cell_crop_guard_returns_none_for_collapsed_inset_and_clips_valid_region():
    image = np.zeros((80, 120, 3), dtype=np.uint8)

    assert _inset_region(image, (10, 10, 13, 13), 0, 0) is None
    cropped = _inset_region(image, (10, 10, 70, 50), 0, 0)
    assert cropped is not None
    crop, left, top = cropped
    assert crop.shape[:2] == (32, 54)
    assert (left, top) == (13, 14)


def test_text_box_order_uses_page_geometry_instead_of_layout_model_order():
    def box(text, x, y, source_index):
        return {
            "text": text,
            "score": 0.99,
            "source_index": source_index,
            "bbox_px": {"x0": x, "y0": y, "x1": x + 30, "y1": y + 12},
        }

    blocks = [
        Block("text", (0, 40, 200, 60), 0.9, 0, "layout", [box("第三行", 10, 40, 2)]),
        Block("text", (0, 20, 200, 40), 0.9, 1, "layout", [box("第二行", 10, 20, 1)]),
        Block("text", (0, 0, 200, 20), 0.9, 2, "layout", [box("第一行", 10, 0, 0)]),
    ]

    assert [item["text"] for item in _ordered_boxes(blocks)] == ["第一行", "第二行", "第三行"]

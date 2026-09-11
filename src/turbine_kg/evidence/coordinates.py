"""Coordinate conversion for OCR regions used by Stage 6 candidates."""

from __future__ import annotations

from turbine_kg.documents.models import BBox


def pixel_bbox_to_pdf_bbox(
    bbox: dict[str, float],
    *,
    dpi: int,
    page_width_pt: float,
    page_height_pt: float,
    rotation_deg: int = 0,
) -> BBox:
    """Convert a RapidOCR pixel bbox to canonical top-left PDF points.

    Stage 6 currently accepts only unrotated pages for this adapter.  A
    rotated page must first receive an explicit rotation mapping rather than
    silently using an incorrect bbox.
    """

    if rotation_deg not in {0, 360}:
        raise ValueError("rotated OCR pages require an explicit coordinate mapping")
    if dpi <= 0:
        raise ValueError("dpi must be positive")
    scale = 72.0 / float(dpi)
    converted = BBox(
        float(bbox["x0"]) * scale,
        float(bbox["y0"]) * scale,
        float(bbox["x1"]) * scale,
        float(bbox["y1"]) * scale,
    )
    if converted.x0 < 0 or converted.y0 < 0 or converted.x1 > page_width_pt or converted.y1 > page_height_pt:
        raise ValueError("converted OCR bbox falls outside the PDF page")
    return converted


def union_pixel_boxes(boxes: list[dict[str, float]]) -> dict[str, float]:
    """Return a deterministic enclosing bbox for already reviewed OCR boxes."""

    if not boxes:
        raise ValueError("at least one OCR box is required")
    return {
        "x0": min(float(box["x0"]) for box in boxes),
        "y0": min(float(box["y0"]) for box in boxes),
        "x1": max(float(box["x1"]) for box in boxes),
        "y1": max(float(box["y1"]) for box in boxes),
    }

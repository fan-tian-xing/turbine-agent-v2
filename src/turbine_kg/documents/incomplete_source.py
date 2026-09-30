"""Fail closed when reviewed OCR contains a fragment whose parent source is absent.

This partitions OCR boxes only. It does not construct, approve, or alter Evidence.
Each exclusion must match the original PDF, rendered page, exact OCR text, and
the following self-contained clause before any box is removed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Mapping

import pymupdf


@dataclass(frozen=True)
class IncompleteSourcePartition:
    retained_boxes: tuple[dict, ...]
    excluded_boxes: tuple[dict, ...]
    region_ids: tuple[str, ...]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bbox_pdf(box: Mapping[str, object], dpi: int) -> tuple[float, float, float, float]:
    scale = 72.0 / dpi
    return tuple(float(box[key]) * scale for key in ("x0", "y0", "x1", "y1"))


def _within(box: Mapping[str, object], bounds: list[float], dpi: int) -> bool:
    x0, y0, x1, y1 = _bbox_pdf(box, dpi)
    left, top, right, bottom = bounds
    return x0 >= left and y0 >= top and x1 <= right and y1 <= bottom


def partition_incomplete_source_ocr(
    boxes: list[dict],
    *,
    document_key: str,
    physical_page: int,
    original_pdf_path: Path,
    dpi: int,
    manifest: Mapping[str, object],
) -> IncompleteSourcePartition:
    """Return only boxes outside verified incomplete-source regions.

    A mismatch raises ValueError, leaving callers without a usable box list.
    Pages absent from this manifest pass through unchanged. The input list is
    never mutated.
    """

    if manifest.get("schema_version") != 1 or manifest.get("artifact_kind") != "stage6_incomplete_source_exclusions":
        raise ValueError("unsupported incomplete-source manifest")
    if document_key != manifest.get("document_key"):
        return IncompleteSourcePartition(tuple(boxes), (), ())
    regions = [row for row in manifest["regions"] if row["physical_page"] == physical_page]
    if not regions:
        return IncompleteSourcePartition(tuple(boxes), (), ())
    if dpi != manifest["ocr_dpi"]:
        raise ValueError("incomplete-source OCR DPI mismatch")
    pdf_path = Path(original_pdf_path)
    if _sha256(pdf_path.read_bytes()) != manifest["source_pdf"]["sha256"]:
        raise ValueError("incomplete-source original PDF SHA mismatch")
    with pymupdf.open(pdf_path) as pdf:
        if physical_page < 1 or physical_page > len(pdf):
            raise ValueError("incomplete-source physical page is absent")
        pixmap = pdf[physical_page - 1].get_pixmap(
            matrix=pymupdf.Matrix(dpi / 72.0, dpi / 72.0), alpha=False
        )
        page_visual_sha256 = _sha256(pixmap.samples)

    excluded_indices: set[int] = set()
    region_ids: list[str] = []
    for region in regions:
        if region["document_key"] != document_key:
            raise ValueError("incomplete-source document key mismatch")
        if page_visual_sha256 != region["source_pdf_visual_page_sha256_170dpi"]:
            raise ValueError("incomplete-source page visual SHA mismatch")
        bounds = region["bbox_pdf_pt"]
        selected = [(index, box) for index, box in enumerate(boxes) if _within(box, bounds, dpi)]
        texts = [str(box["text"]) for _, box in selected]
        if len(selected) != region["selected_ocr_box_count"]:
            raise ValueError("incomplete-source OCR box count mismatch")
        if _sha256("\n".join(texts).encode("utf-8")) != region["selected_ocr_text_sha256"]:
            raise ValueError("incomplete-source OCR text SHA mismatch")
        if not texts or _sha256(texts[0].encode("utf-8")) != region["first_selected_text_sha256"]:
            raise ValueError("incomplete-source first OCR box mismatch")
        if _sha256(texts[-1].encode("utf-8")) != region["last_selected_text_sha256"]:
            raise ValueError("incomplete-source last OCR box mismatch")
        anchor = region["next_complete_clause_anchor"]
        anchor_matches = [
            (index, box)
            for index, box in enumerate(boxes)
            if _sha256(str(box["text"]).encode("utf-8")) == anchor["text_sha256"]
        ]
        if len(anchor_matches) != 1 or anchor_matches[0][0] in {index for index, _ in selected}:
            raise ValueError("incomplete-source following clause anchor mismatch")
        actual_bbox = _bbox_pdf(anchor_matches[0][1], dpi)
        if any(abs(actual - expected) > 0.6 for actual, expected in zip(actual_bbox, anchor["bbox_pdf_pt"])):
            raise ValueError("incomplete-source following clause location mismatch")
        indices = {index for index, _ in selected}
        if excluded_indices & indices:
            raise ValueError("overlapping incomplete-source exclusions")
        excluded_indices.update(indices)
        region_ids.append(region["region_id"])

    return IncompleteSourcePartition(
        retained_boxes=tuple(box for index, box in enumerate(boxes) if index not in excluded_indices),
        excluded_boxes=tuple(box for index, box in enumerate(boxes) if index in excluded_indices),
        region_ids=tuple(region_ids),
    )

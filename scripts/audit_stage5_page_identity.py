"""Reconcile physical PDF page numbers with printed/logical page labels."""

from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path

import numpy as np
import pymupdf

from turbine_kg.settings import PROJECT_ROOT, Settings


TODAY = date.today().isoformat()
OUTPUT = PROJECT_ROOT / "data" / "stage5" / f"stage5_page_identity_audit_{TODAY}.json"


def image_hash(document: pymupdf.Document, physical_page: int) -> str:
    pixmap = document[physical_page - 1].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    return hashlib.sha256(pixmap.samples).hexdigest()


def rendered_image_metrics(original: pymupdf.Document, processing: pymupdf.Document, physical_page: int) -> dict:
    original_pixmap = original[physical_page - 1].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    processing_pixmap = processing[physical_page - 1].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
    original_array = np.frombuffer(original_pixmap.samples, dtype=np.uint8).reshape(original_pixmap.height, original_pixmap.width, original_pixmap.n)
    processing_array = np.frombuffer(processing_pixmap.samples, dtype=np.uint8).reshape(processing_pixmap.height, processing_pixmap.width, processing_pixmap.n)
    if original_array.shape != processing_array.shape:
        return {"rendered_shape": [list(original_array.shape), list(processing_array.shape)], "mean_abs_pixel_delta": None, "exact_pixel_fraction": 0.0, "source_page_visual_match": False}
    delta = np.abs(original_array.astype(np.int16) - processing_array.astype(np.int16))
    exact_pixel_fraction = float(np.mean(np.all(original_array == processing_array, axis=2)))
    return {
        "rendered_shape": list(original_array.shape),
        "mean_abs_pixel_delta": round(float(delta.mean()), 6),
        "exact_pixel_fraction": round(exact_pixel_fraction, 6),
        "source_page_visual_match": bool(delta.mean() <= 5.0 and exact_pixel_fraction >= 0.9),
    }


def audit() -> dict:
    settings = Settings.from_environment()
    cases = [
        {
            "document_key": "D300N",
            "original_path": settings.source_root / "汽轮机说明书" / "汽轮机本体安装及维护说明书.pdf",
            "processing_path": settings.ocr_derived_root / "汽轮机本体安装及维护说明书(OCR).pdf",
            "physical_pdf_page": 94,
            "logical_page_label": "3-3-4",
            "page_role": "blank_boundary_page",
            "original_visual_assessment": "near_blank_page_with_header_document_code_and_printed_logical_label",
            "ocr_interpretation": "low_similarity_is_not_a_content_loss_signal_for_this_near_blank_boundary_page",
        },
        {
            "document_key": "auxiliary_installation_book",
            "original_path": settings.source_root / "2.书籍" / "260824 扫描文件" / "汽轮机辅机安装（第二版）.pdf",
            "processing_path": settings.ocr_derived_root / "汽轮机辅机安装（第二版）(OCR).pdf",
            "physical_pdf_page": 300,
            "logical_page_label": "291",
            "page_role": "formula_figure_text_page",
            "original_visual_assessment": "content_present_with_problem_text_formula_figure_caption_and_printed_page_number",
            "ocr_interpretation": "content_is_present; low_similarity_is_due_to_formula_figure_and_reading_order_and_requires_visual_truth",
        },
    ]
    records = []
    for case in cases:
        original = pymupdf.open(case["original_path"])
        processing = pymupdf.open(case["processing_path"])
        physical_page = case["physical_pdf_page"]
        original_hash = image_hash(original, physical_page)
        processing_hash = image_hash(processing, physical_page)
        visual_metrics = rendered_image_metrics(original, processing, physical_page)
        records.append({
            **{key: value for key, value in case.items() if key not in {"original_path", "processing_path"}},
            "page_number_semantics": "physical_pdf_page_is_one_based_page_index; logical_page_label_is_printed_or_document_internal_label",
            "original_image_sha256": original_hash,
            "processing_image_sha256": processing_hash,
            "original_and_processing_render_hashes": {"original": original_hash, "processing": processing_hash},
            **visual_metrics,
            "status": "reconciled_for_review",
        })
        original.close()
        processing.close()
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_page_identity_audit",
        "audited_at": TODAY,
        "status": "page_identity_reconciled",
        "records": records,
        "boundaries": [
            "All stage5 pdf_page fields refer to one-based physical PDF page indices unless explicitly named logical_page_label.",
            "A low text or OCR similarity result on a blank boundary page is not evidence of lost substantive content.",
            "A content-bearing formula/figure page requires visual truth before its OCR text can enter Evidence.",
        ],
    }


def main() -> int:
    result = audit()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(OUTPUT)}, ensure_ascii=False))
    return 0 if all(item["source_page_visual_match"] for item in result["records"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

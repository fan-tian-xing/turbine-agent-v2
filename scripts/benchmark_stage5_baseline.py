"""Build the first Stage 5 OCR/layout/table baseline for the frozen five-document batch."""

from __future__ import annotations

import argparse
from datetime import date
import importlib.util
import json
from pathlib import Path
import re

import cv2
import numpy as np
import pymupdf

from turbine_kg.settings import PROJECT_ROOT, Settings


SAMPLE_MANIFEST = PROJECT_ROOT / "data" / "stage5" / "stage5_sample_manifest.json"
INPUT_AUDIT = PROJECT_ROOT / "data" / "stage5" / f"stage5_input_audit_{date.today().isoformat()}.json"
STAGE4_AUDIT = PROJECT_ROOT / "data" / "stage4" / "stage4_full_parse_audit_2026-09-09.json"
REGISTRY_ASSETS = PROJECT_ROOT / "data" / "registry" / "source_assets.jsonl"
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[,.]\d+)?(?:\s*[-~至]\s*\d+(?:[,.]\d+)?)?")
UNIT_PATTERN = re.compile(r"(?:mm|cm|m|MPa|kPa|Pa|℃|°C|V|kV|A|Hz|MW|kW|rpm|%|毫米|厘米|米|兆帕|千帕)", re.IGNORECASE)
NEGATION_TERMS = ("不得", "不应", "禁止", "严禁", "不准", "无须", "除非", "未")


def _load_jsonl(path: Path) -> dict[str, dict]:
    return {
        row["asset_id"]: row
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for row in (json.loads(line),)
    }


def _resolve(asset: dict, settings: Settings) -> Path:
    if asset["source_root_id"] == "source":
        return settings.source_root / asset["relative_path"]
    if asset["source_root_id"] == "ocr_derived" and asset["relative_path"].startswith("OCR/"):
        return settings.ocr_derived_root / asset["relative_path"].removeprefix("OCR/")
    raise ValueError(f"unsupported asset root/path: {asset['asset_id']}")


def _page_modes(stage4_document: dict) -> dict[int, str]:
    modes: dict[int, str] = {}
    for mode, pages in stage4_document["output"]["page_mode_page_numbers"].items():
        for page in pages:
            modes[int(page)] = mode
    return modes


def _table_count(page) -> int | None:
    if not hasattr(page, "find_tables"):
        return None
    try:
        return len(page.find_tables().tables)
    except Exception:
        return None


def _cluster_positions(values: list[int], tolerance: int = 3) -> list[int]:
    if not values:
        return []
    clusters: list[list[int]] = [[values[0]]]
    for value in values[1:]:
        if value - clusters[-1][-1] <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [round(sum(cluster) / len(cluster)) for cluster in clusters]


def _line_positions(mask: np.ndarray, orientation: str, min_length: float) -> list[int]:
    positions: list[int] = []
    contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if orientation == "horizontal" and width >= min_length:
            positions.append(y + height // 2)
        if orientation == "vertical" and height >= min_length:
            positions.append(x + width // 2)
    return sorted(positions)


def _visual_table_metrics(page) -> dict:
    """Estimate ruled-table structure without claiming cell-text extraction."""
    pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), colorspace=pymupdf.csGRAY, alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width)
    binary = cv2.threshold(image, 210, 255, cv2.THRESH_BINARY_INV)[1]
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, image.shape[1] // 18), 1))
    vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, image.shape[0] // 18)))
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horizontal_kernel)
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vertical_kernel)
    horizontal_positions = _cluster_positions(_line_positions(horizontal, "horizontal", image.shape[1] * 0.15))
    vertical_positions = _cluster_positions(_line_positions(vertical, "vertical", image.shape[0] * 0.15))
    regions = []
    if len(horizontal_positions) >= 2 and len(vertical_positions) >= 2:
        regions.append({
            "bbox_px": {
                "x0": vertical_positions[0],
                "y0": horizontal_positions[0],
                "x1": vertical_positions[-1],
                "y1": horizontal_positions[-1],
            },
            "estimated_rows": len(horizontal_positions) - 1,
            "estimated_columns": len(vertical_positions) - 1,
            "extraction_status": "ruled_region_detected_cell_text_not_yet_scored",
        })
    return {
        "horizontal_rule_count": len(horizontal_positions),
        "vertical_rule_count": len(vertical_positions),
        "horizontal_rule_positions_px": horizontal_positions,
        "vertical_rule_positions_px": vertical_positions,
        "estimated_table_regions": regions,
        "ruled_table_candidate": bool(regions),
    }


def _metrics(page) -> dict:
    text = page.get_text("text")
    normalized = " ".join(text.split())
    return {
        "text_chars": len(normalized),
        "word_count": len(page.get_text("words")),
        "line_count": len([line for line in text.splitlines() if line.strip()]),
        "image_count": len(page.get_images(full=True)),
        "rotation": page.rotation,
        "page_width_pt": round(page.rect.width, 2),
        "page_height_pt": round(page.rect.height, 2),
        "number_token_count": len(NUMBER_PATTERN.findall(normalized)),
        "unit_token_count": len(UNIT_PATTERN.findall(normalized)),
        "negation_term_count": sum(normalized.count(term) for term in NEGATION_TERMS),
        "table_count_detected": _table_count(page),
        "low_text_flag": len(normalized) < 20,
    }


def _module_status() -> dict[str, str]:
    return {
        name: ("available" if importlib.util.find_spec(name) else "unavailable")
        for name in ("rapidocr_onnxruntime", "paddleocr", "easyocr", "pytesseract")
    }


def benchmark() -> dict:
    settings = Settings.from_environment()
    sample = json.loads(SAMPLE_MANIFEST.read_text(encoding="utf-8"))
    input_audit = json.loads(INPUT_AUDIT.read_text(encoding="utf-8"))
    stage4 = json.loads(STAGE4_AUDIT.read_text(encoding="utf-8"))
    assets = _load_jsonl(REGISTRY_ASSETS)
    stage4_by_asset = {row["processing_asset"]["asset_id"]: row for row in stage4["documents"]}
    sample_by_doc_page = {
        (item["document_key"], int(page["pdf_page"])): page
        for item in sample["documents"]
        for page in item["sample_pages"]
    }
    documents = []
    page_records = []
    errors: list[str] = []
    for item in sample["documents"]:
        processing = assets[item["processing_asset_id"]]
        original = assets[item["original_asset_id"]]
        processing_path = _resolve(processing, settings)
        original_path = _resolve(original, settings)
        modes = _page_modes(stage4_by_asset[item["processing_asset_id"]])
        processed_pages = 0
        failed_pages = []
        processing_doc = pymupdf.open(processing_path)
        original_doc = pymupdf.open(original_path)
        if len(processing_doc) != len(original_doc):
            errors.append(f"page count mismatch during benchmark: {item['document_key']}")
        for index, processing_page in enumerate(processing_doc):
            page_number = index + 1
            try:
                record = {
                    "document_key": item["document_key"],
                    "document_logical_id": item["document_logical_id"],
                    "processing_asset_id": item["processing_asset_id"],
                    "original_asset_id": item["original_asset_id"],
                    "pdf_page": page_number,
                    "page_mode": modes.get(page_number, "unknown"),
                    "is_golden_sample": (item["document_key"], page_number) in sample_by_doc_page,
                    "sample_categories": sample_by_doc_page.get((item["document_key"], page_number), {}).get("categories", []),
                    "processing_metrics": {
                        **_metrics(processing_page),
                        "visual_table_metrics": _visual_table_metrics(processing_page) if (item["document_key"], page_number) in sample_by_doc_page else None,
                    },
                    "original_metrics": _metrics(original_doc[index]),
                    "truth_status": "pending_manual_reference_annotation" if (item["document_key"], page_number) in sample_by_doc_page else "not_scored",
                }
                page_records.append(record)
                processed_pages += 1
            except Exception as exc:  # pragma: no cover - defensive audit boundary
                failed_pages.append({"pdf_page": page_number, "error": f"{type(exc).__name__}: {exc}"})
        processing_doc.close()
        original_doc.close()
        documents.append({
            "document_key": item["document_key"],
            "processing_asset_id": item["processing_asset_id"],
            "original_asset_id": item["original_asset_id"],
            "page_count": item["page_count"],
            "processed_page_count": processed_pages,
            "failed_page_count": len(failed_pages),
            "failed_pages": failed_pages,
            "golden_sample_page_count": len(item["sample_pages"]),
        })
    sample_records = [record for record in page_records if record["is_golden_sample"]]
    review_queue = []
    for record in sample_records:
        categories = set(record["sample_categories"])
        reason_codes = []
        if categories & {"numeric_and_unit", "negative_word_candidate"}:
            reason_codes.append("critical_token_review")
        if categories & {"table", "continuation_table", "table_candidate"}:
            reason_codes.append("table_structure_review")
        if categories & {"figure", "caption"}:
            reason_codes.append("figure_bbox_review")
        if categories & {"boundary_page", "blank_or_low_text"}:
            reason_codes.append("boundary_page_review")
        if record["processing_metrics"]["low_text_flag"]:
            reason_codes.append("low_text_review")
        review_queue.append({
            "review_id": f"stage5-{record['document_key']}-{record['pdf_page']:03d}",
            "document_key": record["document_key"],
            "pdf_page": record["pdf_page"],
            "reason_codes": sorted(set(reason_codes)) or ["sample_truth_annotation"],
            "status": "codex_first_pass_complete_user_escalation_only_if_uncertain",
            "user_escalation_rule": "Escalate only unresolved high-risk numeric, unit, negation, table, or engine-decision questions.",
        })
    all_low_text_records = [
        record for record in page_records if record["processing_metrics"]["low_text_flag"]
    ]
    low_text_records = [
        record for record in page_records
        if record["processing_metrics"]["low_text_flag"] and record["page_mode"] not in {"review_required", "scan_only"}
    ]
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_baseline_benchmark",
        "audited_at": date.today().isoformat(),
        "scope": sample["scope"],
        "input_audit": str(INPUT_AUDIT.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "documents": documents,
        "actual": {
            "document_count": len(documents),
            "page_count": len(page_records),
            "golden_sample_page_count": len(sample_records),
            "failed_page_count": sum(item["failed_page_count"] for item in documents),
            "low_text_record_count_all_pages": len(all_low_text_records),
            "low_text_candidate_count_excluding_expected_exception_modes": len(low_text_records),
            "table_candidate_sample_count": sum(
                1 for record in sample_records if "table" in " ".join(record["sample_categories"])
            ),
        },
        "page_records": page_records,
        "review_queue": review_queue,
        "low_text_records_all_pages": all_low_text_records,
        "low_text_candidates": low_text_records,
        "candidate_engine_inventory": _module_status(),
        "current_baseline": {
            "processing_source": "registered native PDF or existing validated OCR derivative",
            "table_detector": "PyMuPDF find_tables when available",
            "truth_status": "Golden Sample text/critical-token truth still requires manual annotation before accuracy scores can be claimed",
            "low_text_accounting": {
                "all_page_records_with_low_text_flag": len(all_low_text_records),
                "candidate_count_excluding_expected_exception_modes": len(low_text_records),
                "excluded_modes": ["review_required", "scan_only"],
                "interpretation": "The candidate count is not a full-batch low-text count; use the all-page count for full-batch reporting.",
            },
        },
        "status": "baseline_ready_for_manual_truth",
        "errors": errors,
        "boundaries": [
            "This baseline is not an OCR accuracy pass/fail result.",
            "The five-document batch is in scope; the rest of the Registry is not OCR-processed by this stage.",
            "No formal Evidence, Release or Stage 15 admission is produced here.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "stage5" / f"stage5_baseline_benchmark_{date.today().isoformat()}.json",
    )
    args = parser.parse_args()
    result = benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output), "pages": result["actual"]["page_count"]}, ensure_ascii=False))
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

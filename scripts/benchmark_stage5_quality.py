"""Score Stage 5 OCR output against truth derived from the original PDFs.

The report deliberately separates scored text regions from quarantined layout
regions.  A high similarity score can never promote an unresolved table or
figure into structured evidence.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
import difflib
import json
from pathlib import Path
import re

import fitz

from stage5_fingerprint import OCR_DPI, find_matching_artifact, load_assets, ocr_fingerprint, resolve_asset
from turbine_kg.settings import PROJECT_ROOT, Settings


STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"
MANIFEST = STAGE5_ROOT / "stage5_sample_manifest.json"
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[,.]\d+)?(?:\s*[-~至]\s*\d+(?:[,.]\d+)?)?")
UNIT_PATTERN = re.compile(
    r"(?:mm|cm|m|MPa|kPa|Pa|℃|°C|V|kV|A|Hz|MW|kW|rpm|%|毫米|厘米|米|兆帕|千帕)",
    re.IGNORECASE,
)
NEGATION_TERMS = ("不得", "不应", "禁止", "严禁", "不准", "无须", "除非", "未")


def _latest(pattern: str) -> Path:
    candidates = sorted(STAGE5_ROOT.glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"no Stage 5 artifact matches {pattern}")
    return candidates[-1]


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _lines(text: str) -> list[str]:
    return [_normalize(line) for line in text.splitlines() if _normalize(line)]


def _compact(text: str) -> str:
    return "".join(ch for ch in _normalize(text) if not ch.isspace())


def _levenshtein(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _number_tokens(text: str) -> list[str]:
    return NUMBER_PATTERN.findall(_normalize(text))


def _unit_tokens(text: str) -> list[str]:
    return [token.lower() for token in UNIT_PATTERN.findall(_normalize(text))]


def _negation_tokens(text: str) -> list[str]:
    return [term for term in NEGATION_TERMS for _ in range(_normalize(text).count(term))]


def _token_score(reference: list[str], candidate: list[str]) -> dict[str, float | int]:
    expected = Counter(reference)
    actual = Counter(candidate)
    matched = sum((expected & actual).values())
    precision = matched / len(candidate) if candidate else (1.0 if not reference else 0.0)
    recall = matched / len(reference) if reference else (1.0 if not candidate else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "reference_count": len(reference),
        "candidate_count": len(candidate),
        "matched_count": matched,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def _iou(left: dict, right: dict) -> float:
    x0, y0 = max(left["x0"], right["x0"]), max(left["y0"], right["y0"])
    x1, y1 = min(left["x1"], right["x1"]), min(left["y1"], right["y1"])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, left["x1"] - left["x0"]) * max(0.0, left["y1"] - left["y0"])
    right_area = max(0.0, right["x1"] - right["x0"]) * max(0.0, right["y1"] - right["y0"])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _native_layout_score(page, boxes: list[dict], candidate_lines: list[str], reference_lines: list[str]) -> dict:
    words = page.get_text("words")
    grouped: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    for row in words:
        grouped.setdefault((int(row[5]), int(row[6])), []).append(
            (float(row[0]), float(row[1]), float(row[2]), float(row[3]))
        )
    truth = [
        {
            "x0": min(item[0] for item in values) * 170 / 72,
            "y0": min(item[1] for item in values) * 170 / 72,
            "x1": max(item[2] for item in values) * 170 / 72,
            "y1": max(item[3] for item in values) * 170 / 72,
        }
        for _, values in sorted(grouped.items(), key=lambda item: (min(row[1] for row in item[1]), min(row[0] for row in item[1])))
    ]
    if not truth or not boxes:
        return {"status": "not_scored_no_boxes", "bbox_iou_mean": None, "reading_order_similarity": None}
    count = min(len(truth), len(boxes))
    ious = [_iou(truth[index], boxes[index]) for index in range(count)]
    order_similarity = difflib.SequenceMatcher(None, reference_lines, candidate_lines).ratio()
    return {
        "status": "scored_original_native_text_geometry",
        "truth_box_count": len(truth),
        "candidate_box_count": len(boxes),
        "matched_box_count": count,
        "bbox_iou_mean": round(sum(ious) / len(ious), 6),
        "reading_order_similarity": round(order_similarity, 6),
    }


def _summary(records: list[dict], engine_key: str) -> dict:
    scored = [row for row in records if row["text_metrics"][engine_key]["status"] == "scored"]
    token_names = ("numbers", "units", "negations")
    token_summary = {}
    for name in token_names:
        metric = [row["text_metrics"][engine_key]["critical_tokens"][name] for row in scored]
        token_summary[name] = {
            field: round(sum(float(item[field]) for item in metric) / len(metric), 6) if metric else None
            for field in ("precision", "recall", "f1")
        }
    elapsed = [row["runtime"][engine_key]["elapsed_seconds"] for row in records]
    elapsed_sorted = sorted(elapsed)
    p95_index = max(0, min(len(elapsed_sorted) - 1, int(round(0.95 * len(elapsed_sorted))) - 1))
    layout = [row["layout_metrics"][engine_key] for row in records if row["layout_metrics"][engine_key].get("bbox_iou_mean") is not None]
    return {
        "scored_text_page_count": len(scored),
        "visual_only_or_quarantined_page_count": len(records) - len(scored),
        "char_accuracy_mean": round(sum(row["text_metrics"][engine_key]["char_accuracy"] for row in scored) / len(scored), 6) if scored else None,
        "critical_tokens": token_summary,
        "native_geometry_scored_page_count": len(layout),
        "bbox_iou_mean": round(sum(row["bbox_iou_mean"] for row in layout) / len(layout), 6) if layout else None,
        "reading_order_similarity_mean": round(sum(row["reading_order_similarity"] for row in layout) / len(layout), 6) if layout else None,
        "runtime": {
            "total_seconds": round(sum(elapsed), 6),
            "mean_page_seconds": round(sum(elapsed) / len(elapsed), 6) if elapsed else None,
            "p95_page_seconds": round(elapsed_sorted[p95_index], 6) if elapsed_sorted else None,
        },
    }


def benchmark() -> dict:
    settings = Settings.from_environment()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    truth = json.loads(_latest("stage5_truth_annotations_*.json").read_text(encoding="utf-8"))
    truth_by_page = {(row["document_key"], int(row["physical_page"])): row for row in truth["records"]}
    assets = load_assets()
    ocr_input_fingerprint, _ = ocr_fingerprint()
    if truth.get("source_input_fingerprint") != ocr_input_fingerprint:
        raise ValueError(
            "truth annotations do not match the current source/input fingerprint; "
            "run scripts/build_stage5_truth_annotations.py first"
        )
    rapid_path = find_matching_artifact("stage5_rapidocr_sample_benchmark_*.json", ocr_input_fingerprint)
    if rapid_path is None:
        raise FileNotFoundError(
            "no reusable RapidOCR sample artifact matches the current input fingerprint; "
            "run scripts/benchmark_stage5_rapidocr_sample.py first"
        )
    rapid_report = json.loads(rapid_path.read_text(encoding="utf-8"))
    rapid_by_page = {(row["document_key"], int(row["physical_page"])): row for row in rapid_report["records"]}
    records: list[dict] = []
    errors: list[dict] = []

    for document in manifest["documents"]:
        original = assets[document["original_asset_id"]]
        original_doc = fitz.open(resolve_asset(original, settings))
        try:
            for sample_page in document["sample_pages"]:
                page_number = int(sample_page["physical_page"])
                key = (document["document_key"], page_number)
                truth_row = truth_by_page[key]
                rapid_row = rapid_by_page[key]
                original_page = original_doc[page_number - 1]
                cached = rapid_row["fresh_rapidocr"]
                engine_outputs = {
                    "rapidocr": {
                        "text": _normalize(cached.get("text", "")),
                        "lines": _lines(cached.get("text", "")),
                        "boxes": cached.get("boxes", []),
                        "elapsed_seconds": float(cached.get("elapsed_seconds", 0)),
                    }
                }

                row = {
                    "document_key": document["document_key"],
                    "physical_page": page_number,
                    "categories": sample_page["categories"],
                    "truth_reference_kind": truth_row["reference_kind"],
                    "truth_reference_text_sha256": truth_row["reference_text_sha256"],
                    "truth_scope": "structured_text" if truth_row["structured_text_scoring_allowed"] else "visual_only_or_quarantined",
                    "gate_disposition": truth_row["gate_disposition"],
                    "runtime": {},
                    "text_metrics": {},
                    "layout_metrics": {},
                    "table_metrics": truth_row["table_truth"],
                }
                reference = truth_row["reference_text"]
                for name, output in engine_outputs.items():
                    row["runtime"][name] = {"elapsed_seconds": output["elapsed_seconds"], "render_dpi": OCR_DPI}
                    row["layout_metrics"][name] = (
                        _native_layout_score(
                            original_page,
                            output["boxes"],
                            output["lines"],
                            truth_row.get("reference_lines", []),
                        )
                        if original_page.get_text("text").strip()
                        else {"status": "not_scored_scan_page_no_native_geometry"}
                    )
                    if truth_row["structured_text_scoring_allowed"] and reference:
                        reference_compact = _compact(reference)
                        candidate_compact = _compact(output["text"])
                        distance = _levenshtein(reference_compact, candidate_compact)
                        row["text_metrics"][name] = {
                            "status": "scored",
                            "reference_char_count": len(reference_compact),
                            "candidate_char_count": len(candidate_compact),
                            "edit_distance": distance,
                            "char_accuracy": round(max(0.0, 1.0 - distance / max(len(reference_compact), 1)), 6),
                            "critical_tokens": {
                                "numbers": _token_score(_number_tokens(reference), _number_tokens(output["text"])),
                                "units": _token_score(_unit_tokens(reference), _unit_tokens(output["text"])),
                                "negations": _token_score(_negation_tokens(reference), _negation_tokens(output["text"])),
                            },
                        }
                    else:
                        row["text_metrics"][name] = {
                            "status": "not_scored",
                            "reason": "original_page_requires_visual_or_region_level_truth",
                        }
                records.append(row)
        finally:
            original_doc.close()

    table_rows = [row for row in records if row["table_metrics"] is not None]
    quarantined_tables = [row for row in table_rows if row["table_metrics"]["cell_text_accuracy_status"] == "quarantined_not_scored"]
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_original_pdf_quality_benchmark",
        "audited_at": date.today().isoformat(),
        "authority": "Original materials original PDF; scanned-page text is scored only against independent manual transcription",
        "ocr_input_fingerprint": ocr_input_fingerprint,
        "ocr_artifact": str(rapid_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "truth_annotations": str(_latest("stage5_truth_annotations_*.json").relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "sample_page_count": len(records),
        "engines": {
            "rapidocr": _summary(records, "rapidocr"),
        },
        "table_quality": {
            "table_page_count": len(table_rows),
            "cell_accuracy_scored_page_count": 0,
            "cell_accuracy_status": "quarantined_not_scored",
            "quarantine_coverage": f"{len(quarantined_tables)}/{len(table_rows)}" if table_rows else "0/0",
        },
        "records": records,
        "errors": errors,
        "status": "complete_with_quarantine" if len(records) == manifest["sample_page_count"] and not errors and len(quarantined_tables) == len(table_rows) else "incomplete",
        "boundaries": [
            "Character and critical-token metrics are scored only where native PDF text or independent manual transcription is available and the Golden Sample disposition permits it.",
            "Native-text pages additionally score geometry and reading-order similarity against original PDF word boxes.",
            "Scanned-page text without independent transcription, scanned-page bbox truth and unresolved table cell text remain visual/region-level work; all such pages stay unscored or quarantined.",
            "Runtime is measured locally and is not a cost estimate for external services.",
        ],
    }


def main() -> int:
    output = STAGE5_ROOT / f"stage5_quality_benchmark_{date.today().isoformat()}.json"
    result = benchmark()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output), "pages": result["sample_page_count"]}, ensure_ascii=False))
    return 0 if result["status"] == "complete_with_quarantine" else 1


if __name__ == "__main__":
    raise SystemExit(main())

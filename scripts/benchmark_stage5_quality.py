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
import time

import fitz

from stage5_fingerprint import load_assets, ocr_fingerprint, resolve_asset, sha256_file
from audit_stage5_exit import _current_user_acceptance
from turbine_kg.settings import PROJECT_ROOT, Settings


STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"
MANIFEST = STAGE5_ROOT / "stage5_sample_manifest.json"
BASELINE = STAGE5_ROOT / "stage5_baseline_benchmark.json"
TRUTH = STAGE5_ROOT / "stage5_truth_annotations_2026-09-28.json"
TABLE_TRUTH = STAGE5_ROOT / "stage5_table_truth_review.json"
FULL_REVIEW = PROJECT_ROOT / "data/registry/ocr_validation_report.json"
REGISTRY = PROJECT_ROOT / "data/registry/source_assets.jsonl"
EXIT_AUDIT = STAGE5_ROOT / "stage5_exit_audit.json"
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[,.]\d+)?(?:\s*[-~至]\s*\d+(?:[,.]\d+)?)?")
UNIT_PATTERN = re.compile(
    r"(?:mm|cm|m|MPa|kPa|Pa|℃|°C|V|kV|A|Hz|MW|kW|rpm|%|毫米|厘米|米|兆帕|千帕)",
    re.IGNORECASE,
)
NEGATION_TERMS = ("不得", "不应", "禁止", "严禁", "不准", "无须", "除非", "未")


def _current_inputs(settings: Settings) -> tuple[dict, dict, dict, dict, str, dict]:
    # Keep Stage 5 quality validation independent of the paused Stage 6 builder.
    from build_stage5_truth_annotations import _table_truth
    from stage7_baseline import validate_current_baseline

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assets = load_assets()
    fingerprint, components = ocr_fingerprint()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    validate_current_baseline(baseline, manifest, assets, fingerprint, components)
    truth = json.loads(TRUTH.read_text(encoding="utf-8"))
    hashes = {"stage5_sample_manifest_sha256": sha256_file(MANIFEST),
              "source_assets_sha256": sha256_file(REGISTRY),
              "full_corpus_reviews_sha256": sha256_file(FULL_REVIEW)}
    if (truth.get("status") != "complete" or truth.get("errors") != []
            or truth.get("source_input_fingerprint") != fingerprint
            or truth.get("source_fingerprint_components") != components
            or truth.get("inputs", {}).get("stage5_table_truth_review_sha256") != sha256_file(TABLE_TRUTH)
            or any(truth.get("inputs", {}).get(field) != digest for field, digest in hashes.items())):
        raise ValueError("quality input truth is not bound to the current PDF and table truth inputs")
    reviews = json.loads(FULL_REVIEW.read_text(encoding="utf-8"))["full_corpus_reviews"]
    gate = json.loads(EXIT_AUDIT.read_text(encoding="utf-8"))
    if (gate.get("status") != "complete" or gate.get("next_stage_allowed") is not True
            or gate.get("blocking_items")):
        raise ValueError("current Stage 5 exit gate must pass before quality scoring")
    review_by_key = {row["document_key"]: row for row in reviews}
    gate_by_key = {row["document_key"]: row for row in gate["documents"]}
    documents = {row["document_key"]: row for row in manifest["documents"]}
    if (len(review_by_key) != len(reviews) or len(gate_by_key) != len(gate["documents"])
            or len(documents) != len(manifest["documents"])
            or set(review_by_key) != set(gate_by_key) or set(gate_by_key) != set(documents)):
        raise ValueError("Stage 5 review/exit document coverage differs")
    expected_pages = {(key, int(page["physical_page"])) for key, doc in documents.items()
                      for page in doc["sample_pages"]}
    actual_pages = [(row["document_key"], int(row["physical_page"])) for row in truth["records"]]
    if len(actual_pages) != len(set(actual_pages)) or set(actual_pages) != expected_pages:
        raise ValueError("Stage 5 truth sample-page coverage differs from current manifest")
    for key, doc in documents.items():
        review = review_by_key[key]
        checked = gate_by_key[key]
        original_sha = assets[doc["original_asset_id"]]["sha256"]
        processing_sha = assets[doc["processing_asset_id"]]["sha256"]
        if (review.get("original_sha256") != original_sha
                or review.get("processing_sha256") != processing_sha
                or checked.get("original_sha256") != original_sha
                or checked.get("processing_sha256") != processing_sha
                or checked.get("issues") or checked.get("pixel_mismatch_pages")
                or checked.get("unsearchable_pages")):
            raise ValueError(f"Stage 5 current PDF review/exit binding differs: {key}")
        agent_reviewed = all(review.get(flag) is True for flag in (
            "line_by_line_reviewed", "table_cells_reviewed", "reading_order_reviewed"
        )) and all(review.get(field) == 0 for field in (
            "unresolved_text_count", "unresolved_table_cell_count", "unresolved_page_mapping_count"
        ))
        user_accepted = (
            checked.get("review_basis") == "explicit_current_pdf_user_acceptance"
            and _current_user_acceptance(
                review.get("user_acceptance"), original_sha, processing_sha,
                doc["page_count"]
            )
        )
        if not (agent_reviewed or user_accepted):
            raise ValueError(f"Stage 5 current full review or exact PDF user acceptance missing: {key}")
        for row in (row for row in truth["records"] if row["document_key"] == key):
            if (row.get("original_asset_id") != doc["original_asset_id"]
                    or row.get("processing_asset_id") != doc["processing_asset_id"]
                    or row.get("original_sha256") != original_sha
                    or row.get("processing_sha256") != processing_sha):
                raise ValueError(f"Stage 5 truth sample PDF identity differs: {key}")
    _table_truth(TABLE_TRUTH, manifest=manifest, assets=assets, fingerprint=fingerprint,
                 components=components, input_hashes=hashes)
    return manifest, assets, baseline, truth, fingerprint, components


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
    truth = _pdf_line_boxes(page)
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


def _pdf_line_boxes(page) -> list[dict]:
    """Extract display PDF-point boxes from the current PDF, rotating only once."""
    boxes = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            box = (fitz.Rect(line["bbox"]) * page.rotation_matrix) & page.rect
            if not box.is_empty:
                boxes.append({"x0": box.x0, "y0": box.y0, "x1": box.x1, "y1": box.y1})
    return boxes


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
    manifest, assets, baseline, truth, ocr_input_fingerprint, components = _current_inputs(settings)
    truth_by_page = {(row["document_key"], int(row["physical_page"])): row for row in truth["records"]}
    records: list[dict] = []
    errors: list[dict] = []

    for document in manifest["documents"]:
        original = assets[document["original_asset_id"]]
        processing = assets[document["processing_asset_id"]]
        original_doc = fitz.open(resolve_asset(original, settings))
        processing_doc = fitz.open(resolve_asset(processing, settings))
        try:
            for sample_page in document["sample_pages"]:
                page_number = int(sample_page["physical_page"])
                key = (document["document_key"], page_number)
                truth_row = truth_by_page[key]
                original_page = original_doc[page_number - 1]
                processing_page = processing_doc[page_number - 1]
                if (original_page.mediabox != processing_page.mediabox
                        or original_page.cropbox != processing_page.cropbox
                        or original_page.rotation != processing_page.rotation):
                    raise ValueError("quality processing PDF geometry differs from its original")
                started = time.perf_counter()
                current_text = processing_page.get_text("text")
                current_boxes = _pdf_line_boxes(processing_page)
                engine_outputs = {
                    "reviewed_pdf": {
                        "text": _normalize(current_text),
                        "lines": _lines(current_text),
                        "boxes": current_boxes,
                        "elapsed_seconds": time.perf_counter() - started,
                    }
                }

                row = {
                    "document_key": document["document_key"],
                    "original_asset_id": original["asset_id"],
                    "processing_asset_id": processing["asset_id"],
                    "original_sha256": original["sha256"],
                    "processing_sha256": processing["sha256"],
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
                    row["runtime"][name] = {"elapsed_seconds": output["elapsed_seconds"],
                                             "operation": "current_pdf_text_and_geometry_extraction",
                                             "coordinate_units": "display_pdf_points"}
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
            processing_doc.close()

    table_rows = [row for row in records if row["table_metrics"] is not None]
    quarantined_tables = [row for row in table_rows if row["table_metrics"]["cell_text_accuracy_status"] == "quarantined_not_scored"]
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_original_pdf_quality_benchmark",
        "audited_at": date.today().isoformat(),
        "authority": "Original materials original PDF; scanned-page text is scored only against independent manual transcription",
        "ocr_input_fingerprint": ocr_input_fingerprint,
        "fingerprint_components": components,
        "baseline": str(BASELINE.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "truth_annotations": str(TRUTH.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "inputs": {"baseline_sha256": sha256_file(BASELINE), "truth_annotations_sha256": sha256_file(TRUTH),
                   "stage5_table_truth_review_sha256": sha256_file(TABLE_TRUTH)},
        "sample_page_count": len(records),
        "engines": {
            "reviewed_pdf": _summary(records, "reviewed_pdf"),
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
            "Runtime measures current PDF extraction, not the earlier OCR generation or external service cost.",
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

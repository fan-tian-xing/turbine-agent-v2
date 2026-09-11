"""Assemble the current Stage 5 gate without falsely closing it."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from turbine_kg.settings import PROJECT_ROOT


STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"
TODAY = date.today().isoformat()
FROZEN_REVIEW_DATE = "2026-09-09"


def read(name: str) -> dict:
    path = STAGE5_ROOT / name
    return json.loads(path.read_text(encoding="utf-8"))


def read_latest(pattern: str) -> tuple[Path, dict]:
    candidates = sorted(STAGE5_ROOT.glob(pattern), reverse=True)
    path = candidates[0] if candidates else None
    if path is None:
        raise FileNotFoundError(f"no Stage 5 artifact matches {pattern}")
    return path, json.loads(path.read_text(encoding="utf-8"))


def audit() -> dict:
    input_audit_path, input_audit = read_latest("stage5_input_audit_*.json")
    baseline_path, baseline = read_latest("stage5_baseline_benchmark_*.json")
    rapidocr_path, rapidocr = read_latest("stage5_rapidocr_sample_benchmark_*.json")
    engine_decision_path, engine_decision = read_latest("stage5_engine_decision_*.json")
    tables = read(f"stage5_table_baseline_{FROZEN_REVIEW_DATE}.json")
    table_truth = read(f"stage5_table_truth_review_{FROZEN_REVIEW_DATE}.json")
    page_identity = read(f"stage5_page_identity_audit_{FROZEN_REVIEW_DATE}.json")
    sample = read("stage5_sample_manifest.json")
    golden_review_path = STAGE5_ROOT / f"stage5_golden_sample_review_{FROZEN_REVIEW_DATE}.json"
    golden_review = json.loads(golden_review_path.read_text(encoding="utf-8")) if golden_review_path.exists() else None
    quality_path, quality = read_latest("stage5_quality_benchmark_*.json")
    low_similarity_scanned = [
        {
            "document_key": item["document_key"],
            "pdf_page": item["pdf_page"],
            "fresh_vs_registered_similarity": item["fresh_vs_registered_similarity"],
            "status": "codex_first_pass_complete_user_escalation_only_if_uncertain",
        }
        for item in rapidocr["records"]
        if not item["source_has_native_text"] and item["fresh_vs_registered_similarity"] < 0.97
    ]
    checks = {
        "sample_manifest_five_documents": len(sample["documents"]) == 5,
        "sample_manifest_36_pages": sample["sample_page_count"] == 36,
        "sample_manifest_physical_page_contract": all(
            int(page["physical_page"]) == int(page["pdf_page"])
            for document in sample["documents"]
            for page in document["sample_pages"]
        ),
        "input_audit_pass": input_audit["status"] == "pass",
        "full_baseline_775_pages": baseline["actual"]["page_count"] == 775,
        "full_baseline_zero_failures": baseline["actual"]["failed_page_count"] == 0,
        "rapidocr_sample_36_pages": rapidocr["actual"]["sample_page_count"] == 36,
        "rapidocr_zero_failures": rapidocr["actual"]["failed_page_count"] == 0,
        "engine_choice_recorded": (
            engine_decision["selection"]["primary_engine"] == "rapidocr_onnxruntime"
        ),
        "table_candidate_scope_corrected": (
            tables["candidate_count"] == 7
            and tables["table_candidate_count"] == 6
            and tables["complex_layout_not_table_count"] == 1
        ),
        "table_structural_truth_complete": (
            len(table_truth["records"]) == 7
            and all(
                item["visible_content_match"]
                and item["table_truth_status"] in {"codex_reviewed_structural_truth", "not_applicable"}
                and (
                    item["table_truth_status"] == "not_applicable"
                    or item.get("leaf_column_count") is not None
                )
                for item in table_truth["records"]
            )
        ),
        "table_cell_accuracy_boundary_explicit": (
            tables["table_candidate_count"] == 6
            and sum(
                item["cell_text_accuracy_status"] == "not_scored_manual_truth_required"
                for item in tables["candidates"]
            ) == 6
        ),
        "golden_sample_visual_review_complete": bool(
            golden_review
            and golden_review.get("sample_page_count") == 36
            and golden_review.get("reviewed_page_count") == 36
            and golden_review.get("status") == "codex_reviewed_for_stage5_gate"
            and golden_review.get("summary", {}).get("quarantined_structured_pages") == 6
        ),
        "page_identity_reconciled": page_identity["status"] == "page_identity_reconciled" and all(item["source_page_visual_match"] for item in page_identity["records"]),
        "original_pdf_quality_benchmark_recorded": bool(
            quality
            and quality.get("status") == "complete_with_quarantine"
            and quality.get("sample_page_count") == sample["sample_page_count"]
            and not quality.get("errors")
            and quality.get("table_quality", {}).get("quarantine_coverage") == "6/6"
        ),
    }
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_exit_audit",
        "audited_at": TODAY,
        "status": "complete_with_quarantine" if all(checks.values()) else "awaiting_golden_sample_review",
        "closure_status": "closed_with_quarantine" if all(checks.values()) else "open",
        "formal_release": False,
        "owner_confirmed_quality_policy": {
            "content_must_match_original_exactly": True,
            "critical_tokens": ["Chinese characters", "digits", "decimal points", "units", "negation terms"],
            "layout_requirements": ["table row/column and continuation relationships", "formula meaning", "figure/caption association"],
            "similarity_is_acceptance_metric": False,
            "unresolved_visual_content_must_not_enter_structured_evidence": True,
        },
        "checks": checks,
        "completed_scope": {
            "document_count": len(sample["documents"]),
            "page_count": baseline["actual"]["page_count"],
            "golden_sample_page_count": sample["sample_page_count"],
            "rapidocr_sample_page_count": rapidocr["actual"]["sample_page_count"],
            "table_candidate_count": tables["candidate_count"],
            "actual_table_page_count": tables["table_candidate_count"],
            "complex_layout_not_table_count": tables["complex_layout_not_table_count"],
            "table_cell_accuracy_status": "not_scored_for_quarantined_structured_regions",
            "low_text_record_count_all_pages": baseline["actual"]["low_text_record_count_all_pages"],
            "low_text_candidate_count_excluding_expected_exception_modes": baseline["actual"]["low_text_candidate_count_excluding_expected_exception_modes"],
        },
        "low_similarity_scanned_pages": low_similarity_scanned,
        "quality_benchmark": (
            {
                "path": str(quality_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "status": quality["status"],
                "authority": quality["authority"],
                "rapidocr": quality["engines"]["rapidocr"],
                "table_quality": quality["table_quality"],
            }
            if quality
            else {"status": "not_run"}
        ),
        "audit_mode": "frozen_stage5_artifact_read_only",
        "automatic_recheck": False,
        "manual_recheck_trigger": "仅在负责人明确要求或主动确认原始资料/OCR代码发生变化时重新执行阶段5复核",
        "next_stage_allowed": (
            "Stage 6 may begin for original-page-verified, reliably locatable regions; quarantined pages and structured regions require region/cell review first."
            if all(checks.values())
            else "Stage 6 is blocked until the Stage 5 visual and quality gates close."
        ),
        "next_stage_inputs": [
            "data/stage5/stage5_sample_manifest.json",
            "data/stage5/stage5_truth_annotations_*.json",
            "data/stage5/stage5_quality_benchmark_*.json",
            "data/stage5/stage5_exit_audit_*.json",
            "Original materials original PDF pages",
        ],
        "artifact_provenance": {
            "input_fingerprint": rapidocr.get("input_fingerprint"),
            "input_audit": str(input_audit_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "baseline": str(baseline_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "rapidocr": str(rapidocr_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "engine_decision": str(engine_decision_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "matched_by_input_fingerprint": False,
        },
        "blocking_items": [
            "36页 Golden Sample 逐页视觉复核记录尚未形成。",
        ] if not checks["golden_sample_visual_review_complete"] else (
            ["Original PDF quality benchmark has not completed."]
            if not checks["original_pdf_quality_benchmark_recorded"]
            else []
        ),
        "owner_review_needed_in_chat": [],
        "boundaries": [
            "本审计不把 RapidOCR 与既有文本的相似度当作 OCR 准确率。",
            "规则线检测只产生表格候选区域，不代表单元格解析已通过。",
            "表格或图示无法可靠结构化时，只保留原始页视觉依据并隔离出正式 Evidence 流程。",
        ],
    }


def main() -> int:
    output = STAGE5_ROOT / f"stage5_exit_audit_{TODAY}.json"
    result = audit()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output)}, ensure_ascii=False))
    return 0 if all(result["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

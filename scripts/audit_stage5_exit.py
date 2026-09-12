"""Assemble the current Stage 5 gate without falsely closing it."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from turbine_kg.settings import PROJECT_ROOT


STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"
TODAY = date.today().isoformat()
FROZEN_PROVENANCE = {
    "input_audit": "stage5_input_audit_2026-09-10.json",
    "baseline": "stage5_baseline_benchmark_2026-09-10.json",
    "rapidocr": "stage5_rapidocr_sample_benchmark_2026-09-10.json",
    "engine_decision": "stage5_engine_decision_2026-09-10.json",
    "quality": "stage5_quality_benchmark_2026-09-10.json",
    "review_snapshot_date": "2026-09-09",
}
BLOCKING_MESSAGES = {
    "frozen_provenance_consistent": "Frozen Stage 5 provenance is inconsistent.",
    "sample_manifest_five_documents": "The frozen sample manifest does not contain five documents.",
    "sample_manifest_36_pages": "The frozen sample manifest does not contain 36 pages.",
    "sample_manifest_physical_page_contract": "The physical-page contract is not satisfied.",
    "input_audit_pass": "The Stage 5 input audit has blocking discrepancies.",
    "full_baseline_775_pages": "The full baseline does not cover 775 pages.",
    "full_baseline_zero_failures": "The full baseline contains failed pages.",
    "rapidocr_sample_36_pages": "The frozen RapidOCR sample does not cover 36 pages.",
    "rapidocr_zero_failures": "The frozen RapidOCR sample contains failed pages.",
    "engine_choice_recorded": "The Stage 5 OCR engine choice is not recorded as RapidOCR.",
    "table_candidate_scope_corrected": "The frozen table candidate scope is inconsistent.",
    "table_structural_truth_complete": "Table structural truth is incomplete.",
    "table_cell_accuracy_boundary_explicit": "The table cell accuracy boundary is not explicit.",
    "golden_sample_visual_review_complete": "The 36-page Golden Sample visual review is incomplete.",
    "page_identity_reconciled": "Page identity reconciliation is incomplete.",
    "original_pdf_quality_benchmark_recorded": "The Original PDF quality benchmark is incomplete.",
}


def _relative_artifact_path(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")


def _derive_blocking_items(checks: dict[str, bool]) -> list[str]:
    return [
        message for name, message in BLOCKING_MESSAGES.items() if not checks.get(name, False)
    ]


def _load_frozen_artifacts(provenance: dict[str, str] | None = None) -> dict:
    """Load one explicitly frozen artifact set and verify its identity links.

    The exit audit is intentionally read-only.  It must not select each input
    independently by recency because that can silently combine different
    Stage 5 runs.  ``provenance`` is injectable for tests and future frozen
    batches; the default names the currently approved historical snapshot.
    """

    selected = dict(FROZEN_PROVENANCE)
    if provenance:
        selected.update(provenance)
    names = {
        key: selected[key]
        for key in ("input_audit", "baseline", "rapidocr", "engine_decision", "quality")
    }
    review_date = selected["review_snapshot_date"]
    names.update(
        {
            "tables": f"stage5_table_baseline_{review_date}.json",
            "table_truth": f"stage5_table_truth_review_{review_date}.json",
            "page_identity": f"stage5_page_identity_audit_{review_date}.json",
            "golden_review": f"stage5_golden_sample_review_{review_date}.json",
            "sample": "stage5_sample_manifest.json",
        }
    )

    paths = {key: STAGE5_ROOT / name for key, name in names.items()}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing frozen Stage 5 artifacts: " + ", ".join(missing))
    artifacts = {
        key: json.loads(path.read_text(encoding="utf-8"))
        for key, path in paths.items()
    }

    input_fingerprint = artifacts["input_audit"].get("input_fingerprint")
    fingerprint_fields = {
        "baseline": "input_fingerprint",
        "rapidocr": "input_fingerprint",
        "engine_decision": "ocr_input_fingerprint",
        "quality": "ocr_input_fingerprint",
    }
    if not input_fingerprint or any(
        artifacts[key].get(field) != input_fingerprint
        for key, field in fingerprint_fields.items()
    ):
        raise ValueError("frozen Stage 5 artifacts do not share one input fingerprint")

    expected_links = {
        "baseline": ("input_audit", "input_audit"),
        "engine_decision": ("rapidocr", "ocr_artifact"),
        "quality": ("rapidocr", "ocr_artifact"),
    }
    for artifact_key, (target_key, field) in expected_links.items():
        declared = artifacts[artifact_key].get(field)
        expected = _relative_artifact_path(paths[target_key])
        if declared != expected:
            raise ValueError(
                f"frozen {artifact_key} does not point to the selected {target_key}: "
                f"{declared!r} != {expected!r}"
            )
    return {
        "input_fingerprint": input_fingerprint,
        "review_snapshot_date": review_date,
        "paths": paths,
        "artifacts": artifacts,
    }


def audit(provenance: dict[str, str] | None = None) -> dict:
    frozen = _load_frozen_artifacts(provenance)
    paths = frozen["paths"]
    artifacts = frozen["artifacts"]
    input_audit = artifacts["input_audit"]
    baseline = artifacts["baseline"]
    rapidocr = artifacts["rapidocr"]
    engine_decision = artifacts["engine_decision"]
    quality = artifacts["quality"]
    tables = artifacts["tables"]
    table_truth = artifacts["table_truth"]
    page_identity = artifacts["page_identity"]
    sample = artifacts["sample"]
    golden_review = artifacts["golden_review"]
    quality_path = paths["quality"]
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
        "frozen_provenance_consistent": True,
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
    next_stage_allowed = all(checks.values())
    blocking_items = _derive_blocking_items(checks)
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_exit_audit",
        "audited_at": TODAY,
        "status": "complete_with_quarantine" if next_stage_allowed else "awaiting_golden_sample_review",
        "closure_status": "closed_with_quarantine" if next_stage_allowed else "open",
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
                "path": _relative_artifact_path(quality_path),
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
        "next_stage_allowed": next_stage_allowed,
        "next_stage_message": (
            "Stage 6 may begin for original-page-verified, reliably locatable regions; quarantined pages and structured regions require region/cell review first."
            if next_stage_allowed
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
            "input_fingerprint": frozen["input_fingerprint"],
            "review_snapshot_date": frozen["review_snapshot_date"],
            "selected_artifacts": {
                key: _relative_artifact_path(path) for key, path in paths.items()
            },
            "fingerprint_comparison_performed": True,
            "fingerprint_match_status": "matched",
        },
        "blocking_items": blocking_items,
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

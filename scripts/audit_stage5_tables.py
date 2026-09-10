"""Summarize Stage 5 table-structure candidates without claiming cell accuracy."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from turbine_kg.settings import PROJECT_ROOT


def _latest_baseline() -> Path:
    candidates = sorted((PROJECT_ROOT / "data" / "stage5").glob("stage5_baseline_benchmark_*.json"))
    if not candidates:
        raise FileNotFoundError("no Stage 5 baseline benchmark artifact is available")
    return candidates[-1]


def audit() -> dict:
    baseline_path = _latest_baseline()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    records = [
        record
        for record in baseline["page_records"]
        if record["is_golden_sample"]
        and set(record["sample_categories"]) & {"table", "continuation_table", "table_candidate", "complex_layout"}
    ]
    candidates = []
    for record in records:
        metrics = record["processing_metrics"]["visual_table_metrics"] or {}
        has_grid = bool(metrics.get("ruled_table_candidate"))
        if "complex_layout" in record["sample_categories"]:
            status = "complex_layout_not_table_reviewed"
            cell_status = "not_applicable_not_table"
        elif has_grid:
            status = "ruled_grid_candidate_manual_cell_truth_pending"
            cell_status = "not_scored_manual_truth_required"
        elif metrics.get("horizontal_rule_count", 0) >= 2:
            status = "partial_rules_manual_column_boundary_review_pending"
            cell_status = "not_scored_manual_truth_required"
        else:
            status = "no_rule_detected_manual_layout_review_pending"
            cell_status = "not_scored_manual_truth_required"
        candidates.append({
            "document_key": record["document_key"],
            "pdf_page": record["pdf_page"],
            "physical_page": record.get("physical_page", record["pdf_page"]),
            "sample_categories": record["sample_categories"],
            "page_mode": record["page_mode"],
            "pymupdf_table_count": record["processing_metrics"]["table_count_detected"],
            "visual_table_metrics": metrics,
            "status": status,
            "cell_text_accuracy_status": cell_status,
            "review_scope": "complex_layout_not_table" if "complex_layout" in record["sample_categories"] else "table_candidate",
            "user_escalation": False,
            "user_escalation_rule": "Escalate only if Codex cannot resolve a numeric, unit, negation, cell-boundary, or continuation-table question from the original page.",
        })
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_table_structure_baseline",
        "audited_at": date.today().isoformat(),
        "baseline": str(baseline_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "scope": "Golden Sample table and continuation-table pages only",
        "candidate_count": len(candidates),
        "ruled_grid_candidate_count": sum(item["status"].startswith("ruled_grid") for item in candidates),
        "partial_rule_candidate_count": sum(item["status"].startswith("partial_rules") for item in candidates),
        "table_candidate_count": sum(item["review_scope"] == "table_candidate" for item in candidates),
        "complex_layout_not_table_count": sum(item["review_scope"] == "complex_layout_not_table" for item in candidates),
        "candidates": candidates,
        "status": "table_structure_baseline_ready_original_page_truth_recorded",
        "boundaries": [
            "Rule detection estimates candidate regions; it does not establish cell text or row/column accuracy.",
            "PyMuPDF returning zero tables is recorded as detector output, not as proof that no table exists.",
            "The original PDF page remains the authority for manual truth.",
        ],
    }


def main() -> int:
    output = PROJECT_ROOT / "data" / "stage5" / f"stage5_table_baseline_{date.today().isoformat()}.json"
    result = audit()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output), "candidates": result["candidate_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

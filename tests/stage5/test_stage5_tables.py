import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _latest_table_baseline() -> Path:
    candidates = sorted((ROOT / "data" / "stage5").glob("stage5_table_baseline_*.json"))
    if not candidates:
        raise AssertionError("no Stage 5 table baseline artifact is available")
    return candidates[-1]


def test_stage5_table_baseline_has_all_layout_review_pages():
    path = _latest_table_baseline()
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["status"] == "table_structure_baseline_ready_original_page_truth_recorded"
    assert report["candidate_count"] == 7
    assert report["table_candidate_count"] == 6
    assert report["complex_layout_not_table_count"] == 1
    assert report["ruled_grid_candidate_count"] == 4
    assert report["partial_rule_candidate_count"] == 2
    assert sum(item["cell_text_accuracy_status"] == "not_applicable_not_table" for item in report["candidates"]) == 1
    assert sum(item["cell_text_accuracy_status"] == "not_scored_manual_truth_required" for item in report["candidates"]) == 6

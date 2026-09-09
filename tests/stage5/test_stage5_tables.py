import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_stage5_table_baseline_has_all_six_review_pages():
    path = ROOT / "data" / "stage5" / f"stage5_table_baseline_{date.today().isoformat()}.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["status"] == "table_structure_baseline_ready_manual_cell_truth_pending"
    assert report["candidate_count"] == 6
    assert report["ruled_grid_candidate_count"] == 4
    assert report["partial_rule_candidate_count"] == 2
    assert all(item["cell_text_accuracy_status"] == "not_scored_manual_truth_required" for item in report["candidates"])

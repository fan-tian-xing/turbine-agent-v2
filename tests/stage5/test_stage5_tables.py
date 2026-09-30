import json
from copy import deepcopy
from pathlib import Path

import pytest

from audit_stage5_tables import _validate_baseline
from build_stage5_truth_annotations import _table_truth, _validate_table_review
from stage5_fingerprint import sha256_file

ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> dict:
    return json.loads((ROOT / "data/stage5" / name).read_text(encoding="utf-8"))


def test_stage5_table_baseline_has_all_layout_review_pages():
    report = _read("stage5_table_baseline.json")
    sample = _read("stage5_sample_manifest.json")
    truth = _read("stage5_table_truth_review.json")
    expected = {(doc["document_key"], page["physical_page"]) for doc in sample["documents"]
                for page in doc["sample_pages"]
                if set(page["categories"]) & {"table", "continuation_table", "table_candidate", "complex_layout"}}
    candidates = report["candidates"]
    actual = {(row["document_key"], row["physical_page"]) for row in candidates}
    assert report["status"] == "table_structure_baseline_ready_original_page_truth_recorded"
    assert len(candidates) == len(actual) == len(expected) == report["candidate_count"]
    assert actual == expected
    assert report["baseline"] == "data/stage5/stage5_baseline_benchmark.json"
    assert report["inputs"]["baseline_sha256"] == sha256_file(ROOT / report["baseline"])
    table_keys = {(row["document_key"], row["physical_page"]) for row in truth["records"] if row["role"] != "complex_layout_not_table"}
    assert {(row["document_key"], row["physical_page"]) for row in candidates
            if row["review_scope"] == "table_candidate"} == table_keys
    assert report["table_candidate_count"] == len(table_keys)
    assert report["complex_layout_not_table_count"] == len(expected - table_keys)
    assert report["ruled_grid_candidate_count"] == sum(row["status"].startswith("ruled_grid") for row in candidates)
    assert report["partial_rule_candidate_count"] == sum(row["status"].startswith("partial_rules") for row in candidates)
    assert all(row["cell_text_accuracy_status"] == ("not_scored_manual_truth_required" if row["review_scope"] == "table_candidate" else "not_applicable_not_table") for row in candidates)


@pytest.mark.parametrize("change", ["old_fingerprint", "missing_page", "failed_page"])
def test_table_baseline_rejects_stale_or_incomplete_current_input(change):
    baseline = _read("stage5_baseline_benchmark.json")
    fingerprint, components = baseline["input_fingerprint"], baseline["fingerprint_components"]
    if change == "old_fingerprint":
        baseline["input_fingerprint"] = "historical"
    elif change == "missing_page":
        baseline["page_records"].pop()
    else:
        baseline["documents"][0]["failed_pages"] = [{"pdf_page": 1, "error": "read failure"}]
    with pytest.raises(ValueError):
        _validate_baseline(baseline, _read("stage5_sample_manifest.json"), fingerprint, components)


def _table_binding():
    report = _read("stage5_table_truth_review.json")
    assets = {row["asset_id"]: row for line in (ROOT / "data/registry/source_assets.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip() for row in (json.loads(line),)}
    input_hashes = {"stage5_sample_manifest_sha256": sha256_file(ROOT / "data/stage5/stage5_sample_manifest.json"),
                    "source_assets_sha256": sha256_file(ROOT / "data/registry/source_assets.jsonl"),
                    "full_corpus_reviews_sha256": sha256_file(ROOT / "data/registry/ocr_validation_report.json")}
    return report, {"manifest": _read("stage5_sample_manifest.json"), "assets": assets,
                    "fingerprint": report["source_input_fingerprint"],
                    "components": report["source_fingerprint_components"], "input_hashes": input_hashes}


def test_table_truth_retains_source_structure_and_historical_confirmation():
    report, kwargs = _table_binding()
    rows = _validate_table_review(report, **kwargs)
    assert rows[("DL5190.3", 86)]["role"] == "complex_layout_not_table"
    assert rows[("D300N", 50)]["continuation"]["continues_from_physical_page"] == 49
    assert rows[("D300N", 50)]["continuation"]["continues_to_physical_page"] == 51
    assert rows[("DLT863", 28)]["secondary_table"] == {"label": "表D.2", "leaf_column_count": 13}
    assert report["reviewed_at"] == "2026-09-09"
    assert report["historical_content_confirmation"]["new_user_approval_created"] is False
    for row in rows.values():
        assert row["original_page_geometry"]["rotation"] == row["processing_page_geometry"]["rotation"]


@pytest.mark.parametrize("change", ["stale_review", "wrong_pdf", "duplicate_page"])
def test_table_truth_cannot_be_relabelled_current_without_its_real_binding(change):
    report, kwargs = _table_binding()
    if change == "stale_review":
        report["inputs"]["full_corpus_reviews_sha256"] = "historical"
    elif change == "wrong_pdf":
        report["records"][0]["processing_sha256"] = "historical"
    else:
        report["records"].append(deepcopy(report["records"][0]))
    with pytest.raises(ValueError):
        _validate_table_review(report, **kwargs)


def test_table_truth_rejects_old_derivative_rotation(tmp_path):
    report, kwargs = _table_binding()
    row = next(row for row in report["records"] if row["document_key"] == "auxiliary_installation_book")
    row["processing_page_geometry"]["rotation"] = 0
    path = tmp_path / "stale-table-truth.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="geometry/rotation"):
        _table_truth(path, **kwargs)


def test_baseline_command_cannot_succeed_with_a_reported_page_failure(tmp_path, monkeypatch):
    import benchmark_stage5_baseline as producer
    monkeypatch.setattr(producer, "benchmark", lambda: {
        "status": "baseline_incomplete", "errors": [],
        "actual": {"page_count": 1, "failed_page_count": 1},
    })
    output = tmp_path / "failed-baseline.json"
    monkeypatch.setattr("sys.argv", ["benchmark_stage5_baseline", "--output", str(output)])
    assert producer.main() == 1

"""Reject stale or incomplete upstream baselines without consuming real PDFs."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

import stage7_baseline as binding
from audit_stage7_exit import _baseline_manifest_checks, _candidate_manifest_binding
from build_stage7_input_manifest import _expected_page_statuses
from turbine_kg.terminology.validation import content_fingerprint


@pytest.fixture
def current():
    document = {
        "document_key": "book", "document_logical_id": "doc-book", "page_count": 4,
        "original_asset_id": "original", "processing_asset_id": "ocr",
        "original_relative_path": "book.pdf", "processing_relative_path": "OCR/book.pdf",
        "sample_pages": [],
    }
    assets = {
        key: {"asset_id": key, "relative_path": path, "sha256": sha,
              "document_logical_id": "doc-book", "revision_id": "revision"}
        for key, path, sha in (("original", "book.pdf", "original-sha"), ("ocr", "OCR/book.pdf", "ocr-sha"))
    }
    components = {"selected_assets": [
        {field: asset[field] for field in ("asset_id", "relative_path", "sha256")}
        for asset in assets.values()
    ]}
    rows = []
    for number in range(1, 5):
        rows.append({
            "document_key": "book", "document_logical_id": "doc-book",
            "original_asset_id": "original", "processing_asset_id": "ocr",
            "physical_page": number, "page_mode": "mixed",
            "processing_metrics": {"text_chars": 0 if number == 3 else 10 if number == 4 else 100,
                                   "low_text_flag": number in (3, 4), "table_count_detected": int(number == 2)},
            "reviewed_blank": number == 3, "full_review_binding_status": "bound_to_current_pdf",
        })
    baseline = {
        "artifact_kind": "stage5_baseline_benchmark", "input_fingerprint": "current",
        "fingerprint_components": components, "errors": [], "page_records": rows,
        "documents": [{"document_key": "book", "page_count": 4, "processed_page_count": 4,
                       "failed_page_count": 0, "failed_pages": [], "processing_sha256": "ocr-sha",
                       "original_sha256": "original-sha"}],
        "actual": {"document_count": 1, "page_count": 4, "failed_page_count": 0},
    }
    return baseline, {"documents": [document]}, assets, components


def validate(current):
    baseline, sample, assets, components = current
    return binding.validate_current_baseline(baseline, sample, assets, "current", components)


def test_dynamic_coverage_and_legal_blank_are_counted_from_records(current):
    pages, coverage = validate(current)
    assert coverage == {"expected_page_count": 4, "page_record_count": 4,
                        "reviewed_blank_page_count": 1, "empty_nonblank_page_count": 0,
                        "failed_page_count": 0}
    statuses = _expected_page_statuses(current[1], pages, {("book", 3): {"status": "accepted_text_evidence"}})
    assert list(statuses.values()) == ["text_accepted", "visual_only", "excluded_non_content", "quarantined"]


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "out_of_scope", "failure", "false_total"])
def test_incomplete_or_failed_baseline_cannot_be_admitted(current, mutation):
    baseline = current[0]
    if mutation == "missing":
        baseline["page_records"].pop()
    elif mutation == "duplicate":
        baseline["page_records"].append(deepcopy(baseline["page_records"][0]))
    elif mutation == "out_of_scope":
        baseline["page_records"][-1]["physical_page"] = 5
    elif mutation == "failure":
        baseline["documents"][0]["failed_pages"] = [{"pdf_page": 4, "error": "read failure"}]
        baseline["documents"][0]["failed_page_count"] = 1
    else:
        baseline["actual"]["page_count"] = 775
    with pytest.raises(ValueError):
        validate(current)


@pytest.mark.parametrize("change", ["stale_fingerprint", "changed_pdf", "wrong_identity", "unbound_blank", "missing_blank_flag"])
def test_stale_bytes_identity_or_unreviewed_blank_cannot_pass(current, change):
    baseline, _, assets, _ = current
    if change == "stale_fingerprint":
        baseline["input_fingerprint"] = "historical"
    elif change == "changed_pdf":
        assets["ocr"]["sha256"] = "different-current-pdf"
    elif change == "wrong_identity":
        baseline["page_records"][0]["processing_asset_id"] = "historical-ocr"
    elif change == "unbound_blank":
        baseline["page_records"][2]["full_review_binding_status"] = "not_bound"
    else:
        del baseline["page_records"][2]["reviewed_blank"]
    with pytest.raises(ValueError):
        validate(current)


def test_canonical_baseline_cannot_fall_back_to_a_newer_dated_file(current, tmp_path, monkeypatch):
    baseline, sample, assets, components = current
    directory = tmp_path / "data/stage5"
    directory.mkdir(parents=True)
    (directory / "stage5_baseline_benchmark_2099-12-31.json").write_text(json.dumps(baseline))
    stale = deepcopy(baseline)
    stale["input_fingerprint"] = "old"
    (tmp_path / binding.BASELINE_RELATIVE_PATH).write_text(json.dumps(stale))
    monkeypatch.setattr(binding, "ocr_fingerprint", lambda: ("current", components))
    with pytest.raises(ValueError, match="fingerprint"):
        binding.load_current_baseline(tmp_path, sample, assets)


def test_audit_recomputes_statuses_and_detects_swaps_even_with_correct_totals(current):
    pages, coverage = validate(current)
    statuses = _expected_page_statuses(current[1], pages, {})
    manifest = {"pages": [
        {"document_key": key[0], "physical_page": key[1], "document_logical_id": "doc-book",
         "processing_asset_id": "ocr", "authority_asset_id": "original", "stage5_page_mode": "mixed",
         "page_status": status}
        for key, status in statuses.items()
    ], "status_counts": {status: 1 for status in statuses.values()}, "baseline_coverage": coverage}
    assert all(_baseline_manifest_checks(manifest, current[1], pages, coverage, {}).values())
    manifest["pages"][0]["page_status"], manifest["pages"][1]["page_status"] = (
        manifest["pages"][1]["page_status"], manifest["pages"][0]["page_status"])
    checks = _baseline_manifest_checks(manifest, current[1], pages, coverage, {})
    assert checks["expected_page_status_counts"]
    assert not checks["page_statuses_match_current_baseline"]
    manifest["status_counts"]["text_accepted"] = 726
    assert not _baseline_manifest_checks(manifest, current[1], pages, coverage, {})["expected_page_status_counts"]


def test_stage6_binding_rejects_historical_evidence_and_changed_review(tmp_path: Path):
    paths = {
        "stage5_sample_manifest_sha256": "data/stage5/stage5_sample_manifest.json",
        "source_assets_sha256": "data/registry/source_assets.jsonl",
        "full_corpus_reviews_sha256": "data/registry/ocr_validation_report.json",
        "stage5_exit_audit_sha256": "data/stage5/stage5_exit_audit.json",
        "stage5_truth_annotations_sha256": "data/stage5/stage5_truth_annotations_2026-09-28.json",
        "stage5_table_truth_review_sha256": "data/stage5/stage5_table_truth_review.json",
    }
    for relative in paths.values():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"status": "complete", "next_stage_allowed": True}))
    inputs = {key: binding.sha256_file(tmp_path / relative) for key, relative in paths.items()}
    inputs["source_input_fingerprint"] = "current"
    stage6 = tmp_path / "data/stage6"
    stage6.mkdir()
    golden = stage6 / "stage6_evidence_golden_sample.json"
    golden.write_text(json.dumps({"inputs": inputs}))
    build = stage6 / "stage6_evidence_build_audit.json"
    build.write_text(json.dumps({"status": "complete", "inputs": {**inputs,
        "stage6_evidence_golden_sample_sha256": binding.sha256_file(golden)}}))
    (stage6 / "stage6_table_evidence_audit.json").write_text(json.dumps({"status": "complete", "inputs": inputs}))
    (stage6 / "stage6_exit_audit.json").write_text(json.dumps({"status": "complete", "next_stage_allowed": True}))
    binding.validate_stage6_source_binding(tmp_path, "current")
    with pytest.raises(ValueError, match="current reviewed"):
        binding.validate_stage6_source_binding(tmp_path, "new-PDF")
    (tmp_path / paths["full_corpus_reviews_sha256"]).write_text("changed review")
    with pytest.raises(ValueError, match="current reviewed"):
        binding.validate_stage6_source_binding(tmp_path, "current")


def test_old_candidates_cannot_pass_after_current_manifest_is_rebuilt():
    manifest = {"content_fingerprint": "current", "pages": [
        {"page_id": "accepted", "page_status": "text_accepted"},
        {"page_id": "table", "page_status": "visual_only"},
    ]}
    candidates = {"inputs": {
        "terminology_input_manifest": "data/stage7/terminology_input_manifest.json",
        "source_fingerprint": content_fingerprint({"manifest": "current", "page_text_ids": ["accepted"]}),
    }}
    assert _candidate_manifest_binding(candidates, manifest)
    manifest["content_fingerprint"] = "new-current-inputs"
    assert not _candidate_manifest_binding(candidates, manifest)

import json
from pathlib import Path
import sys

import pytest

import audit_stage5_exit
import audit_stage5_inputs
import benchmark_stage5_rapidocr_sample


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"


def _latest(pattern: str) -> Path:
    candidates = sorted(STAGE5_ROOT.glob(pattern))
    assert candidates, f"no artifact matches {pattern}"
    return candidates[-1]


def test_stage5_sample_manifest_is_frozen_to_five_documents_and_36_pages():
    manifest = json.loads((STAGE5_ROOT / "stage5_sample_manifest.json").read_text(encoding="utf-8"))

    assert manifest["status"] == "frozen_for_initial_review"
    assert manifest["sample_page_count"] == 36
    assert len(manifest["documents"]) == 5
    assert sum(len(document["sample_pages"]) for document in manifest["documents"]) == 36
    assert all(document["page_count"] > 0 for document in manifest["documents"])
    assert all(
        page["physical_page"] == page["pdf_page"]
        for document in manifest["documents"]
        for page in document["sample_pages"]
    )


def test_stage5_input_and_rapidocr_audit_have_no_page_failures():
    input_audit = json.loads(_latest("stage5_input_audit_*.json").read_text(encoding="utf-8"))
    baseline = json.loads(_latest("stage5_baseline_benchmark_*.json").read_text(encoding="utf-8"))
    rapidocr = json.loads(_latest("stage5_rapidocr_sample_benchmark_*.json").read_text(encoding="utf-8"))

    assert input_audit["status"] == "pass"
    assert input_audit["sample_page_count"] == 36
    assert baseline["actual"]["page_count"] == 775
    assert baseline["actual"]["failed_page_count"] == 0
    assert baseline["actual"]["low_text_record_count_all_pages"] == 3
    assert baseline["actual"]["low_text_candidate_count_excluding_expected_exception_modes"] == 1
    assert rapidocr["actual"]["sample_page_count"] == 36
    assert rapidocr["actual"]["failed_page_count"] == 0


def test_stage5_rapidocr_artifact_records_reusable_fingerprint_payload():
    rapidocr = json.loads(_latest("stage5_rapidocr_sample_benchmark_*.json").read_text(encoding="utf-8"))
    assert rapidocr["input_fingerprint"]
    assert all(
        "text" in row["fresh_rapidocr"]
        and "boxes" in row["fresh_rapidocr"]
        and row["fresh_rapidocr"]["elapsed_seconds"] > 0
        for row in rapidocr["records"]
    )


def test_stage5_exit_audit_is_frozen_read_only():
    audit = json.loads(_latest("stage5_exit_audit_*.json").read_text(encoding="utf-8"))
    assert audit["audit_mode"] == "frozen_stage5_artifact_read_only"
    assert audit["automatic_recheck"] is False
    provenance = audit["artifact_provenance"]
    assert provenance["fingerprint_comparison_performed"] is True
    assert provenance["fingerprint_match_status"] == "matched"
    assert "selected_artifacts" in provenance


def test_stage5_exit_audit_closes_after_visual_gate_and_keeps_boundaries():
    exit_audit = json.loads(_latest("stage5_exit_audit_*.json").read_text(encoding="utf-8"))

    assert exit_audit["status"] == "complete_with_quarantine"
    assert exit_audit["closure_status"] == "closed_with_quarantine"
    assert exit_audit["formal_release"] is False
    assert exit_audit["owner_confirmed_quality_policy"]["content_must_match_original_exactly"] is True
    assert exit_audit["owner_confirmed_quality_policy"]["similarity_is_acceptance_metric"] is False
    assert all(exit_audit["checks"].values())
    assert exit_audit["blocking_items"] == []
    assert exit_audit["owner_review_needed_in_chat"] == []


def test_stage5_page_identity_distinguishes_physical_and_logical_pages():
    identity = json.loads(_latest("stage5_page_identity_audit_*.json").read_text(encoding="utf-8"))

    assert identity["status"] == "page_identity_reconciled"
    d300n = next(item for item in identity["records"] if item["document_key"] == "D300N")
    aux = next(item for item in identity["records"] if item["document_key"] == "auxiliary_installation_book")
    assert (d300n["physical_pdf_page"], d300n["logical_page_label"], d300n["page_role"]) == (94, "3-3-4", "blank_boundary_page")
    assert (aux["physical_pdf_page"], aux["logical_page_label"], aux["page_role"]) == (300, "291", "formula_figure_text_page")
    assert all(item["source_page_visual_match"] for item in identity["records"])


def test_stage5_original_pdf_quality_benchmark_is_complete_with_explicit_quarantine():
    candidates = sorted(STAGE5_ROOT.glob("stage5_quality_benchmark_*.json"))
    assert candidates, "quality benchmark artifact has not been generated"
    report = json.loads(candidates[-1].read_text(encoding="utf-8"))
    assert report["authority"].startswith("Original materials")
    assert report["status"] == "complete_with_quarantine"
    assert report["sample_page_count"] == 36
    assert report["errors"] == []
    assert report["engines"]["rapidocr"]["scored_text_page_count"] > 0
    assert report["engines"]["rapidocr"]["runtime"]["total_seconds"] > 0
    assert report["table_quality"]["quarantine_coverage"] == "6/6"
    assert report["ocr_input_fingerprint"] == json.loads(
        _latest("stage5_rapidocr_sample_benchmark_*.json").read_text(encoding="utf-8")
    )["input_fingerprint"]


def test_stage5_scan_truth_never_reuses_registered_ocr_as_truth():
    truth = json.loads(_latest("stage5_truth_annotations_*.json").read_text(encoding="utf-8"))
    assert all(
        row["reference_kind"] != "original_pdf_visual_reviewed_transcription"
        for row in truth["records"]
    )


def test_stage5_quarantined_table_pages_cannot_be_structured_text_scored():
    truth = json.loads(_latest("stage5_truth_annotations_*.json").read_text(encoding="utf-8"))
    quarantined = [
        row for row in truth["records"]
        if row["gate_disposition"] == "quarantine_structured_ocr"
    ]
    assert len(quarantined) == 6
    assert all(not row["structured_text_scoring_allowed"] for row in quarantined)
    assert all(
        row["structured_text_scoring_allowed"]
        or row["gate_disposition"] == "quarantine_structured_ocr"
        or row["reference_kind"] == "original_pdf_visual_only_unscored"
        for row in truth["records"]
    )


def test_stage5_latest_exit_audit_consumes_original_pdf_quality_benchmark():
    candidates = sorted(STAGE5_ROOT.glob("stage5_exit_audit_*.json"))
    assert candidates, "Stage 5 exit audit artifact has not been generated"
    audit = json.loads(candidates[-1].read_text(encoding="utf-8"))
    assert audit["status"] == "complete_with_quarantine"
    assert audit["checks"]["original_pdf_quality_benchmark_recorded"] is True
    assert audit["quality_benchmark"]["status"] == "complete_with_quarantine"
    assert audit["next_stage_allowed"] is True
    assert "Stage 6" in audit["next_stage_message"]
    assert audit["next_stage_inputs"]


def _configure_input_audit(monkeypatch, tmp_path, registry_sha256: str):
    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"fixture")
    asset = {
        "asset_id": "asset-fixture",
        "source_root_id": "source",
        "relative_path": "document.pdf",
        "sha256": registry_sha256,
    }
    sample_path = tmp_path / "sample.json"
    sample_path.write_text(
        json.dumps(
            {
                "scope": "fixture",
                "sample_page_count": 1,
                "documents": [
                    {
                        "document_key": "fixture",
                        "document_logical_id": "doc-fixture",
                        "processing_asset_id": "asset-fixture",
                        "original_asset_id": "asset-fixture",
                        "page_count": 1,
                        "sample_pages": [{"physical_page": 1, "pdf_page": 1}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class FixtureSettings:
        source_root = tmp_path
        ocr_derived_root = tmp_path

        @classmethod
        def from_environment(cls):
            return cls()

    monkeypatch.setattr(audit_stage5_inputs, "Settings", FixtureSettings)
    monkeypatch.setattr(audit_stage5_inputs, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit_stage5_inputs, "SAMPLE_MANIFEST", sample_path)
    monkeypatch.setattr(audit_stage5_inputs, "_load_assets", lambda: {asset["asset_id"]: asset})
    monkeypatch.setattr(audit_stage5_inputs, "ocr_fingerprint", lambda: ("fixture-input", {}))
    monkeypatch.setattr(audit_stage5_inputs, "_page_summary", lambda path: {"page_count": 1})
    return pdf_path


def test_stage5_input_audit_hashes_each_physical_file_once(monkeypatch, tmp_path):
    pdf_path = _configure_input_audit(monkeypatch, tmp_path, "fixture-sha256")
    calls = []

    def fake_sha256(path):
        calls.append(path.resolve())
        return "fixture-sha256"

    monkeypatch.setattr(audit_stage5_inputs, "_sha256", fake_sha256)
    result = audit_stage5_inputs.audit()

    assert result["status"] == "pass"
    assert calls == [pdf_path.resolve()]


def test_stage5_input_audit_hash_mismatch_is_a_blocking_error(monkeypatch, tmp_path):
    _configure_input_audit(monkeypatch, tmp_path, "registry-sha256")
    monkeypatch.setattr(audit_stage5_inputs, "_sha256", lambda path: "actual-sha256")

    result = audit_stage5_inputs.audit()

    assert result["status"] == "fail"
    assert any("SHA-256 differs from Registry" in error for error in result["errors"])


def test_stage5_exit_uses_one_explicit_frozen_provenance_set():
    frozen = audit_stage5_exit._load_frozen_artifacts()

    assert frozen["input_fingerprint"]
    assert frozen["review_snapshot_date"] == "2026-09-09"
    assert set(frozen["paths"]) == {
        "input_audit",
        "baseline",
        "rapidocr",
        "engine_decision",
        "quality",
        "tables",
        "table_truth",
        "page_identity",
        "golden_review",
        "sample",
    }
    assert not hasattr(audit_stage5_exit, "read_latest")


def test_stage5_exit_rejects_cross_batch_fingerprint_mix(tmp_path, monkeypatch):
    root = tmp_path / "data" / "stage5"
    root.mkdir(parents=True)
    monkeypatch.setattr(audit_stage5_exit, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(audit_stage5_exit, "STAGE5_ROOT", root)
    names = dict(audit_stage5_exit.FROZEN_PROVENANCE)
    for key in ("input_audit", "baseline", "rapidocr", "engine_decision", "quality"):
        payload = {
            "input_fingerprint": "batch-a",
            "ocr_input_fingerprint": "batch-a",
        }
        if key == "quality":
            payload["ocr_input_fingerprint"] = "batch-b"
        if key == "baseline":
            payload["input_audit"] = "data/stage5/" + names["input_audit"]
        if key in {"engine_decision", "quality"}:
            payload["ocr_artifact"] = "data/stage5/" + names["rapidocr"]
        (root / names[key]).write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
    for name in (
        "stage5_table_baseline_2026-09-09.json",
        "stage5_table_truth_review_2026-09-09.json",
        "stage5_page_identity_audit_2026-09-09.json",
        "stage5_golden_sample_review_2026-09-09.json",
        "stage5_sample_manifest.json",
    ):
        (root / name).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="one input fingerprint"):
        audit_stage5_exit._load_frozen_artifacts()


def test_stage5_blocking_items_are_derived_from_failed_boolean_checks():
    checks = {name: True for name in audit_stage5_exit.BLOCKING_MESSAGES}
    checks["input_audit_pass"] = False
    checks["rapidocr_zero_failures"] = False

    assert audit_stage5_exit._derive_blocking_items(checks) == [
        "The Stage 5 input audit has blocking discrepancies.",
        "The frozen RapidOCR sample contains failed pages.",
    ]


def test_stage5_rapidocr_cache_mismatch_does_not_rerun_without_force(monkeypatch, tmp_path):
    cached = tmp_path / "cached.json"
    monkeypatch.setattr(benchmark_stage5_rapidocr_sample, "ocr_fingerprint", lambda: ("new", {}))
    monkeypatch.setattr(benchmark_stage5_rapidocr_sample, "find_matching_artifact", lambda pattern, fingerprint: cached)
    monkeypatch.setattr(
        benchmark_stage5_rapidocr_sample,
        "benchmark",
        lambda: pytest.fail("OCR must not rerun without --force"),
    )
    monkeypatch.setattr(sys, "argv", ["benchmark_stage5_rapidocr_sample.py", "--output", str(tmp_path / "new.json")])

    assert benchmark_stage5_rapidocr_sample.main() == 0

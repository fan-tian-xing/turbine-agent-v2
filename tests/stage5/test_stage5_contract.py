import json
from pathlib import Path


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
    assert "Stage 6" in audit["next_stage_allowed"]
    assert audit["next_stage_inputs"]

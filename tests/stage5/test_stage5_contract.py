import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"


def test_stage5_sample_manifest_is_frozen_to_five_documents_and_36_pages():
    manifest = json.loads((STAGE5_ROOT / "stage5_sample_manifest.json").read_text(encoding="utf-8"))

    assert manifest["status"] == "frozen_for_initial_review"
    assert manifest["sample_page_count"] == 36
    assert len(manifest["documents"]) == 5
    assert sum(len(document["sample_pages"]) for document in manifest["documents"]) == 36
    assert all(document["page_count"] > 0 for document in manifest["documents"])


def test_stage5_input_and_baseline_audits_have_no_page_failures():
    input_audit = json.loads((STAGE5_ROOT / "stage5_input_audit_2026-09-09.json").read_text(encoding="utf-8"))
    baseline = json.loads((STAGE5_ROOT / "stage5_baseline_benchmark_2026-09-09.json").read_text(encoding="utf-8"))
    rapidocr = json.loads((STAGE5_ROOT / "stage5_rapidocr_sample_benchmark_2026-09-09.json").read_text(encoding="utf-8"))

    assert input_audit["status"] == "pass"
    assert input_audit["sample_page_count"] == 36
    assert baseline["actual"]["page_count"] == 775
    assert baseline["actual"]["failed_page_count"] == 0
    assert baseline["actual"]["low_text_record_count_all_pages"] == 3
    assert baseline["actual"]["low_text_candidate_count_excluding_expected_exception_modes"] == 1
    assert rapidocr["actual"]["sample_page_count"] == 36
    assert rapidocr["actual"]["failed_page_count"] == 0


def test_stage5_exit_audit_keeps_owner_quality_decisions_open():
    exit_audit = json.loads((STAGE5_ROOT / "stage5_exit_audit_2026-09-09.json").read_text(encoding="utf-8"))

    assert exit_audit["status"] == "awaiting_owner_quality_decisions"
    assert all(exit_audit["checks"].values())
    assert len(exit_audit["blocking_items"]) == 4


def test_stage5_page_identity_distinguishes_physical_and_logical_pages():
    identity = json.loads((STAGE5_ROOT / "stage5_page_identity_audit_2026-09-09.json").read_text(encoding="utf-8"))

    assert identity["status"] == "page_identity_reconciled"
    d300n = next(item for item in identity["records"] if item["document_key"] == "D300N")
    aux = next(item for item in identity["records"] if item["document_key"] == "auxiliary_installation_book")
    assert (d300n["physical_pdf_page"], d300n["logical_page_label"], d300n["page_role"]) == (94, "3-3-4", "blank_boundary_page")
    assert (aux["physical_pdf_page"], aux["logical_page_label"], aux["page_role"]) == (300, "291", "formula_figure_text_page")
    assert all(item["source_page_visual_match"] for item in identity["records"])

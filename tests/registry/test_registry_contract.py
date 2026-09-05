import json
import re
import sys
from pathlib import Path

import fitz


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_ROOT = PROJECT_ROOT / "data" / "registry"
SOURCE_ROOT = (PROJECT_ROOT / "../Original materials").resolve()
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from build_source_registry import (
    aggregate_document_status,
    asset_id_for_path,
    load_document_identity_map,
    validate_asset_record,
    validate_document_record,
)
from check_source_allowlist import load_allowlist


def load_jsonl(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (REGISTRY_ROOT / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_registry_counts_and_identity_contract():
    assets = load_jsonl("source_assets.jsonl")
    documents = load_jsonl("source_documents.jsonl")
    duplicates = load_jsonl("source_duplicate_groups.jsonl")
    review_queue = load_jsonl("source_review_queue.jsonl")
    manual_findings = load_jsonl("source_manual_findings.jsonl")
    summary = json.loads((REGISTRY_ROOT / "source_registry_summary.json").read_text(encoding="utf-8"))

    assert len(assets) == 48
    assert len(documents) == 44
    assert summary["physical_asset_count"] == len(assets)
    assert summary["logical_document_count"] == len(documents)
    assert summary["admitted_source_count"] == sum(
        asset["admission_status"] == "admitted" for asset in assets
    )
    expected_extractable_documents = {
        asset["document_logical_id"]
        for asset in assets
        if asset["text_adapter_status"] in {"native_text_available", "ocr_validated"}
        and any(
            other["document_logical_id"] == asset["document_logical_id"]
            and other["admission_status"] == "admitted"
            for other in assets
        )
    }
    assert summary["independent_extractable_source_count"] == len(expected_extractable_documents)
    assert summary["text_adapter_status_counts"]["native_text_available"] >= 1
    assert summary["text_adapter_status_counts"]["ocr_required"] >= 1
    assert all(asset["relative_path"].endswith(".pdf") for asset in assets)
    assert all("/" in asset["relative_path"] or "\\" in asset["relative_path"] for asset in assets)
    assert len({asset["asset_id"] for asset in assets}) == len(assets)
    assert len({asset["document_logical_id"] for asset in assets}) == len(documents)
    assert all(asset["revision_id"] == f"sha256:{asset['sha256']}" for asset in assets)
    for asset in assets:
        validate_asset_record(asset)
        assert asset["applicability_scope"]
    for document in documents:
        validate_document_record(document)
    assert all(
        all(field in asset for field in (
            "authority_level",
            "normative_modality",
            "project_adoption_status",
            "manufacturer_approval_status",
            "validity_status",
            "supersedes",
            "exception_basis",
            "text_adapter_status",
        ))
        for asset in assets
    )
    assert any(asset["identity_status"] == "confirmed" for asset in assets)
    assert any(asset["admission_status"] == "admitted" for asset in assets)
    assert any(asset["admission_status"] == "quarantined" for asset in assets)
    assert review_queue
    assert all(item["status"] in {"open", "resolved"} for item in review_queue)
    assert any(
        duplicate["relation_type"] == "ocr_derivative_of"
        and len(duplicate["asset_paths"]) == 2
        for duplicate in duplicates
    )
    assert any(
        asset["relative_path"].startswith("OCR/")
        and asset["admission_status"] == "duplicate_or_derivative"
        for asset in assets
    )
    assert any(
        "NB／T+10933" in asset["relative_path"]
        and asset["completeness_status"] == "incomplete"
        and asset["admission_status"] == "quarantined"
        for asset in assets
    )
    asset_paths = {asset["relative_path"] for asset in assets}
    assert {finding["relative_path"] for finding in manual_findings} <= asset_paths
    assert len(manual_findings) == len({finding["relative_path"] for finding in manual_findings})
    assert sum(item["issue_type"] == "manual_follow_up" for item in review_queue) == 4
    dl5190_document = next(
        document
        for document in documents
        if document["document_logical_id"] == "doc-24b25833ab82e212d1fb"
    )
    assert dl5190_document["document_status"] == "admitted"
    assert dl5190_document["text_adapter_status"] == "native_text_available"
    assert dl5190_document["open_review_count"] == 0
    assert dl5190_document["applicability_scope"]
    dl5190_asset = next(asset for asset in assets if asset["document_logical_id"] == dl5190_document["document_logical_id"])
    assert dl5190_asset["year_candidates"] == ["2019"]
    assert dl5190_asset["supersedes"] == []


def test_registry_does_not_copy_source_text_or_historical_assets():
    registry_text = "\n".join(path.read_text(encoding="utf-8") for path in REGISTRY_ROOT.glob("*"))

    for forbidden in ("旧demo", "quickpicture", "Graph Records", "blind_evaluation"):
        assert forbidden not in registry_text
    for forbidden_field in ("ocr_text", "raw_text", "source_text"):
        assert forbidden_field not in registry_text


def test_registry_schema_and_summary_are_valid_json():
    schema = json.loads((REGISTRY_ROOT / "source_registry.schema.json").read_text(encoding="utf-8"))
    document_schema = json.loads((REGISTRY_ROOT / "source_documents.schema.json").read_text(encoding="utf-8"))
    summary = json.loads((REGISTRY_ROOT / "source_registry_summary.json").read_text(encoding="utf-8"))

    assert schema["title"] == "Source Registry asset record"
    assert "asset_id" in schema["required"]
    for field in (
        "text_adapter_status",
        "authority_level",
        "normative_modality",
        "project_adoption_status",
        "manufacturer_approval_status",
        "validity_status",
        "supersedes",
        "exception_basis",
        "applicability_scope",
    ):
        assert field in schema["required"]
        assert field in schema["properties"]
    assert schema["additionalProperties"] is False
    assert document_schema["title"] == "Source Registry logical-document record"
    assert document_schema["additionalProperties"] is False
    for field in (
        "authority_level",
        "normative_modality",
        "project_adoption_status",
        "manufacturer_approval_status",
        "validity_status",
        "supersedes",
        "exception_basis",
    ):
        assert field in document_schema["required"]
        assert field in document_schema["properties"]
    assert "local_only" in schema["properties"]["external_processing_status"]["enum"]
    assert "supersedes_status" in schema["required"]
    assert "exception_basis_status" in schema["required"]
    assert summary["processing_note"].startswith("Metadata and fingerprints only")


def test_document_admission_requires_a_verified_text_adapter():
    asset = {
        "admission_status": "admitted",
        "identity_status": "confirmed",
        "completeness_status": "complete",
        "applicability_status": "confirmed",
        "applicability_scope": ["test scope"],
        "external_processing_status": "local_only",
        "text_adapter_status": "ocr_pending_validation",
    }
    assert aggregate_document_status([asset]) == "metadata_review_required"


def test_stage2_audit_outputs_exist():
    assert (REGISTRY_ROOT / "project_source_gaps.json").is_file()
    assert (REGISTRY_ROOT / "stage2_source_selection.json").is_file()
    assert (REGISTRY_ROOT / "ocr_validation_report.json").is_file()

    selection = json.loads((REGISTRY_ROOT / "stage2_source_selection.json").read_text(encoding="utf-8"))
    assert selection["stage"] == "2C"
    assert selection["stage_status"] == "complete"
    assert selection["completion_scope"] == "first_batch_selected_sources_only"


def test_selected_ocr_derivatives_exist_have_unicode_text_and_pass_validation_gate():
    expected = {
        "OCR/汽轮机本体安装及维护说明书(OCR).pdf": (94, "D300N"),
        "OCR/DLT 863-2016汽轮机启动调试导则(OCR).pdf": (28, "汽轮机启动调试导则"),
        "OCR/汽轮机辅机安装（第二版）(OCR).pdf": (480, "汽轮机辅机安装"),
        "OCR/HAF103核动力厂调试和运行安全规定-印刷页3-34(OCR).pdf": (32, "核动力厂调试和运行安全规定"),
    }
    for relative_path, (page_count, marker) in expected.items():
        path = SOURCE_ROOT / Path(*relative_path.split("/"))
        assert path.is_file()
        document = fitz.open(path)
        try:
            text = "\n".join(page.get_text("text") for page in document)
            assert len(document) == page_count
            assert marker in text
            assert "�" not in text
            assert len(re.findall(r"[\u3400-\u9fff]", text)) >= 10
        finally:
            document.close()

    selection = json.loads((REGISTRY_ROOT / "stage2_source_selection.json").read_text(encoding="utf-8"))
    assert {
        source["document_logical_id"] for source in selection["selected_sources"]
    } >= {
        "doc-24b25833ab82e212d1fb",
        "doc-af7fa1738c5c89599e41",
        "doc-7a1d2e663bf13813d7b7",
        "doc-a1f2fbf6ee4b0277e46a",
        "doc-2e6846eb4bb57309a4ee",
    }
    assert all(
        source["formal_extraction_status"] == "ready_for_stage3"
        for source in selection["selected_sources"]
    )
    assert all(
        source["text_adapter_status"] in {"native_text_available", "ocr_validated"}
        for source in selection["selected_sources"]
    )
    assets = load_jsonl("source_assets.jsonl")
    documents = {document["document_logical_id"]: document for document in load_jsonl("source_documents.jsonl")}
    assets_by_path = {asset["relative_path"]: asset for asset in assets}
    for source in selection["selected_sources"]:
        asset = assets_by_path[source["relative_path"]]
        document = documents[source["document_logical_id"]]
        assert asset["admission_status"] == "admitted"
        assert document["document_status"] == "admitted"
        assert document["open_review_count"] == 0
        assert asset["completeness_status"] == "complete"
        assert asset["applicability_status"] == "confirmed"
        assert asset["external_processing_status"] != "not_assessed"


def test_controlled_ocr_derivative_relations_and_haf_excerpt_boundary():
    relations = load_jsonl("source_duplicate_groups.jsonl")
    expected_pairs = {
        (
            "汽轮机说明书/汽轮机本体安装及维护说明书.pdf",
            "OCR/汽轮机本体安装及维护说明书(OCR).pdf",
        ),
        (
            "标准法规/DLT 863-2016汽轮机启动调试导则.pdf",
            "OCR/DLT 863-2016汽轮机启动调试导则(OCR).pdf",
        ),
        (
            "2.书籍/260824 扫描文件/汽轮机辅机安装（第二版）.pdf",
            "OCR/汽轮机辅机安装（第二版）(OCR).pdf",
        ),
        (
            "标准法规/HAF103核动力厂调试和运行安全规定.pdf",
            "OCR/HAF103核动力厂调试和运行安全规定-印刷页3-34(OCR).pdf",
        ),
    }
    actual_pairs = {
        tuple(relation["asset_paths"])
        for relation in relations
        if relation["relation_type"] == "ocr_derivative_of"
    }
    assert expected_pairs <= actual_pairs

    assets = load_jsonl("source_assets.jsonl")
    haf_original = next(
        asset for asset in assets if asset["relative_path"] == "标准法规/HAF103核动力厂调试和运行安全规定.pdf"
    )
    haf_ocr = next(
        asset
        for asset in assets
        if asset["relative_path"] == "OCR/HAF103核动力厂调试和运行安全规定-印刷页3-34(OCR).pdf"
    )
    assert (haf_original["admission_status"], haf_original["completeness_status"]) == (
        "admitted",
        "complete",
    )
    assert haf_ocr["admission_status"] == "duplicate_or_derivative"
    assert haf_ocr["text_adapter_status"] == "ocr_validated"
    selection = json.loads((REGISTRY_ROOT / "stage2_source_selection.json").read_text(encoding="utf-8"))
    haf_selection = next(
        source
        for source in selection["selected_sources"]
        if source["document_logical_id"] == haf_original["document_logical_id"]
    )
    assert haf_selection["selected_scope"] == "Supplied complete source unit, printed pages 3-34, PDF pages 1-32"
    assert haf_selection["stage2_registration_status"] == "complete"
    assert haf_selection["formal_extraction_status"] == "ready_for_stage3"
    assert "excerpt_retained" not in haf_selection["review_status"]


def test_asset_validation_rejects_contract_drift():
    asset = {
        "asset_id": "asset-00000000000000000000",
        "document_logical_id": "doc-00000000000000000000",
        "revision_id": "sha256:" + "0" * 64,
        "relative_path": "x.pdf",
        "sha256": "0" * 64,
        "size_bytes": 1,
        "page_count": 1,
        "source_role": "book",
        "text_layer_status": "scan_only",
        "text_adapter_status": "ocr_required",
        "identity_status": "review_required",
        "completeness_status": "review_required",
        "external_processing_status": "not_assessed",
        "authority_level": "unknown",
        "normative_modality": "unknown",
        "project_adoption_status": "unknown",
        "manufacturer_approval_status": "unknown",
        "validity_status": "unknown",
        "supersedes": [],
        "supersedes_status": "review_required",
        "exception_basis": [],
        "exception_basis_status": "review_required",
        "admission_status": "metadata_review_required",
        "applicability_status": "review_required",
        "applicability_scope": ["unknown"],
        "unexpected": True,
    }
    import pytest

    with pytest.raises(ValueError, match="unexpected fields"):
        validate_asset_record(asset)


def test_controlled_document_identity_map_covers_allowlist():
    _, entries = load_allowlist(PROJECT_ROOT / "config" / "source_allowlist.tsv")
    mapping = load_document_identity_map()

    assert set(mapping) == {entry["path"] for entry in entries}
    assert len(set(mapping.values())) == 44

    assets = load_jsonl("source_assets.jsonl")
    assert {asset["relative_path"]: asset["document_logical_id"] for asset in assets} == mapping


def test_asset_id_is_path_based_not_content_hash_based():
    assert asset_id_for_path("same-content-a.pdf") != asset_id_for_path("same-content-b.pdf")

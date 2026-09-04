import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_ROOT = PROJECT_ROOT / "data" / "registry"
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from build_source_registry import asset_id_for_path, load_document_identity_map
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

    assert len(assets) == 45
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
        duplicate["relation_type"] == "visual_content_identical"
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
    assert sum(item["issue_type"] == "manual_follow_up" for item in review_queue) == 5


def test_registry_does_not_copy_source_text_or_historical_assets():
    registry_text = "\n".join(path.read_text(encoding="utf-8") for path in REGISTRY_ROOT.glob("*"))

    for forbidden in ("旧demo", "quickpicture", "Graph Records", "blind_evaluation"):
        assert forbidden not in registry_text
    for forbidden_field in ("ocr_text", "raw_text", "source_text"):
        assert forbidden_field not in registry_text


def test_registry_schema_and_summary_are_valid_json():
    schema = json.loads((REGISTRY_ROOT / "source_registry.schema.json").read_text(encoding="utf-8"))
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
    ):
        assert field in schema["required"]
        assert field in schema["properties"]
    assert summary["processing_note"].startswith("Metadata and fingerprints only")


def test_controlled_document_identity_map_covers_allowlist():
    _, entries = load_allowlist(PROJECT_ROOT / "config" / "source_allowlist.tsv")
    mapping = load_document_identity_map()

    assert set(mapping) == {entry["path"] for entry in entries}
    assert len(set(mapping.values())) == 44

    assets = load_jsonl("source_assets.jsonl")
    assert {asset["relative_path"]: asset["document_logical_id"] for asset in assets} == mapping


def test_asset_id_is_path_based_not_content_hash_based():
    assert asset_id_for_path("same-content-a.pdf") != asset_id_for_path("same-content-b.pdf")

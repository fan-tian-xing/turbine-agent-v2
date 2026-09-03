import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_ROOT = PROJECT_ROOT / "data" / "registry"


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
    assert summary["independent_extractable_source_count"] == sum(
        asset["admission_status"] == "admitted" for asset in assets
    )
    assert all(asset["relative_path"].endswith(".pdf") for asset in assets)
    assert all("/" in asset["relative_path"] or "\\" in asset["relative_path"] for asset in assets)
    assert len({asset["asset_id"] for asset in assets}) == len(assets)
    assert len({asset["document_logical_id"] for asset in assets}) == len(documents)
    assert all(asset["revision_id"] == f"sha256:{asset['sha256']}" for asset in assets)
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
    assert summary["processing_note"].startswith("Metadata and fingerprints only")

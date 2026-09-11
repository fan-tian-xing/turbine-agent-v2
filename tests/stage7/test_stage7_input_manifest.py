import json
from pathlib import Path

from turbine_kg.terminology.validation import validate_input_manifest


ROOT = Path(__file__).parents[2]


def _manifest():
    return json.loads((ROOT / "data/stage7/terminology_input_manifest.json").read_text(encoding="utf-8"))


def test_stage7_manifest_covers_exactly_775_pages_and_frozen_statuses():
    manifest = validate_input_manifest(_manifest())
    assert manifest["status"] == "frozen"
    assert manifest["input_boundary"]["source_count"] == 5
    assert set(manifest["input_boundary"]["excluded_source_classes"]) == {"formal_case_materials", "holdout_materials", "blind_test_materials"}
    assert sum(manifest["status_counts"].values()) == 775
    assert manifest["status_counts"] == {
        "text_accepted": 726,
        "visual_only": 39,
        "quarantined": 2,
        "excluded_non_content": 8,
    }
    assert all(row["page_status"] == "text_accepted" or row["exclusion_reason"] for row in manifest["pages"])


def test_stage7_manifest_keeps_original_authority_separate_from_ocr_processing():
    manifest = _manifest()
    ocr_pages = [row for row in manifest["pages"] if row["processing_asset_id"] != row["authority_asset_id"]]
    assert ocr_pages
    assert all(row["processing_asset_id"] != row["authority_asset_id"] for row in ocr_pages)
    assert all(row["authority_relative_path"] != row["processing_relative_path"] for row in ocr_pages)
    assert all(row["authority_asset_id"].startswith("asset-") for row in manifest["pages"])

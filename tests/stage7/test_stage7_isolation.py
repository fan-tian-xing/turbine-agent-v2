import json
from pathlib import Path

import pytest

from turbine_kg.terminology.analyzer import analyze_terminology
from turbine_kg.terminology.validation import validate_input_manifest


def _page(status, page_id, text_sha):
    return {
        "page_id": page_id,
        "document_key": "fixture",
        "document_logical_id": "doc-" + "1" * 20,
        "revision_id": "rev-" + "2" * 20,
        "processing_asset_id": "asset-" + "3" * 20,
        "authority_asset_id": "asset-" + "4" * 20,
        "physical_page": 1 if page_id.endswith("1") else 2,
        "page_status": status,
        "text_source": "native_pdf_text",
        "processing_relative_path": "fixture.pdf",
        "authority_relative_path": "fixture.pdf",
        "processing_text_sha256": text_sha,
        "processing_text_chars": 20,
        "stage5_page_mode": "native_text",
        "stage6_sample_status": None,
        "exclusion_reason": None if status == "text_accepted" else "fixture isolation",
    }


def test_non_text_accepted_pages_are_not_consumed():
    accepted_text = "密封瓦检查要求为0.03mm。"
    import hashlib
    manifest = {"pages": [_page("text_accepted", "page-1", hashlib.sha256(accepted_text.encode()).hexdigest()), _page("visual_only", "page-2", "x")], "schema_version": 1, "stage": "7", "status_counts": {"text_accepted": 1, "visual_only": 1, "quarantined": 0, "excluded_non_content": 0}}
    # The production validator intentionally requires the real 775-page gate;
    # this test exercises the analyzer's filtering behavior with a local copy.
    page = manifest["pages"][0]
    candidates = analyze_terminology(manifest, {page["page_id"]: accepted_text})
    assert candidates
    assert all(all(item["physical_page"] == 1 for item in row["occurrences"]) for row in candidates)
    assert all("page-2" not in {item["page_id"] for item in row["occurrences"]} for row in candidates)


def test_stage7_does_not_accept_forged_manifest_shape():
    from turbine_kg.terminology.validation import validate_input_manifest
    with pytest.raises(ValueError, match="no pages"):
        validate_input_manifest({"schema_version": 1, "stage": "7", "pages": []})

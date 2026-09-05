"""Small, corpus-independent contracts for Registry behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from turbine_kg.registry.duplicates import duplicate_relations
from turbine_kg.registry.profiles import load_source_profiles, profile_for_path
from turbine_kg.registry.schema import validate_asset_record
from turbine_kg.registry.source_inputs import validate_source_allowlist


def _asset(path: str, *, text_status: str, text_fingerprint: str | None, visual_fingerprint: str) -> dict[str, object]:
    return {
        "relative_path": path,
        "sha256": f"sha-{path}",
        "visual_content_fingerprint": visual_fingerprint,
        "text_content_fingerprint": text_fingerprint,
        "text_layer_status": text_status,
        "page_visual_fingerprints": [visual_fingerprint],
    }


def test_scan_only_assets_do_not_form_a_normalized_text_duplicate_group() -> None:
    assets = [
        _asset("a.pdf", text_status="scan_only", text_fingerprint=None, visual_fingerprint="visual-a"),
        _asset("b.pdf", text_status="scan_only", text_fingerprint=None, visual_fingerprint="visual-b"),
    ]
    _, relations = duplicate_relations(assets)
    assert not [relation for relation in relations if relation["relation_type"] == "normalized_text_identical"]


def test_native_text_assets_still_form_a_normalized_text_relation() -> None:
    assets = [
        _asset("a.pdf", text_status="native_text", text_fingerprint="same-text", visual_fingerprint="visual-a"),
        _asset("b.pdf", text_status="native_text", text_fingerprint="same-text", visual_fingerprint="visual-b"),
    ]
    _, relations = duplicate_relations(assets)
    assert [relation for relation in relations if relation["relation_type"] == "normalized_text_identical"]


def test_most_specific_profile_prefix_wins_even_when_assignments_are_reordered() -> None:
    profiles, assignments, default_profile_id = load_source_profiles()
    target = "标准法规/GB/T 123.pdf"
    expected = profile_for_path(target, profiles, assignments, default_profile_id)
    reordered = list(reversed(assignments))
    assert profile_for_path(target, profiles, reordered, default_profile_id) == expected


def test_asset_contract_requires_explicit_root_kind_and_decoupled_revision() -> None:
    asset = {
        "asset_id": "asset-00000000000000000000",
        "document_logical_id": "doc-00000000000000000000",
        "revision_id": "rev-00000000000000000000",
        "source_root_id": "source",
        "asset_kind": "original",
        "source_profile_id": "book",
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
    }
    validate_asset_record(asset)
    asset["asset_kind"] = "invalid"
    with pytest.raises(ValueError, match="asset_kind"):
        validate_asset_record(asset)


def test_ocr_derived_root_cannot_be_inside_source_root(tmp_path: Path) -> None:
    errors = validate_source_allowlist(
        source_root=tmp_path / "source",
        ocr_derived_root=tmp_path / "source" / "ocr",
        allowlist_path=tmp_path / "allowlist.tsv",
    )
    assert errors == ["OCR_DERIVED_ROOT must be outside SOURCE_ROOT"]

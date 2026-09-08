from __future__ import annotations

import json
from pathlib import Path

import pytest

from turbine_kg.documents.catalog import load_identity_catalog


ROOT = Path(__file__).parents[2]


def _paths() -> tuple[Path, Path, Path]:
    return (
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )


def test_registry_identity_catalog_preserves_current_ids() -> None:
    catalog = load_identity_catalog(*_paths())
    assert len(catalog.assets) == 48
    assert len(catalog.revisions) == 44
    assert len(catalog.path_aliases) == 48
    assert len({asset.document_logical_id for asset in catalog.assets}) == 44
    derived = [asset for asset in catalog.assets if asset.asset_kind == "derived_ocr"]
    assert len(derived) == 4
    assert all(asset.derived_from_asset_id for asset in derived)
    for asset in catalog.assets:
        assert catalog.asset_for_path(asset.relative_path).asset_id == asset.asset_id


def test_derived_asset_relation_is_same_document_and_revision() -> None:
    catalog = load_identity_catalog(*_paths())
    for derived in (asset for asset in catalog.assets if asset.derived_from_asset_id):
        source = catalog.asset_for_id(derived.derived_from_asset_id)
        assert (derived.document_logical_id, derived.revision_id) == (
            source.document_logical_id,
            source.revision_id,
        )


def test_duplicate_path_is_rejected(tmp_path: Path) -> None:
    assets_path, revisions_path, links_path = _paths()
    rows = [json.loads(line) for line in assets_path.read_text(encoding="utf-8").splitlines() if line]
    rows[1]["relative_path"] = rows[0]["relative_path"]
    candidate = tmp_path / "source_assets.jsonl"
    candidate.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate asset ID or path"):
        load_identity_catalog(candidate, revisions_path, links_path)


def test_missing_derived_source_is_rejected(tmp_path: Path) -> None:
    assets_path, revisions_path, _ = _paths()
    links = tmp_path / "derived.tsv"
    links.write_text(
        "derived_relative_path\tsource_relative_path\n"
        "OCR/not-registered.pdf\tmissing.pdf\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unregistered path"):
        load_identity_catalog(assets_path, revisions_path, links)

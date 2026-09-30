from pathlib import Path

from scripts.build_stage6_golden_evidence import DOCUMENTS
from scripts.stage6_cross_page_supplements import build_cross_page_evidence, load_supplements
from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[2]


def test_six_reviewed_source_units_bind_both_original_pages():
    data = load_supplements()
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    for unit in data["source_units"]:
        evidence, ir = build_cross_page_evidence(
            unit, catalog=catalog, documents=DOCUMENTS, settings=Settings.from_environment(),
        )
        assert {location.physical_page for location in evidence.locations} == set(unit["physical_pages"])
        assert evidence.authority_asset_id == next(
            asset.asset_id for asset in ir.assets if asset.asset_kind == "original"
        )
        assert evidence.source_text_sha256
        if unit["document_key"] == "D300N":
            assert len(unit["steps"]) == 9
            assert len(evidence.locations) >= 9


def test_cross_page_evidence_version_is_repeatable_for_current_pdf():
    data = load_supplements()
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    for key in ("HAF103", "D300N"):
        unit = next(row for row in data["source_units"] if row["document_key"] == key)
        first, _ = build_cross_page_evidence(
            unit, catalog=catalog, documents=DOCUMENTS, settings=Settings.from_environment(),
        )
        second, _ = build_cross_page_evidence(
            unit, catalog=catalog, documents=DOCUMENTS, settings=Settings.from_environment(),
        )
        assert first.evidence_id == second.evidence_id
        assert first.evidence_version_id == second.evidence_version_id
        assert first.source_span_ids == second.source_span_ids

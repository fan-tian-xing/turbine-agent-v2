"""Stage 6 reviewed-source integration and page-review provenance gates."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from scripts.build_stage6_golden_evidence import (
    DOCUMENTS,
    STAGE6,
    _decision_for_fingerprint,
    _reviewed_source_supplements_for_sample,
    main,
)
from scripts.stage6_auxiliary_supplements import load_supplements as load_auxiliary
from scripts.stage6_cross_page_supplements import load_supplements as load_cross_page
from scripts.stage6_standard_supplements import load_supplements as load_standard
from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.evidence import validate_evidence_bundle
from turbine_kg.evidence.models import EvidenceBundle
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def source_context():
    return (
        load_identity_catalog(
            ROOT / "data/registry/source_assets.jsonl",
            ROOT / "config/revision_identity.tsv",
            ROOT / "config/derived_asset_links.tsv",
        ),
        Settings.from_environment(),
        load_standard(),
        load_cross_page(),
        load_auxiliary(),
    )


@pytest.mark.parametrize("key,page,logical,kind", [
    ("DL5190.3", 113, "102", "standard_reviewed"),
    ("DLT863", 27, "38", "standard_reviewed"),
    ("DLT863", 28, "39", "standard_reviewed"),
    ("HAF103", 1, "3", "cross_page_reviewed"),
    ("D300N", 50, "2-14-3", "cross_page_reviewed"),
    ("auxiliary_installation_book", 300, "291", "auxiliary_original_page_region"),
    ("auxiliary_installation_book", 480, "471", "auxiliary_reviewed"),
])
def test_reviewed_supplement_evidence_uses_actual_source_ir(
    source_context, key, page, logical, kind,
):
    catalog, settings, standard, cross_page, auxiliary = source_context
    rows = _reviewed_source_supplements_for_sample(
        {"document_key": key, "physical_page": page, "logical_page": logical},
        catalog=catalog, settings=settings,
        standard_manifest=standard, cross_page_manifest=cross_page,
        auxiliary_manifest=auxiliary,
    )
    assert rows
    assert len({item.evidence_id for item, _, _ in rows}) == len(rows)
    kinds = {metadata["source_supplement_kind"] for _, _, metadata in rows}
    assert kind in kinds
    assert kinds <= {kind, "reviewed_visual_only", "d300n_row8_reviewed_column", "auxiliary_reviewed_header"}
    for item, ir, metadata in rows:
        assert item.authority_asset_id == item.locations[0].original_asset_id
        assert ir.parsing_run.output_fingerprint
        validate_evidence_bundle(
            EvidenceBundle(1, ir.revision.revision_id, (item,), ir.parsing_run.output_fingerprint), ir,
        )
        if metadata["source_supplement_kind"] == "cross_page_reviewed":
            assert metadata["cross_page_source_unit_id"]
            assert len({location.physical_page for location in item.locations}) == 2
        if metadata["source_supplement_kind"] == "standard_reviewed" and key == "DLT863":
            assert item.disposition == "region_scoped"
        if metadata["source_supplement_kind"] == "auxiliary_original_page_region":
            assert item.disposition == "region_scoped"
            assert metadata["stage12_extractability"] == "context_only"
    groups = {metadata.get("source_supplement_group_id") for _, _, metadata in rows}
    if (key, page) == ("D300N", 50):
        assert {"d300n-p50-row8-work", "d300n-p50-row8-record-directory"} <= groups
    if (key, page) == ("auxiliary_installation_book", 300):
        assert {"aux-p300-question-2098", "aux-p300-answer-2098", "aux-p300-question-2099"} <= groups
        assert "reviewed_visual_only" in kinds
    if (key, page) == ("auxiliary_installation_book", 480):
        assert "aux-p480-two-tier-table-header" in groups


def test_stale_page_review_is_never_inherited():
    decision = {"review_id": "sample", "decision": "accepted", "expected_page_fingerprint": "old"}
    assert _decision_for_fingerprint("sample", "new", {"sample": decision}) == (
        "needs_review", decision, True,
    )


def test_prepare_review_prints_new_fingerprints_without_writing():
    artifacts = [
        STAGE6 / "stage6_evidence_annotations.jsonl",
        STAGE6 / "stage6_evidence_page_review_queue.jsonl",
        STAGE6 / "stage6_evidence_build_audit.json",
    ]
    before = {path: path.read_bytes() if path.exists() else None for path in artifacts}
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        main(prepare_review=True)
    after = {path: path.read_bytes() if path.exists() else None for path in artifacts}
    assert after == before
    prepared = json.loads(output.getvalue())
    assert prepared["writes"] is False
    by_id = {row["review_id"]: row for row in prepared["page_fingerprints"]}
    decisions = {
        row["review_id"]: row
        for row in (
            json.loads(line)
            for line in (STAGE6 / "stage6_page_review_decisions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    for review_id in (
        "stage6-DL5190.3-p113-page", "stage6-DLT863-p27-page",
        "stage6-DLT863-p28-page", "stage6-HAF103-p1-page",
        "stage6-D300N-p50-page", "stage6-auxiliary_installation_book-p480-page",
    ):
        assert by_id[review_id]["decision"] == decisions[review_id]["decision"] == "accepted"
        assert by_id[review_id]["expected_page_fingerprint"] == decisions[review_id]["expected_page_fingerprint"]
        assert len(by_id[review_id]["expected_page_fingerprint"]) == 64
    assert by_id["stage6-HAF103-p1-page"]["prior_review_fingerprint"] is None
    assert by_id["stage6-DL5190.3-p14-page"]["decision"] == "accepted"

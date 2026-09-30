"""HAF103 folios must not hide body text or cross-page source fragments."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.build_stage6_golden_evidence import (
    _body_spans,
    _exclude_haf103_page_footer,
    _exclude_reviewed_fragments,
    _groups,
)
from scripts.stage6_cross_page_supplements import load_supplements, unit_for_sample_page
from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.documents.page_identity import apply_page_identity
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.evidence import build_evidence


ROOT = Path(__file__).resolve().parents[2]
REGISTERED = "OCR/HAF103核动力厂调试和运行安全规定-印刷页3-34(OCR).pdf"
PDF = ROOT / "var/derived/ocr/HAF103核动力厂调试和运行安全规定-印刷页3-34(OCR).pdf"
EXPECTED_COUNTS = {1: 7, 11: 8, 12: 14, 16: 8, 29: 5, 32: 1}


@pytest.fixture(scope="module")
def source_context():
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    golden = json.loads((ROOT / "data/stage6/stage6_evidence_golden_sample.json").read_text(encoding="utf-8"))
    samples = {row["physical_page"]: row for row in golden["records"] if row["document_key"] == "HAF103"}
    overrides = json.loads((ROOT / "data/stage6/stage6_source_review_overrides.json").read_text(encoding="utf-8"))
    return catalog, samples, overrides, load_supplements()


def _page(catalog, sample):
    number = sample["physical_page"]
    ir = parse_registered_pdf(
        PDF, REGISTERED, catalog, title="HAF103", page_indices=(number - 1,),
        parser_version="test-stage6-haf-footer-v1",
    )
    return apply_page_identity(ir, {number: sample["logical_page"]})


@pytest.mark.parametrize("number", sorted(EXPECTED_COUNTS))
def test_haf_footer_is_separate_from_every_meaningful_span(source_context, number):
    catalog, samples, overrides, cross = source_context
    sample = samples[number]
    ir = _page(catalog, sample)
    original_spans = _body_spans(ir)
    body = _exclude_haf103_page_footer(sample, original_spans, overrides)
    assert len(original_spans) - len(body) == 1
    groups = _groups(body, ocr=True)
    assert {sid for group in groups for sid in group} == {span.source_span_id for span in body}
    drafts = [build_evidence(ir, group, evidence_role="requirement_source", disposition="structured")
              for group in groups]
    kept_groups, kept = _exclude_reviewed_fragments(sample, groups, drafts, overrides, cross)
    assert len(kept) == EXPECTED_COUNTS[number]
    assert len(groups) - len(kept_groups) == (0 if number == 12 else 1)
    if number != 12:
        source_unit = unit_for_sample_page(cross, "HAF103", number)
        excluded = next(draft for draft in drafts if draft not in kept)
        fragment = next(part for part in source_unit["fragments"] if part["physical_page"] == number)
        assert "".join(excluded.source_text.split()) == "".join(fragment["source_text"].split())
    assert all(f"— {sample['logical_page']} —" not in item.source_text for item in kept)


def test_cross_page_exclusion_cannot_hide_changed_haf_body(source_context):
    catalog, samples, overrides, cross = source_context
    sample = samples[1]
    ir = _page(catalog, sample)
    groups = _groups(_exclude_haf103_page_footer(sample, _body_spans(ir), overrides), ocr=True)
    drafts = [build_evidence(ir, group, evidence_role="requirement_source", disposition="structured")
              for group in groups]
    tampered = copy.deepcopy(cross)
    unit = unit_for_sample_page(tampered, "HAF103", 1)
    unit["fragments"][0]["source_text"] = "different body text"
    with pytest.raises(ValueError, match="differs from reviewed cross-page source"):
        _exclude_reviewed_fragments(sample, groups, drafts, overrides, tampered)


def test_footer_review_fails_closed_when_its_text_binding_changes(source_context):
    catalog, samples, overrides, _ = source_context
    sample = samples[12]
    changed = copy.deepcopy(overrides)
    row = next(row for row in changed["source_fragment_exclusions"]
               if row["document_key"] == "HAF103" and row["physical_page"] == 12)
    row["expected_text_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="printed footer review differs"):
        _exclude_haf103_page_footer(sample, _body_spans(_page(catalog, sample)), changed)

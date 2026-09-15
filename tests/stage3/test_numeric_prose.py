"""Regression cases use only public synthetic knowledge, never blind questions."""

from dataclasses import replace
from pathlib import Path

import pytest

from turbine_kg.stage3.corpus import load_corpus
from turbine_kg.stage3.models import Claim, ScopeContext
from turbine_kg.stage3.validation import _numbers_supported, validate_claim


ROOT = Path(__file__).resolve().parents[2]


def _case():
    corpus = load_corpus(ROOT / "tests/fixtures/stage3/corpus.json")
    statement = corpus[0].statements[0]
    claim = Claim(
        "numeric-regression", "fact", statement.text, statement.statement_id,
        statement.evidence_ids, statement.object_id,
        ScopeContext.from_dict(statement.scope.as_dict()),
    )
    return corpus, claim


@pytest.mark.parametrize("text", [
    "Cold shaft alignment uses a 0.20 mm limit.",
    "Cold shaft alignment uses a 0.10 inch limit.",
    "Cold shaft alignment uses a 0.10 ft limit.",
    "Cold shaft alignment uses a 0.10 英寸 limit.",
    "Cold shaft alignment uses a 0.10 mm² limit.",
    "Cold shaft alignment uses a 0.10 毫米/秒 limit.",
    "Cold shaft alignment uses a 0.10 mm limit and a 9.9 mm adjustment.",
    "Cold shaft alignment uses a -0.10 mm limit.",
    "Cold shaft alignment uses a 0.10000001 mm limit.",
])
def test_prose_cannot_bypass_checks_by_omitting_numeric_fields(text):
    corpus, claim = _case()
    result = validate_claim(replace(claim, text=text), corpus)
    assert not result.allowed
    assert "claim_prose_quantity_not_supported" in result.failures


@pytest.mark.parametrize("text", [
    "Cold shaft alignment uses a 0.10 mm limit.",
    "Cold shaft alignment uses a .1 mm limit.",
    "Cold shaft alignment uses a 1e-1 mm limit.",
    "Cold shaft alignment uses a ０.１０ mm limit.",
    "Cold shaft alignment uses a 0.10毫米 limit.",
    "Cold shaft alignment uses the stated limit.",
])
def test_supported_prose_and_nonnumeric_claims_remain_allowed(text):
    corpus, claim = _case()
    assert validate_claim(replace(claim, text=text), corpus).allowed


def test_range_metadata_cannot_certify_itself_in_claim_text():
    corpus, claim = _case()
    result = validate_claim(replace(
        claim, text="Cold shaft alignment uses a 0.10 to 0.20 mm limit.",
        quantities=((0.1, 0.2, "mm"),),
    ), corpus)
    assert not result.allowed
    assert "claim_ranges_not_in_evidence" in result.failures


def test_claim_cannot_borrow_unbound_statement_number():
    corpus, claim = _case()
    doc = corpus[0]
    statement = replace(doc.statements[0], text=doc.statements[0].text + " Record 9.9 mm.")
    result = validate_claim(replace(claim, text=statement.text), (replace(doc, statements=(statement,)),))
    assert not result.allowed
    assert "claim_prose_quantity_not_supported" in result.failures


@pytest.mark.parametrize("claim,source,allowed", [
    ("0.2 mm", "0.2～0.5mm", True),
    ("-0.2 mm", "−0.2 mm", True),
    ("0.2 cm", "0.2～0.5mm", False),
    ("75%", "75％", True),
    ("-0.2 mm", "0.2 mm", False),
])
def test_quantity_notation_preserves_sign_range_units_and_percent(claim, source, allowed):
    assert _numbers_supported(claim, source) is allowed

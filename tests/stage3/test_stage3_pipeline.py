import json
from dataclasses import replace
from pathlib import Path

from turbine_kg.stage3.corpus import load_corpus
from turbine_kg.stage3.cli import _with_chinese_display_names
from turbine_kg.stage3.models import Claim, ScopeContext
from turbine_kg.stage3.pipeline import answer_question
from turbine_kg.stage3.projection import build_traceability_projection, projected_retrieve
from turbine_kg.stage3.retrieval import json_baseline, retrieve
from turbine_kg.stage3.validation import validate_claim


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "stage3"


def test_public_question_matrix_has_twenty_cases():
    cases = json.loads((FIXTURE_ROOT / "questions.json").read_text(encoding="utf-8"))
    assert len(cases) == 20
    assert {case["expected"] for case in cases} >= {"validated", "evidence_gap", "downgraded_candidate", "conflict"}
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    for case in cases:
        result = answer_question(
            corpus,
            primary_question=case["question"],
            context=ScopeContext.from_dict(case["context"]),
            high_risk_action=case["id"] == "q20",
        )
        assert result["status"] == case["expected"], case["id"]


def test_end_to_end_answer_and_evidence_gap():
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    context = ScopeContext.from_dict({"model": "N-300", "equipment": "steam_turbine", "lifecycle_stage": "installation", "activity": "alignment", "operating_state": "cold", "capacity_range": 300, "condition": "shaft_alignment"})
    answer = answer_question(corpus, primary_question="What is the manufacturer cold shaft alignment limit?", context=context)
    assert answer["status"] == "validated"
    assert answer["claims"][0]["evidence_ids"]
    gap = answer_question(corpus, primary_question="What applies to a condenser?", context=ScopeContext.from_dict({"equipment": "condenser", "lifecycle_stage": "installation", "activity": "alignment"}))
    assert gap["status"] == "evidence_gap"
    assert not gap["claims"]


def test_missing_context_returns_a_conditional_reference_instead_of_stopping_retrieval():
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    context = ScopeContext.from_dict({"equipment": "steam_turbine", "lifecycle_stage": "installation", "activity": "alignment", "operating_state": "cold"})
    answer = answer_question(corpus, primary_question="What should happen after coupling fit?", context=context)
    assert answer["status"] == "conditional_reference"
    assert answer["claims"]
    assert any(item.startswith("missing_context:") for item in answer["claims"][0]["validation"]["warnings"])


def test_cli_output_adds_chinese_display_names_without_replacing_machine_ids():
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    context = ScopeContext.from_dict({"model": "N-300", "equipment": "steam_turbine", "lifecycle_stage": "installation", "activity": "alignment", "operating_state": "cold", "capacity_range": 300, "condition": "shaft_alignment"})
    result = _with_chinese_display_names(
        answer_question(corpus, primary_question="What is the manufacturer cold shaft alignment limit?", context=context)
    )
    assert result["claims"][0]["claim_type"] == "fact"
    assert result["claims"][0]["claim_type_display_name"] == "事实结论"
    assert result["retrieval"]["source_role"] == "manufacturer_manual"
    assert result["retrieval"]["source_role_display_name"] == "制造商说明书"


def test_plain_limit_question_does_not_silently_choose_between_sources():
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    context = ScopeContext.from_dict({"model": "N-300", "equipment": "steam_turbine", "lifecycle_stage": "installation", "activity": "alignment", "operating_state": "cold", "capacity_range": 300, "condition": "shaft_alignment"})
    answer = answer_question(corpus, primary_question="What is the cold shaft alignment limit?", context=context)
    assert answer["status"] == "conflict"


def test_conflict_is_visible_in_baseline_candidates():
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    context = ScopeContext.from_dict({"model": "N-300", "equipment": "steam_turbine", "lifecycle_stage": "installation", "activity": "alignment", "operating_state": "cold", "capacity_range": 300, "condition": "shaft_alignment"})
    hits = retrieve(corpus, question="alignment value and unit", context=context)
    assert {hit.statement.statement_id for hit in hits} >= {"statement-111-1", "statement-222-1"}
    assert json_baseline(corpus, question="alignment value and unit", context=context) == tuple(hit.statement.statement_id for hit in hits)


def test_baseline_and_projected_retrieval_keep_conditional_hits_when_context_is_incomplete():
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    context = ScopeContext.from_dict({"equipment": "steam_turbine", "lifecycle_stage": "installation", "activity": "alignment", "operating_state": "cold"})
    baseline = json_baseline(corpus, question="What should happen after coupling fit?", context=context)
    projected = projected_retrieve(build_traceability_projection(corpus), question="What should happen after coupling fit?", context=context)
    assert baseline == projected
    assert baseline == ("statement-111-2",)


def test_high_risk_claim_is_downgraded_not_authorized():
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    context = ScopeContext.from_dict({"equipment": "auxiliary_pump", "lifecycle_stage": "installation", "activity": "inspection", "condition": "visual_check"})
    answer = answer_question(corpus, primary_question="Can this evidence authorize lifting?", context=context, high_risk_action=True)
    assert answer["status"] == "downgraded_candidate"
    assert "not_authorized_for_execution" in answer["validation"]["warnings"]


def test_conflict_question_keeps_both_candidate_values_visible():
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    context = ScopeContext.from_dict({"model": "N-300", "equipment": "steam_turbine", "lifecycle_stage": "installation", "activity": "alignment", "operating_state": "cold", "capacity_range": 300, "condition": "shaft_alignment"})
    answer = answer_question(corpus, primary_question="Compare the manufacturer and generic baseline values.", context=context)
    assert answer["status"] == "conflict"
    assert {"statement-111-1", "statement-222-1"} <= set(answer["retrieval"]["candidate_statement_ids"])
    assert {0.1, 0.15} == {claim["value"] for claim in answer["claims"]}


def test_numeric_question_does_not_silently_choose_between_conflicting_values():
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    context = ScopeContext.from_dict({"model": "N-300", "equipment": "steam_turbine", "lifecycle_stage": "installation", "activity": "alignment", "operating_state": "cold", "capacity_range": 300, "condition": "shaft_alignment"})
    answer = answer_question(corpus, primary_question="What is the alignment value and unit?", context=context)
    assert answer["status"] == "conflict"
    assert {0.1, 0.15} == {claim["value"] for claim in answer["claims"]}


def test_claim_text_and_evidence_values_must_be_supported():
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    context = ScopeContext.from_dict({"model": "N-300", "equipment": "steam_turbine", "lifecycle_stage": "installation", "activity": "alignment", "operating_state": "cold", "capacity_range": 300, "condition": "shaft_alignment"})
    unsupported_text = Claim("claim-unsupported", "fact", "This unsupported conclusion is authorized.", "statement-111-1", ("evidence-111-1",), "steam_turbine_shaft", context, 0.1, "mm")
    text_result = validate_claim(unsupported_text, corpus)
    assert not text_result.allowed
    assert "claim_text_not_supported" in text_result.failures

    original = corpus[0]
    tampered_evidence = replace(original.evidence[0], value=9.9, unit="inch")
    tampered_document = replace(original, evidence=(tampered_evidence, *original.evidence[1:]))
    tampered_corpus = (tampered_document, *corpus[1:])
    consistent_claim = Claim("claim-tampered", "fact", original.statements[0].text, "statement-111-1", ("evidence-111-1",), "steam_turbine_shaft", context, 0.1, "mm")
    value_result = validate_claim(consistent_claim, tampered_corpus)
    assert not value_result.allowed
    assert "evidence_value_mismatch:evidence-111-1" in value_result.failures
    assert "evidence_unit_mismatch:evidence-111-1" in value_result.failures

    numeric_text_claim = replace(consistent_claim, text="Cold shaft alignment uses a 0.20 mm limit.")
    numeric_text_result = validate_claim(numeric_text_claim, corpus)
    assert not numeric_text_result.allowed
    assert "claim_quantity_not_in_text" in numeric_text_result.failures

    tampered_span = replace(original.spans[0], quote="Unrelated inspection text.")
    span_document = replace(original, spans=(tampered_span, *original.spans[1:]))
    span_result = validate_claim(consistent_claim, (span_document, *corpus[1:]))
    assert not span_result.allowed
    assert "evidence_text_not_supported:evidence-111-1" in span_result.failures

    negated = replace(consistent_claim, text="Cold shaft alignment does not use a 0.10 mm limit.")
    negation_result = validate_claim(negated, corpus)
    assert not negation_result.allowed
    assert "negation_mismatch" in negation_result.failures


def test_claim_validator_rejects_object_and_unit_mismatch():
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    context = ScopeContext.from_dict({"model": "N-300", "equipment": "steam_turbine", "lifecycle_stage": "installation", "activity": "alignment", "operating_state": "cold", "capacity_range": 300, "condition": "shaft_alignment"})
    claim = Claim("claim-bad", "fact", "bad", "statement-111-1", ("evidence-111-1",), "wrong_object", context, 0.1, "inch")
    result = validate_claim(claim, corpus)
    assert not result.allowed
    assert "object_mismatch:statement" in result.failures
    assert "unit_mismatch" in result.failures

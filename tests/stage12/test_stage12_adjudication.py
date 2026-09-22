from __future__ import annotations

import copy

from turbine_kg.extraction.semantic import compare_candidates
from scripts.audit_stage12_exit import _development_quality_gate, _reserve_acceptance_gate


def _candidate(candidate_id: str, text: str, *, predicate: str = "requires") -> dict:
    return {
        "candidate_id": candidate_id,
        "statement_text": text,
        "statement_type": "requirement",
        "predicate": predicate,
        "relation_direction": "subject_to_object" if predicate == "requires" else "cause_to_effect",
        "subject_entities": [{"surface_form": text[:2], "role": "subject", "entity_class": "candidate"}],
        "object_value": {"kind": "source_assertion", "value": text},
        "quantities": [],
        "negation_scope": [],
        "conditions": [],
        "applicability_scope": {"status": "unknown"},
        "evidence_bindings": [{"evidence_id": "e-1", "support_type": "direct"}],
    }


def _gold(statement_id: str, text: str, *, predicate: str = "requires") -> dict:
    return {
        "statement_id": statement_id,
        "statement_text": text,
        "statement_type": "requirement",
        "predicate": predicate,
        "entity_alignment": [{"surface_form": text[:2]}],
        "quantities": [],
        "negation_scope": [],
        "conditions": [],
        "applicability_scope": {},
        "evidence_bindings": [{"evidence_id": "e-1", "support_type": "direct"}],
    }


def _decision(gold_id: str | None, candidate_id: str | None, decision: str, *, coverage: bool = True, critical: bool = False) -> dict:
    return {
        "disagreement_id": f"d-{gold_id or candidate_id}",
        "gold_statement_id": gold_id,
        "candidate_id": candidate_id,
        "evidence_id": "e-1",
        "decision": decision,
        "subtype": "synthetic",
        "reason": "synthetic evaluator regression",
        "critical": critical,
        "information_coverage": coverage,
    }


def test_merged_candidate_can_cover_two_gold_statements():
    gold = [_gold("g1", "甲完成"), _gold("g2", "乙完成")]
    merged = _candidate("c1", "甲完成，乙完成")
    adjudication = {"artifact_kind": "synthetic", "decisions": [_decision("g1", "c1", "D"), _decision("g2", "c1", "D")]}
    report = compare_candidates([merged], gold, adjudication=adjudication)
    assert report["adjudicated_information_coverage"] == 1.0
    assert report["adjudicated_disagreement_summary"]["acceptable_semantic_equivalence_count"] == 1
    assert report["adjudication_validation"]["pending_review_count"] == 0


def test_entity_granularity_difference_can_be_accepted_without_model_error():
    gold = [_gold("g1", "密封瓦座垫片材质应符合制造厂技术要求")]
    candidate = _candidate("c1", gold[0]["statement_text"])
    candidate["subject_entities"] = [{"surface_form": "密封瓦座垫片", "role": "subject", "entity_class": "candidate"}, {"surface_form": "制造厂", "role": "related", "entity_class": "candidate"}]
    adjudication = {"artifact_kind": "synthetic", "decisions": [_decision("g1", "c1", "D")]}
    report = compare_candidates([candidate], gold, adjudication=adjudication)
    assert report["adjudicated_disagreement_summary"]["confirmed_model_error_count"] == 0
    assert report["adjudicated_information_coverage"] == 1.0


def test_missing_causal_hop_is_noncritical_information_gap():
    gold = [_gold("g1", "甲导致乙", predicate="causes"), _gold("g2", "乙导致丙", predicate="causes")]
    candidates = [_candidate("c1", "甲导致乙", predicate="causes"), _candidate("c2", "甲导致丙", predicate="causes")]
    adjudication = {"artifact_kind": "synthetic", "decisions": [_decision("g1", "c1", "D"), _decision("g2", "c2", "A", coverage=False)]}
    report = compare_candidates(candidates, gold, adjudication=adjudication)
    summary = report["adjudicated_disagreement_summary"]
    assert summary["confirmed_noncritical_model_error_count"] == 1
    assert summary["confirmed_critical_model_error_count"] == 0
    assert report["adjudicated_information_coverage"] == 0.5


def test_unsupported_candidate_remains_hard_safety_failure():
    evidence = {"evidence_id": "e-1", "effective_text": "甲完成", "source_text": "甲完成", "document_logical_id": "d", "revision_id": "r", "physical_page": 1, "source_span_id": "s", "review_status": "accepted", "evidence_status": "accepted"}
    candidate = _candidate("c1", "乙完成")
    gold = [_gold("g1", "乙完成")]
    report = compare_candidates([candidate], gold, evidence_by_id={"e-1": evidence})
    assert report["safety_metrics"]["unsupported_addition_count"] > 0 or report["evidence_semantic_support"]["accuracy"] == 0.0


def test_critical_quantity_negation_and_direction_metrics_are_not_adjudicated_away():
    candidate = _candidate("c1", "甲导致乙", predicate="causes")
    gold = [_gold("g1", "甲导致乙", predicate="causes")]
    wrong = copy.deepcopy(candidate)
    wrong["relation_direction"] = "effect_to_cause"
    report = compare_candidates([wrong], gold)
    assert report["field_accuracy"]["relation_direction"] == 0.0


def test_development_gate_uses_information_coverage_and_noncritical_tolerance():
    development = {
        "safety_metrics": {"evidence_binding_accuracy": 1.0, "evidence_semantic_support_accuracy": 1.0, "unsupported_addition_count": 0, "unsupported_candidate_count": 0, "critical_quantity_mismatch_count": 0, "critical_polarity_error_count": 0, "critical_relation_direction_error_count": 0, "critical_omission_count": 0, "critical_error_row_count": 0},
        "coverage_metrics": {"matched_candidate_precision": 1.0, "over_split_count": 0},
        "matched_field_accuracy": {"statement_type": 1.0, "relation": 1.0, "quantity": 1.0, "negation": 1.0, "relation_direction": 1.0},
        "adjudicated_information_coverage": 0.975,
        "adjudicated_disagreement_summary": {"confirmed_critical_model_error_count": 0, "confirmed_noncritical_model_error_rate": 0.025, "pending_review_count": 0},
        "adjudication_validation": {"pending_review_count": 0, "extra_adjudication_count": 0},
    }
    policy = {"hard_safety": development["safety_metrics"], "coverage": {"adjudicated_information_coverage": 0.95, "matched_candidate_precision": 0.9, "max_over_split_count": 3}, "matched_quality": development["matched_field_accuracy"], "adjudication": {"max_confirmed_critical_model_errors": 0, "max_confirmed_noncritical_model_error_rate": 0.1, "max_pending_review_count": 0}}
    passed, details = _development_quality_gate(development, policy)
    assert passed is True
    assert details["coverage"]["adjudicated_information_coverage"] is True


def test_reserve_gate_does_not_require_exact_entity_or_applicability_fields():
    policy = {
        "hard_safety": {"evidence_binding_accuracy": 1.0, "unsupported_addition_count": 0},
        "information_coverage": {"adjudicated_information_coverage": 0.95},
        "supported_candidate_quality": {"evidence_semantic_support_accuracy": 1.0, "unsupported_addition_count": 0},
        "adjudication": {"max_confirmed_critical_model_errors": 0, "max_confirmed_noncritical_model_error_rate": 0.1, "max_pending_review_count": 0},
    }
    reserve = {"status": "completed", "eligible_for_final_acceptance": True, "safety_metrics": {"evidence_binding_accuracy": 1.0, "unsupported_addition_count": 0, "evidence_semantic_support_accuracy": 1.0}, "adjudicated_information_coverage": 0.98, "adjudicated_disagreement_summary": {"confirmed_critical_model_error_count": 0, "confirmed_noncritical_model_error_rate": 0.02, "pending_review_count": 0}}
    assert _reserve_acceptance_gate(True, reserve, policy) is True

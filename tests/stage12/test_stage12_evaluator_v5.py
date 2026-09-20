"""Synthetic, Development-independent regressions for the Stage 12 v5 evaluator."""

from turbine_kg.extraction.semantic import (
    _applicability_match,
    _classify_unmatched_candidate,
    _entity_match,
    _maximum_gold_candidate_matching,
    _relation_direction_match,
)


def _binding(evidence_id: str = "synthetic-evidence") -> list[dict]:
    return [{"evidence_id": evidence_id, "support_type": "direct"}]


def _candidate(candidate_id: str, text: str, **overrides) -> dict:
    row = {
        "candidate_id": candidate_id,
        "statement_text": text,
        "statement_type": "requirement",
        "predicate": "requires",
        "relation_direction": "subject_to_object",
        "subject_entities": [{"surface_form": "设备", "role": "subject", "entity_class": "candidate"}],
        "conditions": [],
        "quantities": [],
        "negation_scope": [],
        "applicability_scope": {"status": "unknown"},
        "document_logical_id": "synthetic-document",
        "physical_page": 1,
        "evidence_bindings": _binding(),
    }
    row.update(overrides)
    return row


def _gold(statement_id: str, text: str, **overrides) -> dict:
    row = {
        "statement_id": statement_id,
        "statement_text": text,
        "statement_type": "requirement",
        "predicate": "requires",
        "entity_alignment": [{"surface_form": "设备", "role": "subject"}],
        "conditions": [],
        "quantities": [],
        "negation_scope": [],
        "applicability_scope": {},
        "document_logical_id": "synthetic-document",
        "physical_page": 1,
        "evidence_bindings": _binding(),
    }
    row.update(overrides)
    return row


def test_maximum_matching_is_one_to_one_and_does_not_reuse_a_candidate():
    candidates = [
        _candidate("candidate-a", "设备应检查密封。"),
        _candidate("candidate-b", "设备应检查振动。"),
    ]
    gold = [
        _gold("gold-a", "设备应检查密封。"),
        _gold("gold-b", "设备应检查振动。"),
    ]

    matches = _maximum_gold_candidate_matching(candidates, gold)

    assert {index: row["candidate_id"] for index, row in matches.items()} == {0: "candidate-a", 1: "candidate-b"}
    assert len({row["candidate_id"] for row in matches.values()}) == len(matches)


def test_reference_only_ignores_only_terminal_punctuation_and_whitespace():
    gold = _gold(
        "reference-gold",
        "其他类型设备可参照本规定执行。",
        statement_type="limitation",
        predicate="limits_scope",
        applicability_scope={"status": "reference_only"},
    )
    candidate = _candidate(
        "reference-candidate",
        "  其他类型设备可参照本规定执行  ",
        statement_type="limitation",
        predicate="limits_scope",
        relation_direction="scope_to_subject",
    )
    assert _applicability_match(candidate, gold)

    candidate["statement_text"] = "其他类型设备应直接执行本规定"
    assert not _applicability_match(candidate, gold)


def test_relation_direction_uses_structured_gold_conditions():
    gold = _gold(
        "conditional-gold",
        "设备应启动。",
        conditions=[{"surface_form": "润滑压力稳定", "kind": "condition"}],
    )
    candidate = _candidate(
        "conditional-candidate",
        "设备应启动。",
        relation_direction="condition_to_consequence",
        conditions=[{"surface_form": "润滑压力稳定", "kind": "condition"}],
    )
    assert _relation_direction_match(candidate, gold)


def test_relation_direction_preserves_explicit_procedure_order():
    gold = _gold(
        "procedure-gold",
        "先关闭入口阀，然后打开排放阀。",
        statement_type="procedure",
        predicate="describes",
    )
    candidate = _candidate(
        "procedure-candidate",
        "先关闭入口阀，然后打开排放阀。",
        statement_type="procedure",
        predicate="describes",
        relation_direction="procedure_order",
    )
    assert _relation_direction_match(candidate, gold)


def test_entity_match_requires_complete_role_compatible_gold_coverage():
    gold = _gold(
        "multi-entity-gold",
        "泵与电机应完成对中。",
        entity_alignment=[
            {"surface_form": "泵", "role": "subject"},
            {"surface_form": "电机", "role": "object"},
        ],
    )
    complete = _candidate(
        "complete-candidate",
        gold["statement_text"],
        subject_entities=[
            {"surface_form": "泵", "role": "subject", "entity_class": "candidate"},
            {"surface_form": "电机", "role": "related", "entity_class": "candidate"},
        ],
    )
    missing = _candidate(
        "missing-candidate",
        gold["statement_text"],
        subject_entities=[{"surface_form": "泵", "role": "subject", "entity_class": "candidate"}],
    )

    assert _entity_match(complete, gold)
    assert not _entity_match(missing, gold)


def test_entity_match_retains_canonical_id_to_grounded_alias_adapter():
    gold = _gold(
        "canonical-gold",
        "主泵应保持稳定。",
        entity_alignment=[{"surface_form": "reactor_coolant_pump", "role": "subject"}],
    )
    candidate = _candidate(
        "alias-candidate",
        gold["statement_text"],
        subject_entities=[{"surface_form": "主泵", "role": "subject", "entity_class": "candidate"}],
    )
    assert _entity_match(candidate, gold)


def test_over_split_requires_same_evidence_and_strict_fragment_relation():
    matched = _candidate("matched", "执行检查，以确认接触面均匀连续。")
    fragment = _candidate("fragment", "确认接触面均匀连续。")
    unrelated_binding = _candidate(
        "other-evidence-fragment",
        "确认接触面均匀连续。",
        evidence_bindings=_binding("other-evidence"),
    )

    assert _classify_unmatched_candidate("fragment", [matched, fragment], {0: matched}, []) == "over_split"
    assert (
        _classify_unmatched_candidate(
            "other-evidence-fragment",
            [matched, unrelated_binding],
            {0: matched},
            [],
        )
        == "needs_gold_completion"
    )

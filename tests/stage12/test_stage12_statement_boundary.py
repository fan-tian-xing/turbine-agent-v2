import json
from pathlib import Path

from scripts.apply_stage12_manual_adjudication import _dedupe_preserving_order
from turbine_kg.extraction.semantic import (
    ExtractionProfile,
    HeuristicSemanticExtractor,
    _assemble_candidate,
    _classify_unmatched_candidate,
    to_stage9_runtime_payload,
    validate_candidate_semantics,
)


ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "data/stage11/stage11_statement_development_samples.jsonl"
PROMPT = ROOT / "config/stage12_prompt.txt"


def _gold_rows():
    return [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_current_gold_uses_canonical_semantically_isomorphic_delivery_statement():
    rows = _gold_rows()
    ids = {row["statement_id"] for row in rows}
    canonical = next(row for row in rows if row["statement_id"] == "stage12-gold-delivery-07")
    assert "stage12-gold-delivery-08" not in ids
    assert "stage12-gold-delivery-09" not in ids
    assert len(canonical["entity_alignment"]) == 3
    assert {item["surface_form"] for item in canonical["entity_alignment"]} == {
        "厂房内各基础的纵横中心线",
        "厂房内各基础的标高标识",
        "厂房内各基础的基础沉降观测点",
    }


def test_current_gold_negation_is_source_grounded():
    rows = {row["statement_id"]: row for row in _gold_rows()}
    assert rows["stage12-gold-seal-vertical-joint-flatness"]["negation_scope"] == [
        {"surface_form": "无错口", "polarity": "negative", "scope_type": "statement"}
    ]
    assert rows["stage12-gold-seal-axial-paint-check"]["negation_scope"] == []


def test_prompt_states_semantic_boundary_regression_examples():
    prompt = PROMPT.read_text(encoding="utf-8")
    assert "semantically isomorphic" in prompt
    assert "substantive difference" in prompt
    assert "never decide the boundary by themselves" in prompt
    assert "multiple entities" in prompt
    assert "independently retrievable" in prompt
    assert "60kPa左右" not in prompt
    assert "0.2～0.5mm" not in prompt
    assert "D300-style" not in prompt
    assert "additional gating prerequisite" in prompt


def test_causal_antecedent_is_not_duplicated_as_condition():
    text = "真空过低会给汽缸和转子造成较大的热冲击。"
    evidence = {
        "evidence_id": "boundary-causal",
        "document_logical_id": "fixture",
        "revision_id": "revision",
        "physical_page": 1,
        "source_span_id": "span",
        "source_text_sha256": "x",
        "source_text": text,
        "review_status": "accepted",
    }
    candidate = HeuristicSemanticExtractor().extract(evidence)[0]
    assert candidate["predicate"] == "causes"
    assert candidate["relation_direction"] == "cause_to_effect"
    assert candidate["conditions"] == []


def test_exclusion_negation_is_preserved_deterministically():
    evidence = {
        "evidence_id": "exclusion-evidence", "document_logical_id": "fixture", "revision_id": "revision",
        "physical_page": 1, "source_span_id": "span", "source_text_sha256": "x",
        "source_text": "本规定不涉及工业安全。", "review_status": "accepted",
    }
    candidate = HeuristicSemanticExtractor().extract(evidence)[0]
    assert {item["surface_form"] for item in candidate["negation_scope"]} == {"不涉及"}


def test_negation_preserves_source_phrase_after_marker():
    evidence = _synthetic_evidence("结合面应平整、无错口。")
    candidate = HeuristicSemanticExtractor().extract(evidence)[0]
    assert {item["surface_form"] for item in candidate["negation_scope"]} == {"无错口"}


def test_gold_audit_keeps_local_condition_and_activity_applicability_distinct():
    rows = {row["statement_id"]: row for row in _gold_rows()}
    staged = rows["stage11-statement-5740cf642266fcd6feb4"]
    assert staged["applicability_scope"]["applicability_text"] == "当调试活动分阶段实施时"
    assert staged["conditions"][0]["surface_form"].endswith("监查，并确认调试结果评价满足了全部核安全管理要求之后")
    assert rows["stage12-gold-seal-axial-paint-check"]["conditions"] == []


def test_gold_parallel_entities_are_not_collapsed_into_compound_nouns():
    rows = {row["statement_id"]: row for row in _gold_rows()}
    assert {item["surface_form"] for item in rows["stage12-gold-oil-cleanliness"]["entity_alignment"]} == {"油室", "油孔"}
    assert {item["surface_form"] for item in rows["stage12-gold-delivery-03"]["entity_alignment"]} == {"主辅设备基础", "基座混凝土"}
    assert {item["surface_form"] for item in rows["stage12-gold-delivery-10"]["entity_alignment"]} == {"各层平台", "通道", "梯子", "栏杆", "踢脚板"}
    assert {item["surface_form"] for item in rows["stage11-statement-a9ff60d2526222447813-s5"]["entity_alignment"]} >= {"汽缸", "转子"}
    assert "相关过程" not in rows["stage11-statement-a9ff60d2526222447813-s5"]["statement_text"]


def test_gold_scope_exclusion_and_negation_surfaces_are_source_grounded():
    rows = {row["statement_id"]: row for row in _gold_rows()}
    assert {item["surface_form"] for item in rows["stage12-gold-oil-cleanliness"]["negation_scope"]} == {"无铁屑", "无锈皮等杂物"}
    assert {item["surface_form"] for item in rows["stage12-gold-gasket-undamaged"]["negation_scope"]} == {"无破损"}
    assert {item["surface_form"] for item in rows["real-statement-haf103-p1-scope"]["negation_scope"]} == {"不涉及"}


def test_unmatched_substatement_is_classified_as_over_split():
    base = {"candidate_id": "base", "statement_text": "perform inspection and ensure the contact surface is uniform and continuous"}
    extra = {"candidate_id": "extra", "statement_text": "ensure the contact surface is uniform and continuous"}
    assert _classify_unmatched_candidate("extra", [base, extra], {0: base}, []) == "over_split"


def test_unmatched_same_evidence_fragment_is_over_split_even_with_connector_variation():
    base = {
        "candidate_id": "base",
        "statement_text": "when inspection is required, withdraw the upper slide block and clean it thoroughly",
        "evidence_bindings": [{"evidence_id": "evidence-synthetic", "support_type": "direct"}],
    }
    fragment = {
        "candidate_id": "fragment",
        "statement_text": "when inspection is required, withdraw the upper slide block",
        "evidence_bindings": [{"evidence_id": "evidence-synthetic", "support_type": "direct"}],
    }
    assert _classify_unmatched_candidate("fragment", [base, fragment], {0: base}, []) == "over_split"


def test_manual_adjudication_rule_updates_are_idempotent():
    rules = ["base", "merge rule", "base"]
    assert _dedupe_preserving_order(_dedupe_preserving_order(rules)) == ["base", "merge rule"]

    artifact = json.loads((ROOT / "data/stage12/stage12_development_gold_adjudication.json").read_text(encoding="utf-8"))
    applied = artifact["general_rules_applied"]
    assert len(applied) == len(set(applied))


def _synthetic_evidence(text):
    return {
        "evidence_id": "synthetic-semantic-evidence",
        "document_logical_id": "synthetic-document",
        "revision_id": "synthetic-revision",
        "physical_page": 1,
        "logical_page": "1",
        "source_span_id": "synthetic-span",
        "source_span_ids": ["synthetic-span"],
        "source_text_sha256": "synthetic-sha",
        "source_text": text,
        "review_status": "accepted",
        "document_key": "synthetic",
    }


def _synthetic_profile():
    return ExtractionProfile(
        semantic_role="synthetic",
        extraction_profile_id="synthetic_profile",
        source_profile_id="synthetic",
        source_applicability_scope=(),
    )


def _assemble_synthetic(item, evidence):
    candidate = _assemble_candidate(item, evidence, _synthetic_profile(), "synthetic", 0)
    validate_candidate_semantics(candidate)
    return candidate


def test_pure_causal_antecedent_has_no_forced_condition_and_reaches_stage9():
    text = "若润滑压力过低，会导致轴承温度升高。"
    candidate = HeuristicSemanticExtractor().extract(_synthetic_evidence(text))[0]
    validate_candidate_semantics(candidate)
    assert candidate["predicate"] == "causes"
    assert candidate["relation_direction"] == "cause_to_effect"
    assert candidate["conditions"] == []
    to_stage9_runtime_payload([candidate])


def test_causal_fragment_can_use_explicit_causal_context_from_same_evidence():
    evidence = _synthetic_evidence("若润滑压力过低，会导致轴承温度升高，设备停机。")
    candidate = _assemble_synthetic(
        {
            "statement_text": "轴承温度升高，设备停机。",
            "statement_type": "fact",
            "predicate": "causes",
            "subject_entities": [{"surface_form": "轴承温度", "role": "subject"}, {"surface_form": "设备", "role": "object"}],
            "conditions": [],
            "applicability_scope": {"status": "unknown"},
        },
        evidence,
    )
    assert candidate["predicate"] == "causes"


def test_applicability_scope_is_not_forced_into_condition():
    text = "在设备首次投运阶段，应记录全部初始参数。"
    candidate = _assemble_synthetic(
        {
            "statement_text": text,
            "statement_type": "requirement",
            "predicate": "requires",
            "subject_entities": [{"surface_form": "设备", "role": "subject"}],
            "conditions": [],
            "applicability_scope": {"status": "known", "applicability_text": "在设备首次投运阶段"},
        },
        _synthetic_evidence(text),
    )
    assert candidate["conditions"] == []
    assert candidate["applicability_scope"]["applicability_text"] == "在设备首次投运阶段"


def test_causal_antecedent_can_coexist_with_genuine_gating_condition():
    text = "在保护系统已投入的前提下，若压力持续下降，会导致设备停机。"
    candidate = _assemble_synthetic(
        {
            "statement_text": text,
            "statement_type": "fact",
            "predicate": "causes",
            "subject_entities": [{"surface_form": "压力", "role": "subject"}, {"surface_form": "设备", "role": "object"}],
            "conditions": [{"surface_form": "保护系统已投入"}],
            "applicability_scope": {"status": "unknown"},
        },
        _synthetic_evidence(text),
    )
    assert candidate["predicate"] == "causes"
    assert [item["surface_form"] for item in candidate["conditions"]] == ["保护系统已投入"]


def test_local_condition_does_not_cross_statement_boundary():
    text = "在完成校准后，传感器 A 应重新检查。传感器 B 应保持清洁。"
    first = _assemble_synthetic(
        {
            "statement_text": "在完成校准后，传感器 A 应重新检查。",
            "statement_type": "requirement",
            "predicate": "requires",
            "subject_entities": [{"surface_form": "传感器 A", "role": "subject"}],
            "conditions": [{"surface_form": "完成校准后"}],
            "applicability_scope": {"status": "unknown"},
        },
        _synthetic_evidence(text),
    )
    second = _assemble_synthetic(
        {
            "statement_text": "传感器 B 应保持清洁。",
            "statement_type": "requirement",
            "predicate": "requires",
            "subject_entities": [{"surface_form": "传感器 B", "role": "subject"}],
            "conditions": [],
            "applicability_scope": {"status": "unknown"},
        },
        _synthetic_evidence(text),
    )
    assert [item["surface_form"] for item in first["conditions"]] == ["完成校准后"]
    assert second["conditions"] == []


def test_activity_scope_and_prerequisite_condition_are_separate_fields():
    text = "当调试分阶段进行时，只有在前阶段验收通过后，才可开始下一阶段。"
    candidate = _assemble_synthetic(
        {
            "statement_text": text,
            "statement_type": "requirement",
            "predicate": "requires",
            "subject_entities": [{"surface_form": "下一阶段", "role": "subject"}],
            "conditions": [{"surface_form": "前阶段验收通过"}],
            "applicability_scope": {"status": "known", "applicability_text": "当调试分阶段进行时"},
        },
        _synthetic_evidence(text),
    )
    assert candidate["applicability_scope"]["applicability_text"] == "当调试分阶段进行时"
    assert [item["surface_form"] for item in candidate["conditions"]] == ["前阶段验收通过"]


def test_ordered_procedure_direction_preserves_after_then_sequence():
    text = "清理滑块槽后将滑块复位。"
    candidate = HeuristicSemanticExtractor().extract(_synthetic_evidence(text))[0]
    assert candidate["relation_direction"] == "procedure_order"


def test_reference_only_scope_matches_limitation_without_direct_applicability():
    from turbine_kg.extraction.semantic import _applicability_match

    text = "其他类型设备可参照本规定执行。"
    candidate = _assemble_synthetic(
        {
            "statement_text": text,
            "statement_type": "limitation",
            "predicate": "limits_scope",
            "subject_entities": [{"surface_form": "其他类型设备", "role": "subject"}, {"surface_form": "本规定", "role": "object"}],
            "conditions": [],
            "applicability_scope": {"status": "unknown"},
        },
        _synthetic_evidence(text),
    )
    gold = {"statement_text": text, "document_logical_id": "synthetic-document", "physical_page": 1, "applicability_scope": {"status": "reference_only"}}
    assert _applicability_match(candidate, gold)

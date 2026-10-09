import hashlib
import json
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

from turbine_kg.extraction.semantic import (
    ExtractionProfile,
    ExtractionProviderError,
    ExtractionSchemaError,
    ExternalLLMProvider,
    FixtureExtractionProvider,
    HeuristicSemanticExtractor,
    ProfileRouter,
    ProviderBackedExtractor,
    _assemble_candidate,
    _model_evidence_view,
    _statement_asserts_question_answer,
    compare_candidates,
    candidate_review_diagnostics,
    parse_provider_response,
    source_units_for_evidence,
    validate_source_unit_coverage,
    to_stage9_runtime_payload,
    validate_candidate_against_evidence,
    validate_candidate_payload,
    _resolved_marked_answer_text,
    _resolved_question_group_text,
)
from turbine_kg.llm_client import OpenAICompatibleChatTransport
from scripts import stage12_failure_summary
from scripts.audit_stage12_exit import _reserve_acceptance_gate, audit
from scripts.build_stage12_semantic_coverage_matrix import build_matrix

ROOT = Path(__file__).resolve().parents[2]


def _read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _evidence(text="真空不得低于60kPa。"):
    return {
        "evidence_id": "fixture-evidence",
        "document_logical_id": "fixture-document",
        "revision_id": "fixture-revision",
        "physical_page": 1,
        "source_span_id": "fixture-span",
        "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "source_text": text,
        "review_status": "accepted",
        "document_key": "fixture",
    }


def _candidate(text="真空不得低于60kPa。"):
    return HeuristicSemanticExtractor().extract(_evidence(text))[0]


def test_contract_is_candidate_only_and_has_provider_boundary():
    contract = _read("config/stage12_statement_contract.json")
    schema = _read("config/stage12_candidate.schema.json")
    assert contract["stage"] == "12" and contract["formal_release"] is False
    assert contract["architecture"]["relation_vocabulary"] == ["requires", "prohibits", "describes", "causes", "verifies", "limits_scope"]
    assert contract["architecture"]["gold_exhaustive"] is False
    assert schema["properties"]["provider_id"]["type"] == "string"


def test_semantic_coverage_matrix_matches_current_gold_and_generator():
    matrix = _read("data/stage12/stage12_semantic_coverage_matrix.json")
    gold_rows = [
        json.loads(line)
        for line in (ROOT / "data/stage11/stage11_statement_development_samples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert matrix == build_matrix()
    assert matrix["gold_statement_count"] == len(gold_rows)
    assert matrix["coverage"]["statement_type"]
    assert matrix["coverage"]["predicate"]
    assert matrix["coverage_claim"] == "descriptive_development_gold_coverage_only"


def test_exit_audit_keeps_exit_decision_without_copying_detailed_artifacts():
    report = audit()

    assert report["checks"]["semantic_coverage_matrix_current"] is True
    assert set(report["gates"]) == {
        "REAL_LLM_SINGLE_CALL_VERIFIED",
        "REAL_LLM_BATCH_EXECUTION",
        "PRODUCTION_LLM_PIPELINE_READY",
        "DEVELOPMENT_QUALITY_GATE",
        "ROBUSTNESS_QUALITY_GATE",
        "INDEPENDENT_ACCEPTANCE",
        "PREFREEZE_READY",
        "STAGE12_EXIT",
    }
    for duplicated_section in (
        "counts",
        "execution_evidence",
        "quality_observation",
        "regression_matrix",
        "stage12_status_summary",
        "real_llm_reexecution_error",
    ):
        assert duplicated_section not in report


def test_exposed_holdout_cannot_satisfy_independent_reserve_acceptance():
    thresholds = {"statement_boundary": 0.9, "negation": 1.0}
    exposed_holdout = {
        "status": "completed",
        "eligible_for_final_acceptance": False,
        "field_accuracy": {"statement_boundary": 1.0, "negation": 1.0},
        "error_counts": {"unsupported_claim": 0},
    }
    assert _reserve_acceptance_gate(False, exposed_holdout, thresholds) is False
    assert _reserve_acceptance_gate(True, exposed_holdout, thresholds) is False


def test_independent_reserve_acceptance_uses_only_frozen_reserve_result():
    thresholds = {"statement_boundary": 0.9, "negation": 1.0}
    reserve = {
        "status": "completed",
        "eligible_for_final_acceptance": True,
        "field_accuracy": {"statement_boundary": 0.9, "negation": 1.0},
        "error_counts": {"unsupported_claim": 0},
    }
    assert _reserve_acceptance_gate(True, reserve, thresholds) is True
    reserve["error_counts"]["unsupported_claim"] = 1
    assert _reserve_acceptance_gate(True, reserve, thresholds) is False


def test_provider_response_parser_is_strict_and_does_not_repair_prose():
    with pytest.raises(ExtractionSchemaError):
        parse_provider_response("```json\n{}\n```")
    response = {"schema_version": 1, "response_kind": "stage12_candidate_extraction", "status": "no_statement", "candidates": [], "source_unit_coverage": [{"source_unit_id": "unit-1", "status": "excluded", "candidate_indexes": [], "reason": "Only a page heading."}], "no_statement_reason": "Only a page heading."}
    assert parse_provider_response(response)["status"] == "no_statement"
    with pytest.raises(ExtractionSchemaError, match="no_statement_reason"):
        parse_provider_response({"status": "no_statement", "candidates": []})


def test_provider_response_schema_error_reports_nested_json_path():
    response = FixtureExtractionProvider().extract(_evidence(), _external_profile())
    response["candidates"][0]["subject_entities"][0]["role"] = "invalid-role"
    with pytest.raises(ExtractionSchemaError, match=r"\$\.candidates\[0\]\.subject_entities\[0\]\.role"):
        parse_provider_response(response)


def test_source_unit_ledger_requires_exact_bidirectional_coverage():
    evidence = _evidence("喷嘴应检查。轴承应复查。")
    unit_ids = [unit["source_unit_id"] for unit in source_units_for_evidence(evidence)]
    response = {
        "status": "ok",
        "candidates": [{"source_unit_ids": unit_ids}],
        "source_unit_coverage": [
            {"source_unit_id": unit_id, "status": "covered", "candidate_indexes": [1]}
            for unit_id in unit_ids
        ],
    }
    assert validate_source_unit_coverage(evidence, response) == {"1": unit_ids}
    response["candidates"][0]["source_unit_ids"] = unit_ids[:1]
    with pytest.raises(ExtractionSchemaError, match="disagrees"):
        validate_source_unit_coverage(evidence, response)


@pytest.mark.parametrize(
    ("text", "predicate", "entity"),
    [
        ("地脚螺栓位置误差会影响机组安装。", "causes", "地脚螺栓位置误差"),
        ("应评估工期影响。", "requires", "工期影响"),
        ("安装记录应标注“导致停机”字样。", "requires", "安装记录"),
    ],
)
def test_causal_relation_uses_evidence_semantics_not_single_marker(text, predicate, entity):
    evidence = _evidence(text)
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    item = {
        "statement_text": text,
        "statement_type": "fact" if predicate == "causes" else "requirement",
        "predicate": predicate,
        "subject_entities": [{"surface_form": entity, "role": "subject"}],
        "conditions": [],
        "applicability_scope": {"status": "unknown"},
    }
    candidate = _assemble_candidate(item, evidence, profile, "development_regression_golden", 1)
    assert validate_candidate_against_evidence(candidate, evidence)["evidence_grounding"] is True


def test_provider_adapter_supplies_protocol_constants_without_semantic_retry():
    response = {"schema_version": 999, "response_kind": "wrong", "status": "no_statement", "candidates": [], "source_unit_coverage": [{"source_unit_id": "unit-1", "status": "excluded", "candidate_indexes": [], "reason": "Only a page heading."}], "no_statement_reason": "Only a page heading."}
    parsed = parse_provider_response(response)
    assert parsed["schema_version"] == 1
    assert parsed["response_kind"] == "stage12_candidate_extraction"


def test_provider_backed_path_assembles_candidate_and_preserves_lineage():
    evidence = _evidence()
    router = ProfileRouter(ROOT / "config/stage12_profile_routing.json")
    profile = router.route({"document_logical_id": "doc-af7fa1738c5c89599e41", "revision_id": "rev-c700c57426b967b2d2c9"})
    provider = FixtureExtractionProvider()
    candidate = ProviderBackedExtractor(provider, profile=profile, split="development_regression_golden").extract(evidence)[0]
    assert candidate["review_status"] == "candidate_only"
    assert candidate["formal_release"] is False
    assert candidate["evidence_quote"] == evidence["source_text"]
    assert candidate["statement_type"] in {"fact", "requirement", "procedure", "condition", "observation", "verification", "limitation"}
    assert candidate["subject_entities"][0]["role"] == "subject"


def test_entity_anchor_prefers_subject_but_all_object_entities_remain_grounded():
    evidence = _evidence("设备间隙不得大于4mm。")
    profile = ExtractionProfile(
        semantic_role="construction_standard",
        extraction_profile_id="fixture-profile",
        source_profile_id="standard_or_regulation",
        source_applicability_scope=(),
    )
    item = {
        "statement_text": evidence["source_text"],
        "statement_type": "requirement",
        "predicate": "requires",
        "subject_entities": [
            {"surface_form": "间隙", "role": "quantity_target"},
            {"surface_form": "设备", "role": "subject"},
        ],
        "conditions": [],
        "applicability_scope": {"status": "unknown"},
    }
    candidate = _assemble_candidate(item, evidence, profile, "development_regression_golden", 1)
    assert candidate["subject_entities"][0]["role"] == "subject"
    runtime = to_stage9_runtime_payload([candidate])
    about_links = [link for link in runtime["relations"] if link["predicate"] == "aboutEntity"]
    assert len(about_links) == 1

    item["subject_entities"] = [{"surface_form": "间隙", "role": "quantity_target"}]
    object_only = _assemble_candidate(item, evidence, profile, "development_regression_golden", 1)
    assert object_only["subject_entities"][0]["role"] == "quantity_target"
    to_stage9_runtime_payload([object_only])

    item["subject_entities"] = []
    with pytest.raises(ValueError, match="at least one grounded entity"):
        _assemble_candidate(item, evidence, profile, "development_regression_golden", 1)


def test_candidate_schema_and_stage9_projection_keep_coarse_relation_and_text():
    candidate = _candidate()
    payload = {"schema_version": 1, "stage": "12", "artifact_kind": "engineering_statement_candidates", "status": "candidate_only", "formal_release": False, "producer": "test", "inputs": {"fixture": "fixture"}, "extraction_profile": "heuristic_semantic_v1", "provider_id": "fixture", "prompt_version": "test", "provider_metadata": {"mode": "fixture"}, "candidates": [candidate], "review_diagnostics": []}
    validate_candidate_payload(payload)
    runtime = to_stage9_runtime_payload([candidate])
    statement = next(node for node in runtime["nodes"] if node["type"] == "EngineeringStatement")
    assert statement["properties"]["predicateLabel"] == "requires"
    assert statement["properties"]["statementText"] == candidate["statement_text"]


@pytest.mark.parametrize(
    "text",
    [
        "若真空过低，转子转动需要较多的新蒸汽。",
        "若真空过低，乏汽突然排至凝汽器，会使凝汽器汽侧压力升高。",
    ],
)
def test_conditional_causal_wording_is_classified_as_causes(text):
    candidate = HeuristicSemanticExtractor().extract(_evidence(text))[0]
    assert candidate["predicate"] == "causes"
    assert candidate["relation_direction"] == "cause_to_effect"


def test_normative_yingdang_is_not_a_condition_marker():
    candidate = _candidate("营运单位应当保存运行和维修记录。")
    assert candidate["relation_direction"] == "subject_to_object"


@pytest.mark.parametrize(
    ("source_text", "candidate_text"),
    [
        ("每卷不应少于五种题型。", "每卷应不少于五种题型。"),
        ("销孔不能穿透瓦壁。", "销孔不得穿透瓦壁。"),
    ],
)
def test_equivalent_normative_wording_is_not_rejected(source_text, candidate_text):
    evidence = _evidence(source_text)
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    subject = "每卷" if source_text.startswith("每卷") else "销孔"
    item = {
        "statement_text": candidate_text,
        "statement_type": "requirement",
        "predicate": "prohibits" if "不得" in candidate_text else "requires",
        "subject_entities": [{"surface_form": subject, "role": "subject"}],
        "conditions": [],
        "applicability_scope": {"status": "unknown"},
    }
    candidate = _assemble_candidate(item, evidence, profile, "development_regression_golden", 1)
    validate_candidate_against_evidence(candidate, evidence)


@pytest.mark.parametrize(
    "text,mutator,match",
    [
        ("真空不得低于60kPa。", lambda c: c["quantities"][0].update(value=600), "quantity"),
        ("真空不得低于60kPa。", lambda c: c["quantities"][0].update(unit="MPa"), "quantity"),
        ("真空不得低于60kPa。", lambda c: c["quantities"][0].update(operator="lt"), "quantity"),
        ("真空不得低于60kPa。", lambda c: c["negation_scope"].clear(), "dropped negation"),
    ],
)
def test_deterministic_validator_rejects_high_risk_semantic_drift(text, mutator, match):
    candidate = _candidate(text)
    mutator(candidate)
    with pytest.raises(ValueError, match=match):
        validate_candidate_against_evidence(candidate, _evidence(text))


@pytest.mark.parametrize(
    ("source_text", "candidate_text", "subject", "hard"),
    [
        ("汽轮机不得关闭安全阀。", "汽轮机应关闭安全阀。", "汽轮机", True),
        ("设备可停机检修。", "设备必须停机检修。", "设备", False),
        ("结合面无铁屑。", "结合面有铁屑。", "结合面", True),
    ],
)
def test_source_clause_polarity_and_modality_cannot_be_reversed(source_text, candidate_text, subject, hard):
    evidence = _evidence(source_text)
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    item = {
        "statement_text": candidate_text,
        "statement_type": "requirement" if "关闭" in candidate_text or "检修" in candidate_text else "fact",
        "predicate": "requires" if "关闭" in candidate_text or "检修" in candidate_text else "describes",
        "subject_entities": [{"surface_form": subject, "role": "subject"}],
        "conditions": [],
        "applicability_scope": {"status": "unknown"},
    }
    candidate = _assemble_candidate(item, evidence, profile, "development_regression_golden", 1)
    if hard:
        with pytest.raises(ValueError, match="prohibition|negation"):
            validate_candidate_against_evidence(candidate, evidence)
    else:
        validate_candidate_against_evidence(candidate, evidence)
        assert any(item["code"] == "modality_uncertain" for item in candidate_review_diagnostics(evidence, [candidate], []))


def test_local_normative_check_does_not_copy_another_clause_prohibition():
    evidence = _evidence("汽轮机不得关闭安全阀；给水泵应保持运行。")
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    candidate = _assemble_candidate({
        "statement_text": "给水泵应保持运行。",
        "statement_type": "requirement",
        "predicate": "requires",
        "subject_entities": [{"surface_form": "给水泵", "role": "subject"}],
        "conditions": [],
        "applicability_scope": {"status": "unknown"},
    }, evidence, profile, "development_regression_golden", 1)
    assert validate_candidate_against_evidence(candidate, evidence)["evidence_grounding"] is True


@pytest.mark.parametrize(
    ("source", "statement"),
    [
        ("汽轮机阀门应关闭。汽轮机阀门可关闭。", "汽轮机阀门可关闭。"),
        ("汽轮机阀门可关闭。汽轮机阀门应关闭。", "汽轮机阀门应关闭。"),
        ("喷嘴组与喷嘴槽的结合面应紧密接触。", "喷嘴组与喷嘴槽的结合面应当紧密接触。"),
    ],
)
def test_source_supported_normative_variant_is_not_rejected(source, statement):
    evidence = _evidence(source)
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    candidate = _assemble_candidate({
        "statement_text": statement, "statement_type": "requirement", "predicate": "requires",
        "subject_entities": [{"surface_form": "汽轮机阀门" if "汽轮机" in statement else "喷嘴组", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
    }, evidence, profile, "development_regression_golden", 1)
    assert validate_candidate_against_evidence(candidate, evidence)["evidence_grounding"] is True


@pytest.mark.parametrize(
    ("source", "statement"),
    [
        ("汽轮机油孔应无\n铁屑。", "汽轮机油孔应无铁屑。"),
        ("轴承间隙应不大于\n0.2mm。", "轴承间隙应不大于0.2mm。"),
        ("轴承间隙≤0.10mm。", "轴承间隙不大于0.10mm。"),
    ],
)
def test_equivalent_ocr_or_comparator_wording_is_supported(source, statement):
    evidence = _evidence(source)
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    candidate = _assemble_candidate({
        "statement_text": statement, "statement_type": "requirement", "predicate": "requires",
        "subject_entities": [{"surface_form": "汽轮机油孔" if "油孔" in statement else "轴承间隙", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
    }, evidence, profile, "development_regression_golden", 1)
    assert validate_candidate_against_evidence(candidate, evidence)["evidence_grounding"] is True


def test_quantity_comparator_stays_with_its_local_clause():
    candidate = _candidate("间隙不得大于4mm，厚度宜为5mm。")
    quantities = candidate["quantities"]
    assert [(item["value"], item["operator"]) for item in quantities] == [(4, "lte"), (5, "eq")]
    quantities = _candidate("间隙应不超过10mm且高于5mm")["quantities"]
    assert [(item["value"], item["operator"]) for item in quantities] == [(10, "lte"), (5, "gt")]


def test_causal_consequence_cannot_be_added_to_source():
    evidence = _evidence("汽轮机运行时应保持润滑油压力。")
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    candidate = _assemble_candidate({
        "statement_text": "汽轮机运行时应保持润滑油压力，并导致汽轮机严重损坏。",
        "statement_type": "fact", "predicate": "causes",
        "subject_entities": [{"surface_form": "汽轮机", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
    }, evidence, profile, "development_regression_golden", 1)
    validate_candidate_against_evidence(candidate, evidence)
    assert any(item["code"] == "causality_uncertain" for item in candidate_review_diagnostics(evidence, [candidate], []))


def test_causal_direction_cannot_be_reversed_with_same_words():
    source = "汽轮机振动导致轴承损坏。"
    evidence = _evidence(source)
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    candidate = _assemble_candidate({
        "statement_text": "轴承损坏导致汽轮机振动。", "statement_type": "fact", "predicate": "causes",
        "subject_entities": [{"surface_form": "轴承损坏", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
    }, evidence, profile, "development_regression_golden", 1)
    validate_candidate_against_evidence(candidate, evidence)
    assert any(item["code"] == "causality_uncertain" for item in candidate_review_diagnostics(evidence, [candidate], []))


def test_subject_and_action_cannot_be_combined_from_separate_source_clauses():
    evidence = _evidence("高压缸应检查密封。低压缸应检查轴承。")
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    candidate = _assemble_candidate({
        "statement_text": "高压缸应检查轴承。", "statement_type": "requirement", "predicate": "requires",
        "subject_entities": [{"surface_form": "高压缸", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
    }, evidence, profile, "development_regression_golden", 1)
    with pytest.raises(ValueError, match="different source clauses"):
        validate_candidate_against_evidence(candidate, evidence)


def test_single_clause_action_substitution_is_flagged_for_review():
    evidence = _evidence("喷嘴应检查。")
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    candidate = _assemble_candidate({
        "statement_text": "喷嘴应清洗。", "statement_type": "requirement", "predicate": "requires",
        "subject_entities": [{"surface_form": "喷嘴", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
    }, evidence, profile, "development_regression_golden", 1)
    validate_candidate_against_evidence(candidate, evidence)
    assert any(item["code"] == "action_wording_uncertain" for item in candidate_review_diagnostics(evidence, [candidate], []))


def test_two_source_quantity_limits_cannot_be_swapped_between_objects():
    source = "轴承温度不得超过80℃，润滑油温度不得超过60℃。"
    evidence = _evidence(source)
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    candidate = _assemble_candidate({
        "statement_text": "轴承温度不得超过60℃，润滑油温度不得超过80℃。",
        "statement_type": "requirement", "predicate": "requires",
        "subject_entities": [{"surface_form": "轴承温度", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
    }, evidence, profile, "development_regression_golden", 1)
    with pytest.raises(ValueError, match="wrong source clause"):
        validate_candidate_against_evidence(candidate, evidence)


def test_mixed_question_context_does_not_block_independent_verbatim_claim():
    source = "设备应接地。下一题：余留圈数是多少？(A) 3；(B) 5。"
    evidence = _evidence(source)
    evidence["related_source_context"] = [{"group_kind": "question_options", "group_id": "q1", "source_review_status": "unresolved", "answer_marked": False}]
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    candidate = _assemble_candidate({
        "statement_text": "设备应接地。", "statement_type": "requirement", "predicate": "requires",
        "subject_entities": [{"surface_form": "设备", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
    }, evidence, profile, "development_regression_golden", 1)
    assert validate_candidate_against_evidence(candidate, evidence)["evidence_grounding"] is True


def test_confirmed_answer_annotation_format_can_resolve_answer():
    source = "长度单位未标注时为何种单位？正确答案：A。(A) mm；(B) cm。"
    evidence = _evidence(source)
    evidence["related_source_context"] = [{
        "group_kind": "question_options", "group_id": "q1", "source_review_status": "confirmed", "answer_marked": True,
        "answer_option": "A", "answer_text": "mm", "stem_evidence_id": evidence["evidence_id"],
        "member_evidence": [{"evidence_id": evidence["evidence_id"], "text": source}],
    }]
    resolved = _resolved_marked_answer_text({"source_question": {"group_id": "q1", "status": "linked"}}, evidence)
    assert resolved is not None and "mm" in resolved and "(B) cm" not in resolved


def test_confirmed_multi_blank_answer_is_resolved_by_order_without_marker_in_stem():
    stem_id = "question-stem"
    answer_id = "question-options"
    evidence = _evidence("质量控制点分为（）点和（）点两类。")
    evidence["evidence_id"] = stem_id
    evidence["related_source_context"] = [{
        "group_kind": "question_options", "group_id": "q-multi", "source_review_status": "confirmed",
        "answer_marked": True, "answer_option": "B", "answer_text": "甲、乙",
        "stem_evidence_id": stem_id, "answer_evidence_id": answer_id,
        "answer_source_quote": "（B）甲、乙",
        "member_evidence": [
            {"evidence_id": stem_id, "text": evidence["source_text"]},
            {"evidence_id": answer_id, "text": "（A）甲、丙；（B）甲、乙"},
        ],
    }]
    resolved = _resolved_marked_answer_text({"source_question": {"group_id": "q-multi", "status": "linked"}}, evidence)
    assert resolved == "质量控制点分为甲点和乙点两类。"
    assert _statement_asserts_question_answer(resolved, "甲、乙") is True
    guidance = _model_evidence_view(evidence)["question_extraction_guidance"][0]
    assert guidance["role"] == "stem" and guidance["resolved_statement_support"] == resolved


def test_single_blank_keeps_a_compound_answer_as_one_answer():
    group = {
        "answer_option": "B", "answer_text": "检查、记录",
        "stem_evidence_id": "stem",
        "member_evidence": [{"evidence_id": "stem", "text": "值班人员应（）。"}],
    }
    assert _resolved_question_group_text(group) == "值班人员应检查、记录。"


def test_multiple_blanks_with_mismatched_answer_parts_are_not_guessed():
    group = {
        "answer_option": "B", "answer_text": "一个答案",
        "stem_evidence_id": "stem",
        "member_evidence": [{"evidence_id": "stem", "text": "设备分为（）和（）两类。"}],
    }
    assert _resolved_question_group_text(group) is None


def test_confirmed_question_option_member_is_generic_support_only():
    evidence = _evidence("（A）甲；（B）乙")
    evidence["related_source_context"] = [{
        "group_kind": "question_options", "group_id": "q-option", "source_review_status": "confirmed",
        "answer_marked": True, "answer_option": "B", "answer_text": "乙",
        "stem_evidence_id": "another-evidence", "answer_evidence_id": evidence["evidence_id"],
    }]
    guidance = _model_evidence_view(evidence)["question_extraction_guidance"][0]
    assert guidance["role"] == "support_only"
    assert "其他来源单元" in guidance["instruction"]
    assert "只返回 no_statement" not in guidance["instruction"]


def test_exact_quantity_clause_survives_similar_neighboring_clause():
    source = "喷嘴组左侧定位键与定位销之间的安装间隙应不大于0.04mm；喷嘴组右侧定位键与定位销之间的安装间隙应不大于0.05mm。"
    evidence = _evidence(source)
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    candidate = _assemble_candidate({
        "statement_text": "喷嘴组左侧定位键与定位销之间的安装间隙应不大于0.04mm。",
        "statement_type": "requirement", "predicate": "requires",
        "subject_entities": [{"surface_form": "喷嘴组左侧定位键", "role": "subject"}],
        "conditions": [], "applicability_scope": {"status": "unknown"},
    }, evidence, profile, "development_regression_golden", 1)
    assert validate_candidate_against_evidence(candidate, evidence)["evidence_grounding"] is True


def test_applicability_unknown_is_explicit_and_scope_text_is_retained():
    candidate = _candidate("冲转之前，汽轮机必须建立规定真空。")
    assert candidate["applicability_scope"]["status"] == "known"
    assert candidate["applicability_scope"]["applicability_text"] == "冲转之前"
    candidate["applicability_scope"]["applicability_text"] = "所有机组"
    with pytest.raises(ValueError, match="applicability wording"):
        validate_candidate_against_evidence(candidate, _evidence(candidate["statement_text"]))


def test_known_applicability_must_appear_in_statement_text_not_only_evidence():
    evidence = _evidence("在检修期间，设备应保持封闭。")
    profile = ExtractionProfile("construction_standard", "fixture-profile", "standard_or_regulation", ())
    with pytest.raises(ValueError, match="appear in statement text"):
        _assemble_candidate({
            "statement_text": "设备应保持封闭。",
            "statement_type": "requirement", "predicate": "requires",
            "subject_entities": [{"surface_form": "设备", "role": "subject"}],
            "conditions": [],
            "applicability_scope": {"status": "known", "applicability_text": "在检修期间"},
        }, evidence, profile, "development_regression_golden", 1)


def test_coarse_relation_and_extra_candidate_review_do_not_claim_precision_for_non_exhaustive_gold():
    candidate = _candidate()
    extra = json.loads(json.dumps(candidate))
    extra["candidate_id"] = "stage12-candidate-" + "a" * 20
    extra["statement_text"] = "真空不得低于60kPa，且应保持稳定。"
    extra["object_value"]["value"] = extra["statement_text"]
    report = compare_candidates([candidate, extra], [{"statement_id": "s", "statement_text": candidate["statement_text"], "statement_type": "requirement", "predicate": "requires_condenser_vacuum_before_roll", "entity_alignment": [{"surface_form": "真空"}], "quantities": candidate["quantities"], "negation_scope": candidate["negation_scope"], "conditions": [], "applicability_scope": {}, "document_logical_id": "fixture-document", "physical_page": 1, "evidence_bindings": [{"evidence_id": "fixture-evidence"}]}])
    assert report["gold_exhaustive"] is False
    assert report["candidate_coverage"]["extra_candidate_count"] == 1
    assert report["unmatched_candidate_review"][0]["classification"] in {"needs_gold_completion", "over_split", "duplicate"}
    assert report["candidate_coverage"]["spurious_candidate_rate"] is None


def test_applicability_evaluator_does_not_turn_source_scope_into_not_applicable():
    candidate = _candidate()
    report = compare_candidates([candidate], [{
        "statement_id": "s",
        "statement_text": candidate["statement_text"],
        "statement_type": candidate["statement_type"],
        "predicate": candidate["predicate"],
        "entity_alignment": [{"surface_form": "真空"}],
        "quantities": candidate["quantities"],
        "negation_scope": candidate["negation_scope"],
        "conditions": [],
        "applicability_scope": {"equipment": "condenser"},
        "document_logical_id": "fixture-document",
        "physical_page": 1,
        "evidence_bindings": [{"evidence_id": "fixture-evidence"}],
    }])
    assert report["field_accuracy"]["applicability"] == 1.0
    assert report["field_accuracy"]["applicability_meaning"] == 1.0


def test_evaluator_separates_evidence_support_from_gold_representation():
    evidence = _evidence("若油压低于规定值，应停止调试。")
    candidate = _candidate(evidence["source_text"])
    gold = {
        "statement_id": "s",
        "statement_text": "应停止调试。",
        "statement_type": "requirement",
        "predicate": "requires",
        "entity_alignment": [{"surface_form": "油压"}],
        "quantities": [],
        "negation_scope": [],
        "conditions": [],
        "applicability_scope": {},
        "document_logical_id": evidence["document_logical_id"],
        "physical_page": evidence["physical_page"],
        "evidence_bindings": [{"evidence_id": evidence["evidence_id"]}],
    }
    report = compare_candidates([candidate], [gold], evidence_by_id={evidence["evidence_id"]: evidence})
    assert report["evidence_binding"]["accuracy"] == 1.0
    assert report["evidence_semantic_support"]["accuracy"] == 1.0
    assert report["error_counts"]["unsupported_claim"] == 0
    assert report["gold_mismatch_count"] >= 1


def test_evaluator_maps_legacy_fine_predicate_to_stage12_coarse_relation():
    candidate = _candidate()
    gold = {
        "statement_id": "s",
        "statement_text": candidate["statement_text"],
        "statement_type": "verification",
        "predicate": "requires_dimension_check",
        "entity_alignment": [{"surface_form": "真空"}],
        "quantities": candidate["quantities"],
        "negation_scope": candidate["negation_scope"],
        "conditions": [],
        "applicability_scope": {},
        "document_logical_id": "fixture-document",
        "physical_page": 1,
        "evidence_bindings": [{"evidence_id": "fixture-evidence"}],
    }
    report = compare_candidates([candidate], [gold])
    assert report["field_accuracy"]["relation"] == 1.0


def test_profile_router_exposes_source_level_external_permission_without_filename_logic():
    router = ProfileRouter(ROOT / "config/stage12_profile_routing.json")
    route = router.route({"document_logical_id": "doc-af7fa1738c5c89599e41", "revision_id": "rev-c700c57426b967b2d2c9"})
    assert route.external_llm_allowed is True


def test_profile_router_permissions_are_revision_scoped_and_conservative(tmp_path):
    root = tmp_path / "project"
    config_dir = root / "config"
    registry_dir = root / "data" / "registry"
    config_dir.mkdir(parents=True)
    registry_dir.mkdir(parents=True)
    entries = [
        {"document_logical_id": "doc-1", "revision_id": revision, "semantic_role": "manual", "extraction_profile_id": "manual-v1", "source_profile_id": "manual"}
        for revision in ("rev-1", "rev-2")
    ]
    routing_path = config_dir / "routing.json"
    routing_path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    assets = [
        {"document_logical_id": "doc-1", "revision_id": "rev-1", "external_llm_allowed": True},
        {"document_logical_id": "doc-1", "revision_id": "rev-1", "external_llm_allowed": False},
        {"document_logical_id": "doc-1", "revision_id": "rev-2", "external_llm_allowed": True},
    ]
    (registry_dir / "source_assets.jsonl").write_text("".join(json.dumps(row) + "\n" for row in assets), encoding="utf-8")
    router = ProfileRouter(routing_path)
    assert router.route({"document_logical_id": "doc-1", "revision_id": "rev-1"}).external_llm_allowed is False
    assert router.route({"document_logical_id": "doc-1", "revision_id": "rev-2"}).external_llm_allowed is True


def _external_profile(allowed=True):
    return ExtractionProfile(
        semantic_role="test",
        extraction_profile_id="test_v1",
        source_profile_id="test",
        source_applicability_scope=(),
        external_llm_allowed=allowed,
    )


def test_external_provider_uses_strict_transport_without_fixture_fallback():
    fixture_response = FixtureExtractionProvider().extract(_evidence(), _external_profile())
    provider = ExternalLLMProvider(
        transport=lambda prompt: json.dumps(fixture_response, ensure_ascii=False),
        model_config_identifier="test-model",
        max_attempts=1,
    )
    response = provider.extract(_evidence(), _external_profile())
    assert response["provider_metadata"]["mode"] == "real_llm"
    assert response["candidates"]


def test_external_provider_retries_deterministic_semantic_feedback_without_repairing():
    valid = FixtureExtractionProvider().extract(_evidence(), _external_profile())
    invalid = json.loads(json.dumps(valid, ensure_ascii=False))
    invalid["candidates"][0]["applicability_scope"] = {"status": "known", "applicability_text": "未经证据支持的范围"}
    responses = iter([json.dumps(invalid, ensure_ascii=False), json.dumps(valid, ensure_ascii=False)])
    prompts = []
    events = []

    def transport(prompt):
        prompts.append(prompt["user"])
        return next(responses)

    extractor = ProviderBackedExtractor(
        ExternalLLMProvider(transport=transport, model_config_identifier="test-model", max_attempts=2),
        profile=_external_profile(),
        split="development_regression_golden",
    )
    candidates = extractor.extract(_evidence(), attempt_observer=events.append)
    assert len(candidates) == 1
    assert len(prompts) == 2
    assert "previous_response" in prompts[1] and "validator_reason" in prompts[1]
    assert events[0]["failure_type"] == "semantic_validation_failure"
    assert events[0]["field"] == "applicability"
    assert events[-1]["outcome"] == "success"


def test_external_provider_preserves_final_semantic_diagnostics_without_raw_response():
    valid = FixtureExtractionProvider().extract(_evidence(), _external_profile())
    invalid = json.loads(json.dumps(valid, ensure_ascii=False))
    invalid["candidates"][0]["applicability_scope"] = {"status": "known", "applicability_text": "未经证据支持的范围"}
    raw = json.dumps(invalid, ensure_ascii=False)
    extractor = ProviderBackedExtractor(
        ExternalLLMProvider(transport=lambda prompt: raw, model_config_identifier="test-model", max_attempts=2),
        profile=_external_profile(),
        split="development_regression_golden",
    )
    with pytest.raises(ExtractionProviderError) as raised:
        extractor.extract(_evidence())
    details = raised.value.details
    assert raised.value.failure_type == "semantic_validation_failure"
    assert len(details["semantic_attempts"]) == 2
    assert details["semantic_attempts"][0]["field"] == "applicability"
    assert "raw" not in json.dumps(details, ensure_ascii=False).lower()


def test_provider_shape_errors_are_schema_failures_not_semantic_retries():
    valid = FixtureExtractionProvider().extract(_evidence(), _external_profile())
    malformed = json.loads(json.dumps(valid, ensure_ascii=False))
    malformed["candidates"][0]["applicability_scope"] = "大修时"
    raw = json.dumps(malformed, ensure_ascii=False)
    events = []
    extractor = ProviderBackedExtractor(
        ExternalLLMProvider(transport=lambda prompt: raw, model_config_identifier="test-model", max_attempts=1),
        profile=_external_profile(),
        split="development_regression_golden",
    )
    with pytest.raises(ExtractionProviderError) as raised:
        extractor.extract(_evidence(), attempt_observer=events.append)
    assert raised.value.failure_type == "schema_failure"
    assert events[0]["failure_type"] == "schema_failure"
    assert events[0]["field"] == "schema"


def test_failure_summary_keeps_retry_failure_and_recovery_without_raw_response(tmp_path, monkeypatch):
    summary_path = tmp_path / "stage12_real_llm_failure_summary.json"
    monkeypatch.setattr(stage12_failure_summary, "SUMMARY_PATH", summary_path)
    summary = stage12_failure_summary.write_failure_summary([
        {
            "evidence_id": "E001",
            "attempt": 1,
            "outcome": "failure",
            "failure_type": "semantic_validation_failure",
            "field": "applicability",
            "validator_reason": "scope expansion",
            "evidence_value_or_text": "原文",
            "model_value_or_text": {"applicability_scope": {"status": "known"}},
        },
        {
            "evidence_id": "E001",
            "attempt": 2,
            "outcome": "success",
            "model_value_or_text": {"status": "ok"},
        },
    ], run_kind="controlled_development_diagnostic", evidence_ids=["E001"])
    assert summary["counts"]["semantic_validation_failure"] == 1
    assert summary["counts"]["retry_recovered"] == 1
    assert summary["counts"]["failure_field_counts"] == {"applicability": 1}
    assert summary["raw_model_response_persisted"] is False
    stored = json.loads(summary_path.read_text(encoding="utf-8"))
    assert "attempts" not in stored and "failures" not in stored
    assert "原文" not in summary_path.read_text(encoding="utf-8")
    assert "applicability_scope" not in summary_path.read_text(encoding="utf-8")


def test_failure_summary_counts_one_failover_episode_per_evidence(tmp_path, monkeypatch):
    summary_path = tmp_path / "stage12_real_llm_failure_summary.json"
    monkeypatch.setattr(stage12_failure_summary, "SUMMARY_PATH", summary_path)
    events = [
        {"evidence_id": "E001", "outcome": "failure", "failure_type": "transport_failure", "fallback_triggered": True, "provider_alias": "primary"},
        {"evidence_id": "E001", "outcome": "failure", "failure_type": "transport_failure", "fallback_triggered": True, "provider_alias": "backup"},
        {"evidence_id": "E001", "outcome": "success", "fallback_triggered": True, "provider_alias": "backup"},
    ]
    summary = stage12_failure_summary.write_failure_summary(events, run_kind="test", evidence_ids=["E001"])
    assert summary["counts"]["fallback_triggered"] == 1
    assert summary["counts"]["fallback_success"] == 1


def test_external_provider_rejects_missing_transport_without_fallback():
    with pytest.raises(ExtractionProviderError, match="no configured transport"):
        ExternalLLMProvider().extract(_evidence(), _external_profile())


def test_external_provider_path_rejects_local_only_profile_before_send():
    provider = ExternalLLMProvider(transport=lambda prompt: "never-called", max_attempts=1)
    with pytest.raises(ExtractionProviderError, match="permission"):
        ProviderBackedExtractor(provider, profile=_external_profile(False), split="development_regression_golden").extract(_evidence())


@pytest.mark.parametrize("raw", ["not-json", json.dumps({"schema_version": 1})])
def test_external_provider_rejects_malformed_or_schema_invalid_response(raw):
    provider = ExternalLLMProvider(transport=lambda prompt: raw, max_attempts=1)
    with pytest.raises(ExtractionProviderError, match="schema"):
        provider.extract(_evidence(), _external_profile())


def test_external_provider_surfaces_timeout_without_fixture_fallback():
    provider = ExternalLLMProvider(transport=lambda prompt: (_ for _ in ()).throw(TimeoutError()), max_attempts=1)
    with pytest.raises(ExtractionProviderError, match="provider failed"):
        provider.extract(_evidence(), _external_profile())


def test_transport_retries_retry_after_without_logging_or_fallback():
    calls = []
    sleeps = []
    headers = Message()
    headers["Retry-After"] = "0"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()

    def opener(request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise HTTPError("https://example.invalid", 503, "busy", headers, None)
        return Response()

    transport = OpenAICompatibleChatTransport(
        endpoint="https://example.invalid/v1",
        model="test",
        api_key="secret-for-test",
        timeout_seconds=3,
        max_attempts=2,
        backoff_base_seconds=9,
        sleeper=sleeps.append,
        opener=opener,
    )
    assert transport({"system": "system", "user": "user"}) == "{}"
    assert len(calls) == 2
    assert sleeps == [0.0]


def test_real_evidence_cache_reuses_only_validated_candidates(tmp_path):
    from scripts.build_stage12_candidates import _extract_with_evidence_cache

    response = FixtureExtractionProvider().extract(_evidence(), _external_profile())
    calls = []

    def transport(prompt):
        calls.append(prompt)
        return json.dumps(response, ensure_ascii=False)

    provider = ExternalLLMProvider(transport=transport, model_config_identifier="test-model", max_attempts=1)
    first = _extract_with_evidence_cache(_evidence(), _external_profile(), provider, "development_regression_golden", tmp_path)
    second = _extract_with_evidence_cache(
        _evidence(),
        _external_profile(),
        ExternalLLMProvider(transport=lambda prompt: (_ for _ in ()).throw(AssertionError("cache miss")), model_config_identifier="test-model", max_attempts=1),
        "development_regression_golden",
        tmp_path,
    )
    assert len(first) == len(second) == 1
    assert len(calls) == 1


def test_real_evidence_cache_rejects_changed_source_unit_ledger(tmp_path):
    from scripts.build_stage12_candidates import _extract_with_evidence_cache

    evidence = _evidence("喷嘴应检查。轴承应复查。")
    response = FixtureExtractionProvider().extract(evidence, _external_profile())
    provider = ExternalLLMProvider(transport=lambda prompt: response, model_config_identifier="test-model", max_attempts=1)
    _extract_with_evidence_cache(evidence, _external_profile(), provider, "development_regression_golden", tmp_path)
    cache_path = next(tmp_path.joinpath("evidence").glob("*.json"))
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    cached["source_unit_coverage"][0]["candidate_indexes"] = []
    cache_path.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    calls = []
    _extract_with_evidence_cache(
        evidence, _external_profile(),
        ExternalLLMProvider(transport=lambda prompt: (calls.append(prompt) or response), model_config_identifier="test-model", max_attempts=1),
        "development_regression_golden", tmp_path,
    )
    assert len(calls) == 1


def test_real_evidence_cache_accepts_valid_no_statement_empty_result(tmp_path):
    from scripts.build_stage12_candidates import _extract_with_evidence_cache

    response = {"schema_version": 1, "response_kind": "stage12_candidate_extraction", "status": "no_statement", "candidates": [], "source_unit_coverage": [{"source_unit_id": unit["source_unit_id"], "status": "excluded", "candidate_indexes": [], "reason": "Background text without a proposition."} for unit in source_units_for_evidence(_evidence("这是背景说明。"))], "no_statement_reason": "Background text without a proposition."}
    calls = []

    def transport(prompt):
        calls.append(prompt)
        return json.dumps(response, ensure_ascii=False)

    provider = ExternalLLMProvider(transport=transport, model_config_identifier="test-model", max_attempts=1)
    first = _extract_with_evidence_cache(_evidence("这是背景说明。"), _external_profile(), provider, "development_regression_golden", tmp_path)
    second = _extract_with_evidence_cache(
        _evidence("这是背景说明。"),
        _external_profile(),
        ExternalLLMProvider(transport=lambda prompt: (_ for _ in ()).throw(AssertionError("no_statement cache miss")), model_config_identifier="test-model", max_attempts=1),
        "development_regression_golden",
        tmp_path,
    )
    assert first == second == []
    assert len(calls) == 1
    cache = json.loads(next(tmp_path.joinpath("evidence").glob("*.json")).read_text(encoding="utf-8"))
    assert cache["response_status"] == "no_statement"
    assert cache["no_statement_reason"] == "Background text without a proposition."
    assert cache["candidates"] == []


def test_cache_contract_and_candidate_schema_changes_force_stale_miss(tmp_path):
    from scripts.build_stage12_candidates import _extract_with_evidence_cache

    response = FixtureExtractionProvider().extract(_evidence(), _external_profile())
    provider = ExternalLLMProvider(transport=lambda prompt: json.dumps(response, ensure_ascii=False), model_config_identifier="test-model", max_attempts=1)
    _extract_with_evidence_cache(_evidence(), _external_profile(), provider, "development_regression_golden", tmp_path)
    cache_path = next(tmp_path.joinpath("evidence").glob("*.json"))
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    cache["contract_fingerprints"]["contract"] = "stale-contract"
    cache["contract_fingerprints"]["candidate_schema"] = "stale-schema"
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    calls = []
    refreshed = _extract_with_evidence_cache(
        _evidence(),
        _external_profile(),
        ExternalLLMProvider(transport=lambda prompt: (calls.append(prompt) or json.dumps(response, ensure_ascii=False)), model_config_identifier="test-model", max_attempts=1),
        "development_regression_golden",
        tmp_path,
    )
    assert refreshed
    assert len(calls) == 1


def test_batch_failure_isolation_continues_after_one_error():
    from scripts.build_stage12_candidates import run_evidence_batch

    items = [(str(index), {"evidence_id": str(index)}, None) for index in range(1, 6)]

    def extract_one(evidence, profile):
        if evidence["evidence_id"] == "3":
            raise TimeoutError("timeout")
        return [{"candidate_id": evidence["evidence_id"]}]

    candidates, failures = run_evidence_batch(items, extract_one)
    assert [item["candidate_id"] for item in candidates] == ["1", "2", "4", "5"]
    assert [item[0] for item in failures] == ["3"]


def test_unrelated_same_page_evidence_is_not_supplied_as_group_context():
    from scripts.build_stage12_candidates import _page_evidence

    page = {"document_key": "fixture", "evidence_ids": ["e1", "e2", "e3", "e4"]}
    evidence = {f"e{index}": {"evidence_id": f"e{index}", "effective_text": f"text {index}", "review_status": "accepted"} for index in range(1, 5)}
    prepared = _page_evidence(page, evidence)
    assert prepared["e2"]["document_key"] == "fixture"
    assert "same_page_context" not in prepared["e2"]
    assert prepared["e2"]["operation_group_context"] == []
    assert prepared["e2"]["related_source_context"] == []


def test_robustness_artifact_is_development_only_and_passes():
    report = _read("data/stage12/stage12_fixture_robustness_evaluation.json")
    assert report["holdout_used_for_tuning"] is False
    assert report["failed_count"] == 0
    assert report["case_count"] >= 6
    assert report["execution_kind"] == "fixture"


def test_real_robustness_artifact_is_not_fixture_labeled():
    path = ROOT / "data/stage12/stage12_robustness_evaluation.json"
    if not path.is_file():
        pytest.skip("current real robustness result is generated only after the complete Development rebuild")
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["execution_kind"] == "real_llm"
    assert report["provider_id"] == "external_llm_openai_compatible_v1"


def test_development_builder_gate_rejects_non_development_split():
    from scripts.build_stage12_candidates import _gate

    manifest = _read("data/stage12/stage12_input_manifest.json")
    manifest["source_split"] = "acceptance_holdout"
    with pytest.raises(ValueError):
        _gate(manifest)

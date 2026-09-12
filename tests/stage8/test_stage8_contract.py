import json
from pathlib import Path

import pytest

from turbine_kg.ontology.builder import (
    apply_mapping_review_overlay,
    build_mapping_payload,
    build_review_queue,
    load_json,
    render_turtle,
    select_mapping_shortlist,
    select_modeling_pattern_coverage,
    validate_contract,
)


ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> dict:
    return load_json(ROOT / relative)


def test_stage8_contract_has_minimal_classes_and_capability_coverage():
    contract = _read("config/ontology_contract.json")
    validate_contract(contract)
    assert {row["id"] for row in contract["top_level_classes"]} == {
        "PhysicalEntity", "ProcessEntity", "InformationEntity", "Situation", "Context"
    }
    assert {row["id"] for row in contract["runtime_classes"]} == {
        "Equipment", "Component", "ProcedureDefinition", "StepDefinition",
        "EngineeringStatement", "Evidence", "ApplicabilityScope", "QuantityValue"
    }
    assert set(contract["capability_coverage"]) == {f"cap-{i:02d}" for i in range(1, 11)}
    assert contract["capability_paths"]["cap-03"]["path"][-1] == "quantityKindLabel"
    assert contract["capability_paths"]["cap-04"]["path"] == ["Situation", "involvesEntity", "PhysicalEntity"]
    assert contract["capability_status"]["cap-09"].startswith("deferred")
    assert contract["candidate_mapping"]["automatic_promotion"] is False
    assert contract["candidate_mapping"]["phenomenon_policy"]["default_mapping_kind"] == "defer"


def test_stage8_phenomenon_policy_defers_measurements_and_allows_explicit_abnormality():
    contract = _read("config/ontology_contract.json")
    candidates = _read("data/stage7/terminology_candidates.json")["candidates"]
    capabilities = _read("data/stage7/business_capability_questions.json")["questions"]
    template = next(row for row in candidates if row["candidate_type"] == "phenomenon")

    def resolve(label: str) -> dict:
        row = json.loads(json.dumps(template, ensure_ascii=False))
        row["candidate_id"] = f"synthetic-{label}"
        row["surface_form"] = row["normalized_form"] = label
        return select_mapping_shortlist(contract, [row], capabilities)[0]

    ordinary = resolve("振动")
    assert ordinary["mapped_class"] is None
    assert ordinary["mapping_kind"] == "defer"
    abnormal = resolve("振动异常")
    assert abnormal["mapped_class"] == "Situation"
    assert abnormal["mapping_kind"] == "class_label"


def _synthetic_phenomenon(label: str, score: int = 1) -> dict:
    return {
        "candidate_id": f"synthetic-{label}", "candidate_type": "phenomenon",
        "surface_form": label, "normalized_form": label,
        "content_fingerprint": f"synthetic-fingerprint-{label}",
        "review_status": "candidate_only", "document_equal_weighted_score": score,
        "capability_question_ids": [], "text_origins": ["native_text"],
        "occurrences": [{
            "source_kind": "accepted_stage6_evidence", "text_origin": "native_text",
            "document_key": "synthetic-document", "physical_page": 1,
            "page_id": "synthetic-page", "text_start": 0, "text_end": len(label),
        }],
    }


@pytest.mark.parametrize("label", [
    "振幅", "无异常", "未见异常", "不存在缺陷", "振动无异常", "非故障",
    "疑似故障", "可能异常", "异常待确认", "待核实缺陷", "是否异常",
])
def test_stage8_non_assertive_and_measurable_phenomena_defer(label):
    contract = _read("config/ontology_contract.json")
    row = select_mapping_shortlist(contract, [_synthetic_phenomenon(label)], [])[0]
    assert row["mapped_class"] is None
    assert row["mapping_kind"] == row["candidate_disposition"] == "defer"


def test_stage8_policy_defer_keeps_manual_decision_pending():
    contract = _read("config/ontology_contract.json")
    row = select_mapping_shortlist(contract, [_synthetic_phenomenon("振动")], [])[0]
    reviewed = apply_mapping_review_overlay({"shortlist": [row], "modeling_pattern_definitions": []}, [])
    assert reviewed["status"] == "pending_manual_review"
    assert reviewed["review_summary"]["pending_manual_review_count"] == 1
    assert reviewed["review_summary"]["deferred_count"] == 0
    assert reviewed["shortlist"][0]["mapping_review_decision"] == "pending_manual_review"
    assert reviewed["shortlist"][0]["candidate_disposition"] == "defer"
    assert "reviewer" not in reviewed["shortlist"][0]
    assert build_review_queue(reviewed)[0]["candidate_disposition"] == "defer"


@pytest.mark.parametrize("kind, target", [
    ("defer", None), ("class_label", None), ("class_label", "UnknownClass"),
    ("quantity_kind_label", "QuantityValue"),
])
def test_stage8_overlay_class_acceptance_requires_a_valid_mapping(kind, target):
    contract = _read("config/ontology_contract.json")
    row = select_mapping_shortlist(contract, [_synthetic_phenomenon("振动")], [])[0]
    row["mapping_kind"], row["mapped_class"] = kind, target
    decision = {
        "candidate_id": row["candidate_id"],
        "candidate_content_fingerprint": row["candidate_content_fingerprint"],
        "decision": "accepted", "disposition": "class",
        "original_page_confirmation": "confirmed_by_user",
    }
    with pytest.raises(ValueError, match="valid class_label mapping"):
        apply_mapping_review_overlay({"shortlist": [row], "modeling_pattern_definitions": []}, [decision])


@pytest.mark.parametrize("label", ["振动", "未见异常", "疑似故障"])
def test_stage8_policy_applies_to_pinned_and_coverage_candidates(label):
    contract = _read("config/ontology_contract.json")
    candidate = _synthetic_phenomenon(label)
    competitors = [_synthetic_phenomenon("振幅", 3), _synthetic_phenomenon("频率", 2)]
    pinned = select_mapping_shortlist(contract, competitors + [candidate], [], {candidate["candidate_id"]})
    row = next(row for row in pinned if row["candidate_id"] == candidate["candidate_id"])
    assert row["mapped_class"] is None and row["mapping_kind"] == "defer"
    contract["modeling_pattern_coverage"] = [{
        "pattern_id": "situation", "representatives": [{
            "candidate_id": candidate["candidate_id"], "target_class": "Situation", "mapping_kind": "class_label",
        }],
    }]
    coverage = select_modeling_pattern_coverage(contract, [candidate], [])
    assert coverage[0]["mapped_class"] is None
    assert coverage[0]["mapping_kind"] == coverage[0]["candidate_disposition"] == "defer"


def test_stage8_owl_is_generated_from_contract_and_defers_future_scope():
    contract = _read("config/ontology_contract.json")
    turtle = render_turtle(contract)
    for name in (
        "PhysicalEntity", "ProcessEntity", "InformationEntity", "Situation", "Context",
        "Equipment", "Component", "ProcedureDefinition", "StepDefinition",
        "EngineeringStatement", "Evidence", "ApplicabilityScope", "QuantityValue",
    ):
        assert f"tv2:{name} a owl:Class" in turtle
    assert "owl:disjointWith" not in turtle
    assert "EngineeringCase" not in turtle
    assert "owl:sameAs" not in turtle


def test_stage8_mapping_consumes_stage7_and_stops_at_manual_review():
    contract = _read("config/ontology_contract.json")
    candidates = _read("data/stage7/terminology_candidates.json")
    capabilities = _read("data/stage7/business_capability_questions.json")
    payload = build_mapping_payload(contract, candidates, capabilities)
    assert payload["status"] == "pending_manual_review"
    assert payload["automatic_promotion"] is False
    assert payload["shortlist"]
    standard_rows = [row for row in payload["shortlist"] if not row.get("coverage_pattern_ids")]
    coverage_rows = [row for row in payload["shortlist"] if row.get("coverage_pattern_ids")]
    assert {row["candidate_type"] for row in standard_rows} <= {
        "equipment", "component", "process", "phenomenon"
    }
    assert {row["candidate_type"] for row in coverage_rows} <= {
        "equipment", "component", "phenomenon", "process", "action",
        "applicability_condition", "synonym_candidate"
    }
    assert all(row["candidate_type"] != "parameter" for row in payload["shortlist"])
    assert all(row["candidate_type"] != "applicability_condition" for row in standard_rows)
    assert all(row["review_status"] == "pending_manual_review" for row in payload["shortlist"])
    assert all(isinstance(row["requires_original_confirmation"], bool) for row in payload["shortlist"])
    assert all(row["source_occurrences"] for row in payload["shortlist"])
    assert all(row["candidate_content_fingerprint"] for row in payload["shortlist"])
    assert all(row["excluded_occurrence_count"] >= 0 for row in payload["shortlist"])
    assert len({row["candidate_id"] for row in payload["shortlist"]}) == len(payload["shortlist"])
    queue = build_review_queue(payload)
    assert len(queue) == sum(
        row.get("mapping_review_decision", "pending_manual_review") == "pending_manual_review" or row["requires_original_confirmation"]
        for row in payload["shortlist"]
    )
    assert all(row["review_status"] == "pending_manual_review" for row in queue)


def test_stage8_allows_stage7_page_occurrence_without_stage6_evidence():
    contract = _read("config/ontology_contract.json")
    candidates = _read("data/stage7/terminology_candidates.json")
    capabilities = _read("data/stage7/business_capability_questions.json")
    forged = json.loads(json.dumps(candidates, ensure_ascii=False))
    target = next(row for row in forged["candidates"] if row["candidate_id"] == "term-6276f7217aa3e69267bb")
    target["occurrences"] = [item for item in target["occurrences"] if item["source_kind"] == "page_text"]
    target["text_origins"] = sorted({item["text_origin"] for item in target["occurrences"]})
    selected = select_mapping_shortlist(contract, forged["candidates"], capabilities["questions"])
    assert any(row["candidate_id"] == target["candidate_id"] for row in selected)




def test_stage8_review_overlay_keeps_only_accepted_and_pending_rows():
    contract = _read("config/ontology_contract.json")
    candidates = _read("data/stage7/terminology_candidates.json")
    capabilities = _read("data/stage7/business_capability_questions.json")
    payload = build_mapping_payload(contract, candidates, capabilities)
    overlay = [
        {
            "candidate_id": payload["shortlist"][0]["candidate_id"],
            "candidate_content_fingerprint": payload["shortlist"][0]["candidate_content_fingerprint"],
            "decision": "deferred",
            "disposition": "defer",
        }
    ]
    reviewed = apply_mapping_review_overlay(payload, overlay)
    assert all(row["mapping_review_decision"] != "deferred" for row in reviewed["shortlist"])
    assert len(reviewed["deferred_candidates"]) == 1
    assert reviewed["review_summary"]["deferred_count"] == 1


def test_stage8_review_overlay_rejects_stale_candidate_fingerprint():
    contract = _read("config/ontology_contract.json")
    candidates = _read("data/stage7/terminology_candidates.json")
    capabilities = _read("data/stage7/business_capability_questions.json")
    payload = build_mapping_payload(contract, candidates, capabilities)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        apply_mapping_review_overlay(payload, [{
            "candidate_id": payload["shortlist"][0]["candidate_id"],
            "candidate_content_fingerprint": "stale",
            "decision": "accepted",
        }])


def test_stage8_content_quality_gate_does_not_trust_stage7_type_only():
    contract = _read("config/ontology_contract.json")
    candidates = _read("data/stage7/terminology_candidates.json")
    capabilities = _read("data/stage7/business_capability_questions.json")
    forged = json.loads(json.dumps(candidates, ensure_ascii=False))
    target = next(row for row in forged["candidates"] if row["candidate_id"] == "term-6276f7217aa3e69267bb")
    target["surface_form"] = "0.23mm"
    target["normalized_form"] = "0.23mm"
    assert all(row["candidate_id"] != target["candidate_id"] for row in select_mapping_shortlist(
        contract, forged["candidates"], capabilities["questions"]
    ))

    target["surface_form"] = "当包括对各种运行状态"
    target["normalized_form"] = "当包括对各种运行状态"
    assert all(row["candidate_id"] != target["candidate_id"] for row in select_mapping_shortlist(
        contract, forged["candidates"], capabilities["questions"]
    ))


def test_stage8_rejects_non_candidate_only_input():
    contract = _read("config/ontology_contract.json")
    capabilities = _read("data/stage7/business_capability_questions.json")
    candidates = _read("data/stage7/terminology_candidates.json")
    candidates["status"] = "promoted"
    with pytest.raises(ValueError, match="candidate_only"):
        build_mapping_payload(contract, candidates, capabilities)


def test_stage8_persisted_artifacts_are_consistent():
    contract = _read("config/ontology_contract.json")
    mapping = _read("data/stage8/ontology_mapping_shortlist.json")
    queue = [
        json.loads(line)
        for line in (ROOT / "data/stage8/ontology_mapping_review_queue.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert render_turtle(contract) == (ROOT / "ontology/minimal_turbine.ttl").read_text(encoding="utf-8")
    assert mapping["status"] == "review_complete_candidate_only"
    assert mapping["automatic_promotion"] is False
    assert len(mapping["shortlist"]) == 4
    assert len(mapping["deferred_candidates"]) == 10
    assert sum(row["mapping_review_decision"] == "accepted" for row in mapping["shortlist"]) == 4
    assert sum(row["mapping_review_decision"] == "pending_manual_review" for row in mapping["shortlist"]) == 0
    assert all(row["candidate_disposition"] in mapping["candidate_disposition_schema"] for row in mapping["shortlist"])
    assert set(mapping["inputs"]) == {
        "terminology_input_manifest", "terminology_candidates",
        "business_capability_questions", "ontology_contract", "mapping_review_overlay"
    }
    assert all(item["path"] and item["sha256"] for item in mapping["inputs"].values())
    assert len(queue) == 0
    assert {row["candidate_id"] for row in queue} == {
        row["candidate_id"] for row in mapping["shortlist"]
        if row.get("mapping_review_decision", "pending_manual_review") == "pending_manual_review" or row["requires_original_confirmation"]
    }
    queued_rows = [
        row for row in mapping["shortlist"]
        if row["mapping_review_decision"] == "pending_manual_review" or row["requires_original_confirmation"]
    ]
    assert {row["candidate_content_fingerprint"] for row in queue} == {
        row["candidate_content_fingerprint"] for row in queued_rows
    }
    assert {row["excluded_occurrence_count"] for row in queue} == {
        row["excluded_occurrence_count"] for row in queued_rows
    }
    entry = _read("data/stage8/stage8_entry_audit.json")
    assert entry["status"] == "complete"
    assert entry["next_stage_allowed"] is True
    assert entry["checks"]["manual_mapping_review_complete"] is True
    assert entry["counts"]["shortlist_count"] == 4
    assert entry["counts"]["review_pending_count"] == 0
    assert entry["counts"]["original_page_confirmation_pending_count"] == 0
    assert entry["checks"]["numeric_only_values_excluded"] is True
    assert entry["checks"]["sentence_and_scope_fragments_excluded"] is True
    assert entry["checks"]["candidate_dispositions_recorded"] is True
    bolt = next(row for row in mapping["shortlist"] if row["normalized_form"] == "螺栓")
    assert bolt["hierarchy_relations"] == [{
        "relation": "broader_than",
        "target_candidate_id": "term-71b9d9c035c0eb73dcfb",
        "target_normalized_form": "地脚螺栓",
    }]
    coverage = {row["pattern_id"]: row for row in mapping["modeling_pattern_coverage"]}
    assert set(coverage) == {
        "equipment", "component", "situation", "quantity_kind",
        "process_procedure", "hierarchy", "alias", "applicability",
    }
    assert coverage["quantity_kind"]["representatives"][0]["proposed_target_class"] == "QuantityValue"
    assert coverage["quantity_kind"]["representatives"][0]["mapping_review_decision"] == "deferred"
    assert coverage["situation"]["representatives"][0]["normalized_form"] == "腐蚀"
    assert all(
        representative["decision_reason"] and representative["source_supported"]
        for pattern in coverage.values()
        for representative in pattern["representatives"]
    )

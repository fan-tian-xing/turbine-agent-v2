import json
from pathlib import Path

import pytest

from turbine_kg.extraction.semantic import HeuristicSemanticExtractor, ProfileRouter, ProfileRoutingError, compare_candidates, to_stage9_runtime_payload, validate_candidate_evidence_binding, validate_candidate_payload, validate_stage12_runtime_projection
from scripts.build_stage12_candidates import _gate

ROOT = Path(__file__).resolve().parents[2]


def _read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _jsonl(path):
    return [json.loads(line) for line in (ROOT / path).read_text(encoding="utf-8").splitlines() if line.strip()]


def test_stage12_contract_and_manifest_are_candidate_only_and_label_free():
    contract = _read("config/stage12_statement_contract.json")
    manifest = _read("data/stage12/stage12_input_manifest.json")
    assert contract["stage"] == "12" and contract["formal_release"] is False
    assert contract["candidate_status"] == "candidate_only"
    assert manifest["source_split"] == "development_regression_golden"
    assert manifest["label_free_extractor_view"] is True
    assert manifest["holdout_used_for_tuning"] is False and manifest["blind_read"] is False
    assert {page["document_key"] for page in manifest["pages"]} == {"DL5190.3", "D300N", "DLT863", "HAF103", "auxiliary_installation_book"}
    assert {item for page in manifest["pages"] for item in page["coverage"]} >= {"numeric_unit", "range", "negation", "condition", "multi_object_or_step", "enumeration"}
    assert manifest["scope_kind"] == "representative_page_baseline"
    assert manifest["representative_chapter_claim_allowed"] is False
    assert all(page["section_path"] == "chapter_unknown" for page in manifest["pages"])
    assert {item["extraction_profile_id"] for item in manifest["pages"]} == {"manufacturer_manual_v1", "construction_standard_v1", "commissioning_guideline_v1", "nuclear_safety_regulation_v1", "textbook_v1"}


def test_extractor_does_not_copy_gold_fields_and_is_evidence_grounded():
    evidence = {row["evidence"]["evidence_id"]: row["evidence"] for row in _jsonl("data/stage6/stage6_evidence_bundle.jsonl")}
    manifest = _read("data/stage12/stage12_input_manifest.json")
    extractor = HeuristicSemanticExtractor()
    candidates = [candidate for page in manifest["pages"] for evidence_id in page["evidence_ids"] for candidate in extractor.extract(evidence[evidence_id])]
    assert candidates
    assert all(candidate["review_status"] == "candidate_only" and candidate["formal_release"] is False for candidate in candidates)
    assert all(candidate["evidence_bindings"] and candidate["source_text_sha256"] for candidate in candidates)
    assert not any("stage11-statement" in candidate["candidate_id"] for candidate in candidates)
    payload = {"schema_version": 1, "stage": "12", "artifact_kind": "engineering_statement_candidates", "status": "candidate_only", "formal_release": False, "producer": "test", "inputs": {"fixture": "fixture"}, "extraction_profile": extractor.profile_id, "candidates": candidates}
    validate_candidate_payload(payload)
    runtime = to_stage9_runtime_payload(candidates)
    assert runtime["schema_version"] == 1
    statement = next(node for node in runtime["nodes"] if node["type"] == "EngineeringStatement")
    assert statement["properties"]["predicateLabel"] == candidates[0]["predicate"]
    assert statement["properties"]["objectAssertion"] == candidates[0]["statement_text"]
    assert any(relation["predicate"] == "relatedEntity" for relation in runtime["relations"])
    tampered_runtime = json.loads(json.dumps(runtime))
    tampered_statement = next(node for node in tampered_runtime["nodes"] if node["type"] == "EngineeringStatement")
    tampered_statement["properties"].pop("predicateLabel", None)
    with pytest.raises(ValueError, match="Stage 12 semantic field was lost"):
        validate_stage12_runtime_projection(candidates, tampered_runtime)


def test_stage12_runtime_rejects_unsupported_statement_types_and_actions():
    evidence = {"evidence_id": "e", "document_logical_id": "d", "revision_id": "r", "physical_page": 1, "source_span_id": "span", "source_text_sha256": "a" * 64, "source_text": "equipment shall be inspected.", "document_key": "doc"}
    candidate = HeuristicSemanticExtractor().extract(evidence)[0]
    candidate["statement_type"] = "action_authorization"
    with pytest.raises(KeyError):
        to_stage9_runtime_payload([candidate])


def test_field_level_evaluation_reports_error_classes():
    evidence = {"evidence_id": "e", "document_logical_id": "d", "revision_id": "r", "physical_page": 1, "source_span_id": "span", "source_text_sha256": "a" * 64, "source_text": "真空不得低于60kPa。", "document_key": "doc"}
    candidates = HeuristicSemanticExtractor().extract(evidence)
    gold = [{"statement_id": "s", "statement_text": "真空不得低于60kPa。", "statement_type": "requirement", "predicate": "requires", "entity_alignment": [{"surface_form": "真空"}], "quantities": [{"surface_form": "60kPa", "value": 60, "unit": "kPa", "operator": "gte"}], "negation_scope": [{"surface_form": "不得", "polarity": "negative"}], "conditions": [], "physical_page": 1, "document_logical_id": "d", "evidence_bindings": [{"evidence_id": "e"}]}]
    report = compare_candidates(candidates, gold)
    assert report["gold_statement_count"] == 1
    assert set(report["error_counts"]) >= {"boundary_error", "quantity_error", "unsupported_claim"}


def test_stage12_exit_audit_blocks_formal_entry_and_holdout_is_independent():
    audit = _read("data/stage12/stage12_exit_audit.json")
    holdout = _read("data/stage12/stage12_holdout_evaluation.json")
    assert audit["status"] == "in_progress"
    assert audit["quality_status"] == "quality_not_accepted"
    assert audit["next_stage_allowed"] is False
    assert audit["stage13_formal_entry"] == "blocked"
    assert "acceptance_holdout_exposed" in audit["blockers"]
    assert "development_quality_gate" in audit["blockers"]
    assert "representative_chapter_scope_resolved" in audit["blockers"]
    assert audit["zero_tolerance_errors"] == []
    assert holdout["evaluation_entrypoint"] == "scripts/evaluate_stage12_holdout.py"
    assert holdout["holdout_used_for_tuning"] is False
    assert holdout["result_written_to_development"] is False
    assert holdout["acceptance_eligibility"] == "historical_exposed"
    assert holdout["eligible_for_final_acceptance"] is False


def test_quality_gate_is_field_level_and_has_zero_tolerance_grounding():
    contract = _read("config/stage12_statement_contract.json")
    gate = contract["evaluation"]["development_quality_gate"]
    assert gate["relation"] == 0.9
    assert gate["applicability"] == 0.9
    assert gate["evidence_grounding"] == 1.0
    assert contract["evaluation"]["unsupported_claim_count"] == 0


def test_profile_router_is_stable_identity_based_and_rejects_ambiguity(tmp_path):
    routing = _read("config/stage12_profile_routing.json")
    router = ProfileRouter(ROOT / "config/stage12_profile_routing.json")
    route = router.route({"document_logical_id": routing["entries"][0]["document_logical_id"], "revision_id": routing["entries"][0]["revision_id"]})
    assert route.extraction_profile_id == routing["entries"][0]["extraction_profile_id"]
    bad = dict(routing)
    bad["entries"] = routing["entries"] + [dict(routing["entries"][0])]
    path = tmp_path / "ambiguous.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ProfileRoutingError):
        ProfileRouter(path)


def test_development_builder_fails_closed_on_holdout_manifest():
    manifest = _read("data/stage12/stage12_input_manifest.json")
    manifest["source_split"] = "acceptance_holdout"
    with pytest.raises(ValueError):
        _gate(manifest)


def test_missing_candidate_does_not_pass_grounding_or_relabel_as_unsupported_claim():
    report = compare_candidates([], [{"statement_id": "s", "statement_text": "设备应检查。", "statement_type": "requirement", "predicate": "requires", "entity_alignment": [], "quantities": [], "negation_scope": [], "conditions": [], "applicability_scope": {}, "evidence_bindings": [{"evidence_id": "e"}]}])
    assert report["field_accuracy"]["evidence_grounding"] == 0.0
    assert report["error_counts"]["unsupported_claim"] == 0
    assert report["statement_recall"] == 0.0


def test_candidate_lineage_is_checked_against_canonical_evidence():
    evidence = _jsonl("data/stage6/stage6_evidence_bundle.jsonl")[0]["evidence"]
    candidate = HeuristicSemanticExtractor().extract(evidence)[0]
    validate_candidate_evidence_binding(candidate, evidence)
    candidate["source_text_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        validate_candidate_evidence_binding(candidate, evidence)


def test_project_state_and_stage12_exit_audit_both_block_stage13_formal_entry():
    state = _read("data/project_state.json")
    audit = _read("data/stage12/stage12_exit_audit.json")
    assert state["current_stage"] == 12
    assert state["current_stage_status"] == "in_progress"
    assert state["next_stage_status"] == "blocked"
    assert audit["status"] == state["current_stage_status"]
    assert audit["next_stage_allowed"] is False

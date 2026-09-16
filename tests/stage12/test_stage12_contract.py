import json
from pathlib import Path

import pytest

from turbine_kg.extraction.semantic import HeuristicSemanticExtractor, compare_candidates, to_stage9_runtime_payload, validate_candidate_payload

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
    assert to_stage9_runtime_payload(candidates)["schema_version"] == 1


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


def test_stage12_exit_audit_is_complete_and_holdout_is_independent():
    audit = _read("data/stage12/stage12_exit_audit.json")
    holdout = _read("data/stage12/stage12_holdout_evaluation.json")
    assert audit["status"] == "complete"
    assert audit["next_stage_allowed"] is True
    assert audit["zero_tolerance_errors"] == []
    assert "development_quality_gate" not in audit["blockers"]
    assert holdout["evaluation_entrypoint"] == "scripts/evaluate_stage12_holdout.py"
    assert holdout["holdout_used_for_tuning"] is False
    assert holdout["result_written_to_development"] is False

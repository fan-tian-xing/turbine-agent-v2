import hashlib
import json
from pathlib import Path

import pytest

from turbine_kg.extraction.semantic import (
    ExtractionSchemaError,
    HeuristicSemanticExtractor,
    ProfileRouter,
    ProviderBackedExtractor,
    compare_candidates,
    parse_provider_response,
    provider_from_config,
    to_stage9_runtime_payload,
    validate_candidate_against_evidence,
    validate_candidate_payload,
)

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


def test_provider_response_parser_is_strict_and_does_not_repair_prose():
    with pytest.raises(ExtractionSchemaError):
        parse_provider_response("```json\n{}\n```")
    response = {"schema_version": 1, "response_kind": "stage12_candidate_extraction", "status": "no_statement", "candidates": []}
    assert parse_provider_response(response)["status"] == "no_statement"


def test_provider_backed_path_assembles_candidate_and_preserves_lineage():
    evidence = _evidence()
    router = ProfileRouter(ROOT / "config/stage12_profile_routing.json")
    profile = router.route({"document_logical_id": "doc-af7fa1738c5c89599e41", "revision_id": "rev-c700c57426b967b2d2c9"})
    provider = provider_from_config(ROOT / "config/stage12_provider.json")
    candidate = ProviderBackedExtractor(provider, profile=profile, split="development_regression_golden").extract(evidence)[0]
    assert candidate["review_status"] == "candidate_only"
    assert candidate["formal_release"] is False
    assert candidate["evidence_quote"] == evidence["source_text"]


def test_candidate_schema_and_stage9_projection_keep_coarse_relation_and_text():
    candidate = _candidate()
    payload = {"schema_version": 1, "stage": "12", "artifact_kind": "engineering_statement_candidates", "status": "candidate_only", "formal_release": False, "producer": "test", "inputs": {"fixture": "fixture"}, "extraction_profile": "heuristic_semantic_v1", "provider_id": "fixture", "prompt_version": "test", "candidates": [candidate]}
    validate_candidate_payload(payload)
    runtime = to_stage9_runtime_payload([candidate])
    statement = next(node for node in runtime["nodes"] if node["type"] == "EngineeringStatement")
    assert statement["properties"]["predicateLabel"] == "requires"
    assert statement["properties"]["statementText"] == candidate["statement_text"]


@pytest.mark.parametrize(
    "text,mutator,match",
    [
        ("真空不得低于60kPa。", lambda c: c["quantities"][0].update(value=600), "quantity"),
        ("真空不得低于60kPa。", lambda c: c["quantities"][0].update(unit="MPa"), "quantity"),
        ("真空不得低于60kPa。", lambda c: c["quantities"][0].update(operator="lt"), "quantity"),
        ("真空不得低于60kPa。", lambda c: c["negation_scope"].clear(), "dropped negation"),
        ("若油压低于规定值，应停止调试。", lambda c: c["conditions"].clear(), "dropped condition"),
    ],
)
def test_deterministic_validator_rejects_high_risk_semantic_drift(text, mutator, match):
    candidate = _candidate(text)
    mutator(candidate)
    with pytest.raises(ValueError, match=match):
        validate_candidate_against_evidence(candidate, _evidence(text))


def test_applicability_unknown_is_explicit_and_scope_text_is_retained():
    candidate = _candidate("冲转之前，汽轮机必须建立规定真空。")
    assert candidate["applicability_scope"]["status"] == "known"
    assert candidate["applicability_scope"]["applicability_text"] == "冲转之前"
    candidate["applicability_scope"]["applicability_text"] = "所有机组"
    with pytest.raises(ValueError, match="applicability wording"):
        validate_candidate_against_evidence(candidate, _evidence(candidate["statement_text"]))


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


def test_profile_router_exposes_source_level_external_permission_without_filename_logic():
    router = ProfileRouter(ROOT / "config/stage12_profile_routing.json")
    route = router.route({"document_logical_id": "doc-af7fa1738c5c89599e41", "revision_id": "rev-c700c57426b967b2d2c9"})
    assert route.external_llm_allowed is False


def test_robustness_artifact_is_development_only_and_passes():
    report = _read("data/stage12/stage12_robustness_evaluation.json")
    assert report["holdout_used_for_tuning"] is False
    assert report["failed_count"] == 0
    assert report["case_count"] >= 6


def test_development_builder_gate_rejects_non_development_split():
    from scripts.build_stage12_candidates import _gate

    manifest = _read("data/stage12/stage12_input_manifest.json")
    manifest["source_split"] = "acceptance_holdout"
    with pytest.raises(ValueError):
        _gate(manifest)

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
    compare_candidates,
    parse_provider_response,
    to_stage9_runtime_payload,
    validate_candidate_against_evidence,
    validate_candidate_payload,
)
from turbine_kg.llm_client import OpenAICompatibleChatTransport
from scripts import stage12_failure_summary
from scripts.audit_stage12_exit import _reserve_acceptance_gate

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
    response = {"schema_version": 1, "response_kind": "stage12_candidate_extraction", "status": "no_statement", "candidates": []}
    assert parse_provider_response(response)["status"] == "no_statement"


def test_provider_adapter_supplies_protocol_constants_without_semantic_retry():
    response = {"schema_version": 999, "response_kind": "wrong", "status": "no_statement", "candidates": []}
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


def test_candidate_schema_and_stage9_projection_keep_coarse_relation_and_text():
    candidate = _candidate()
    payload = {"schema_version": 1, "stage": "12", "artifact_kind": "engineering_statement_candidates", "status": "candidate_only", "formal_release": False, "producer": "test", "inputs": {"fixture": "fixture"}, "extraction_profile": "heuristic_semantic_v1", "provider_id": "fixture", "prompt_version": "test", "provider_metadata": {"mode": "fixture"}, "candidates": [candidate]}
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
        prompts.append(prompt["system"])
        return next(responses)

    extractor = ProviderBackedExtractor(
        ExternalLLMProvider(transport=transport, model_config_identifier="test-model", max_attempts=2),
        profile=_external_profile(),
        split="development_regression_golden",
    )
    candidates = extractor.extract(_evidence(), attempt_observer=events.append)
    assert len(candidates) == 1
    assert len(prompts) == 2
    assert "deterministic Evidence validation" in prompts[1]
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
    assert summary["raw_model_response_persisted"] is False
    assert json.loads(summary_path.read_text(encoding="utf-8"))["attempts"][0]["field"] == "applicability"


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


def test_real_evidence_cache_accepts_valid_no_statement_empty_result(tmp_path):
    from scripts.build_stage12_candidates import _extract_with_evidence_cache

    response = {"schema_version": 1, "response_kind": "stage12_candidate_extraction", "status": "no_statement", "candidates": []}
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


def test_robustness_artifact_is_development_only_and_passes():
    report = _read("data/stage12/stage12_fixture_robustness_evaluation.json")
    assert report["holdout_used_for_tuning"] is False
    assert report["failed_count"] == 0
    assert report["case_count"] >= 6
    assert report["execution_kind"] == "fixture"


def test_real_robustness_artifact_is_not_fixture_labeled():
    report = _read("data/stage12/stage12_robustness_evaluation.json")
    assert report["execution_kind"] == "real_llm"
    assert report["provider_id"] == "external_llm_openai_compatible_v1"


def test_development_builder_gate_rejects_non_development_split():
    from scripts.build_stage12_candidates import _gate

    manifest = _read("data/stage12/stage12_input_manifest.json")
    manifest["source_split"] = "acceptance_holdout"
    with pytest.raises(ValueError):
        _gate(manifest)

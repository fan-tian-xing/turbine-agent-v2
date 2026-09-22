"""Audit the Stage 12 representative semantic-extraction closed loop."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from turbine_kg.extraction.semantic import (
    ProfileRouter,
    compare_candidates,
    extraction_contract_fingerprint,
    extraction_source_fingerprint,
    provider_from_config,
    to_stage9_runtime_payload,
    validate_candidate_against_evidence,
    validate_candidate_evidence_binding,
    validate_candidate_payload,
)
from turbine_kg.observability.lineage import verify_input_hashes
from build_stage12_candidates import _evidence_cache_key
from build_stage12_development_adjudication import OUTPUT_PATH

ROOT = Path(__file__).resolve().parents[1]
STAGE12 = ROOT / "data/stage12"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _reserve_acceptance_gate(
    reserve_gold_ready: bool,
    reserve_acceptance: dict,
    acceptance_policy: dict[str, Any],
) -> bool:
    """Evaluate only the independent Reserve result for final acceptance.

    Historical/exposed Holdout observations are intentionally not inputs here;
    they remain implementation and lineage evidence, but can never promote an
    exposed result to final acceptance.
    """
    # Keep old unit-test fixtures readable while the live Contract uses the
    # Evidence-first v2 policy below.
    if "hard_safety" not in acceptance_policy:
        return bool(
            reserve_gold_ready
            and reserve_acceptance.get("status") == "completed"
            and reserve_acceptance.get("eligible_for_final_acceptance") is True
            and all(reserve_acceptance.get("field_accuracy", {}).get(field, 0.0) >= threshold for field, threshold in acceptance_policy.items())
            and reserve_acceptance.get("error_counts", {}).get("unsupported_claim") == 0
        )
    safety = reserve_acceptance.get("safety_metrics", {})
    hard_safety = {
        name: (safety.get(name, 0.0) >= threshold if isinstance(threshold, float) else safety.get(name, 0) <= threshold)
        for name, threshold in acceptance_policy.get("hard_safety", {}).items()
    }
    supported = {
        name: (safety.get(name, 0.0) >= threshold if isinstance(threshold, float) else safety.get(name, 0) <= threshold)
        for name, threshold in acceptance_policy.get("supported_candidate_quality", {}).items()
    }
    coverage = reserve_acceptance.get("adjudicated_information_coverage")
    coverage_ok = coverage is not None and coverage >= acceptance_policy.get("information_coverage", {}).get("adjudicated_information_coverage", 1.0)
    disagreement = reserve_acceptance.get("adjudicated_disagreement_summary", {})
    adjudication = acceptance_policy.get("adjudication", {})
    disagreement_ok = (
        disagreement.get("confirmed_critical_model_error_count", 0) <= adjudication.get("max_confirmed_critical_model_errors", 0)
        and disagreement.get("confirmed_noncritical_model_error_rate", 1.0) <= adjudication.get("max_confirmed_noncritical_model_error_rate", 0.0)
        and disagreement.get("pending_review_count", 1) <= adjudication.get("max_pending_review_count", 0)
    )
    return bool(
        reserve_gold_ready
        and reserve_acceptance.get("status") == "completed"
        and reserve_acceptance.get("eligible_for_final_acceptance") is True
        and all(hard_safety.values())
        and all(supported.values())
        and coverage_ok
        and disagreement_ok
    )


def _development_quality_gate(development: dict, policy: dict) -> tuple[bool, dict[str, Any]]:
    """Apply the Evidence-first v2 gate without using raw Gold exactness."""
    safety = development.get("safety_metrics", {})
    coverage = development.get("coverage_metrics", {})
    matched = development.get("matched_field_accuracy", {})
    disagreement = development.get("disagreement_summary", {})
    hard_safety = {
        name: (safety.get(name, 0.0) >= threshold if isinstance(threshold, float) else safety.get(name, 0) <= threshold)
        for name, threshold in policy.get("hard_safety", {}).items()
    }
    adjudicated_coverage = development.get("adjudicated_information_coverage")
    coverage_checks = {
        "adjudicated_information_coverage": adjudicated_coverage is not None and adjudicated_coverage >= policy.get("coverage", {}).get("adjudicated_information_coverage", 1.0),
        "matched_candidate_precision": coverage.get("matched_candidate_precision", 0.0) >= policy.get("coverage", {}).get("matched_candidate_precision", 1.0),
        "over_split_count": coverage.get("over_split_count", 0) <= policy.get("coverage", {}).get("max_over_split_count", 0),
    }
    matched_checks = {
        field: matched.get(field, 0.0) >= threshold
        for field, threshold in policy.get("matched_quality", {}).items()
    }
    adjudicated = development.get("adjudicated_disagreement_summary") or {}
    adjudication_policy = policy.get("adjudication", {})
    adjudication_checks = {
        "confirmed_critical_model_errors": adjudicated.get("confirmed_critical_model_error_count", 0) <= adjudication_policy.get("max_confirmed_critical_model_errors", 0),
        "confirmed_noncritical_model_error_rate": adjudicated.get("confirmed_noncritical_model_error_rate", 1.0) <= adjudication_policy.get("max_confirmed_noncritical_model_error_rate", 0.0),
        "pending_review_count": adjudicated.get("pending_review_count", 1) <= adjudication_policy.get("max_pending_review_count", 0),
        "adjudication_records_are_current": (development.get("adjudication_validation") or {}).get("pending_review_count", 1) == 0 and (development.get("adjudication_validation") or {}).get("extra_adjudication_count", 1) == 0,
    }
    details = {"hard_safety": hard_safety, "coverage": coverage_checks, "matched_quality": matched_checks, "adjudication": adjudication_checks}
    return all((*hard_safety.values(), *coverage_checks.values(), *matched_checks.values(), *adjudication_checks.values())), details


def audit() -> dict:
    contract = _read(ROOT / "config/stage12_statement_contract.json")
    provider_config = _read(ROOT / "config/stage12_provider.json")
    manifest = _read(STAGE12 / "stage12_input_manifest.json")
    baseline = _read(STAGE12 / "stage12_representative_baseline.json")
    routing = _read(ROOT / "config/stage12_profile_routing.json")
    registry = _read(ROOT / "data/stage11/evaluation_sample_registry.json")
    candidate = _read(STAGE12 / "stage12_development_candidates.json")
    development = _read(STAGE12 / "stage12_development_evaluation.json")
    holdout = _read(STAGE12 / "stage12_holdout_evaluation.json")
    coverage_matrix = _read(STAGE12 / "stage12_semantic_coverage_matrix.json")
    failure_summary = _read(STAGE12 / "stage12_real_llm_failure_summary.json") if (STAGE12 / "stage12_real_llm_failure_summary.json").exists() else {}
    stage11 = _read(ROOT / "data/stage11/stage11_exit_audit.json")
    canonical_evidence = {row["evidence"]["evidence_id"]: row["evidence"] for row in _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")}
    dev_gold = _jsonl(ROOT / "data/stage11/stage11_statement_development_samples.jsonl")
    development_gold_count = len(dev_gold)
    router = ProfileRouter(ROOT / "config/stage12_profile_routing.json")

    runtime_report = {"conforms": False, "failures": [], "counts": {}}
    try:
        validate_candidate_payload(candidate)
        to_stage9_runtime_payload(candidate["candidates"])
        runtime_report["conforms"] = True
    except (ValueError, KeyError, TypeError) as error:
        runtime_report["failures"] = [{"message": str(error)}]

    manifest_pages = {(page.get("document_logical_id"), int(page.get("physical_page"))): page for page in manifest.get("pages", [])}
    baseline_pages = {(page.get("document_logical_id"), int(page.get("physical_page"))): page for page in baseline.get("pages", [])}
    manifest_identities = set(manifest_pages)
    baseline_identities = set(baseline_pages)
    accepted_manifest_evidence = {evidence_id for page in manifest.get("pages", []) for evidence_id in page.get("evidence_ids", [])}
    candidate_evidence_ids = {binding.get("evidence_id") for item in candidate.get("candidates", []) for binding in item.get("evidence_bindings", [])}
    lineage_ok = True
    profile_ok = True
    for item in candidate.get("candidates", []):
        for binding in item.get("evidence_bindings", []):
            evidence = canonical_evidence.get(binding.get("evidence_id"))
            if evidence is None:
                lineage_ok = False
                continue
            try:
                if evidence.get("review_status") != "accepted":
                    raise ValueError("candidate is bound to non-accepted canonical Evidence")
                validate_candidate_evidence_binding(item, evidence)
                validate_candidate_against_evidence(item, evidence)
                profile = router.route(evidence)
                profile_ok = profile_ok and item.get("extraction_profile") == profile.extraction_profile_id
            except (ValueError, KeyError, TypeError):
                lineage_ok = False

    coverage = {item for page in manifest.get("pages", []) for item in page.get("coverage", [])}
    forbidden = json.dumps(candidate, ensure_ascii=False).lower()
    quality_thresholds = contract["evaluation"]["development_quality_gate"]
    acceptance_policy = contract["evaluation"]["acceptance_quality_gate"]
    development_quality = development.get("field_accuracy", {})
    adjudication = _read(OUTPUT_PATH)
    dev_recomputed = compare_candidates(candidate.get("candidates", []), dev_gold, gold_exhaustive=False, evidence_by_id=canonical_evidence, adjudication=adjudication)
    provider_contract_ready = (
        provider_config.get("default_provider") == "external_llm"
        and provider_config.get("providers", {}).get("external_llm", {}).get("enabled") is True
    )
    real_llm_artifact = (
        candidate.get("provider_id") == "external_llm_openai_compatible_v1"
        and candidate.get("provider_metadata", {}).get("mode") == "real_llm"
        and development.get("real_llm_execution") is True
    )
    evidence_cache_dir = ROOT / "var/model_runs/stage12/evidence"
    validated_real_cache_count = 0
    current_cache_fingerprints = {
        "prompt": _sha(ROOT / "config/stage12_prompt.txt"),
        "response_schema": _sha(ROOT / "config/stage12_extraction_response.schema.json"),
        "contract": extraction_contract_fingerprint(),
        "candidate_schema": _sha(ROOT / "config/stage12_candidate.schema.json"),
        "semantic_source": extraction_source_fingerprint(),
        "provider_config": _sha(ROOT / "config/stage12_provider.json"),
    }
    expected_cache_keys = set()
    for page in manifest.get("pages", []):
        for evidence_id in page.get("evidence_ids", []):
            evidence = dict(canonical_evidence[evidence_id], document_key=page["document_key"])
            expected_cache_keys.add(_evidence_cache_key(evidence, router.route(evidence), provider_from_config(), manifest["source_split"]))
    stale_cache_files = []
    if evidence_cache_dir.exists():
        for cache_path in evidence_cache_dir.glob("*.json"):
            if cache_path.stem not in expected_cache_keys:
                stale_cache_files.append(cache_path.name)
                continue
            try:
                cached = _read(cache_path)
                cached_candidates = cached.get("candidates")
                cached_status = cached.get("response_status")
                cache_has_valid_result = (
                    isinstance(cached_candidates, list)
                    and cached_status in {"ok", "no_statement"}
                    and ((cached_status == "no_statement" and not cached_candidates) or (cached_status == "ok" and cached_candidates))
                )
                cached_fingerprints = cached.get("contract_fingerprints") or {}
                if cached.get("schema_version") == 2 and cached.get("provider_mode") == "real_llm" and all(cached_fingerprints.get(key) == value for key, value in current_cache_fingerprints.items()) and cache_has_valid_result:
                    validated_real_cache_count += 1
                else:
                    stale_cache_files.append(cache_path.name)
            except (OSError, json.JSONDecodeError, TypeError):
                stale_cache_files.append(cache_path.name)
    stored_eval_matches = all(development.get(key) == dev_recomputed.get(key) for key in ("gold_statement_count", "candidate_count", "field_totals", "field_correct", "field_accuracy", "matched_field_totals", "matched_field_correct", "matched_field_accuracy", "coverage_metrics", "safety_metrics", "disagreement_summary", "adjudicated_disagreement_summary", "adjudicated_information_coverage", "adjudication_validation", "error_counts", "unmatched_gold", "unmatched_candidates", "gold_mismatch_count", "gold_mismatch_details", "evidence_binding", "evidence_semantic_support"))
    dev_input_hashes_match = all(development.get("input_sha256", {}).get(key) == _sha(path) for key, path in {
        "candidate": STAGE12 / "stage12_development_candidates.json",
        "gold": ROOT / "data/stage11/stage11_statement_development_samples.jsonl",
        "manifest": STAGE12 / "stage12_input_manifest.json",
        "routing": ROOT / "config/stage12_profile_routing.json",
        "baseline": STAGE12 / "stage12_representative_baseline.json",
        "contract": ROOT / "config/stage12_statement_contract.json",
        "provider_config": ROOT / "config/stage12_provider.json",
        "prompt": ROOT / "config/stage12_prompt.txt",
        "response_schema": ROOT / "config/stage12_extraction_response.schema.json",
        "registry": ROOT / "data/registry/source_assets.jsonl",
        "adjudication": OUTPUT_PATH,
    }.items())
    holdout_input_hashes_match = all(holdout.get("input_sha256", {}).get(key) == _sha(path) for key, path in {
        "registry": ROOT / "data/stage11/evaluation_sample_registry.json",
        "routing": ROOT / "config/stage12_profile_routing.json",
        "evidence": ROOT / "data/stage11/stage11_holdout_evidence.jsonl",
        "gold": ROOT / "data/stage11/stage11_statement_holdout.jsonl",
        "contract": ROOT / "config/stage12_statement_contract.json",
    }.items())
    robustness = _read(STAGE12 / "stage12_robustness_evaluation.json") if (STAGE12 / "stage12_robustness_evaluation.json").exists() else {}
    robustness_hashes = {
        "prompt": _sha(ROOT / "config/stage12_prompt.txt"),
        "contract": extraction_contract_fingerprint(),
        "response_schema": _sha(ROOT / "config/stage12_extraction_response.schema.json"),
        "candidate_schema": _sha(ROOT / "config/stage12_candidate.schema.json"),
        "semantic_source": extraction_source_fingerprint(),
        "provider_config": _sha(ROOT / "config/stage12_provider.json"),
        "cases": _sha(STAGE12 / "stage12_robustness_cases.json"),
    }
    robustness_input_hashes_match = all(robustness.get("input_sha256", {}).get(key) == value for key, value in robustness_hashes.items())
    candidate_input_refs = {
        key: {"path": key, "sha256": value}
        for key, value in candidate.get("input_sha256", {}).items()
        if key not in {"config/stage12_statement_contract.json", "src/turbine_kg/extraction/semantic.py", "evaluator"}
    }
    candidate_extraction_fingerprint = candidate.get("extraction_fingerprint") or {}
    candidate_lineage_current = (
        not verify_input_hashes(ROOT, candidate_input_refs)
        and candidate_extraction_fingerprint == {
            "contract": extraction_contract_fingerprint(),
            "prompt": _sha(ROOT / "config/stage12_prompt.txt"),
            "response_schema": _sha(ROOT / "config/stage12_extraction_response.schema.json"),
            "candidate_schema": _sha(ROOT / "config/stage12_candidate.schema.json"),
            "semantic_source": extraction_source_fingerprint(),
            "provider_config": _sha(ROOT / "config/stage12_provider.json"),
        }
    )
    manifest_paths = {
        "stage9_exit_audit": "data/stage9/stage9_exit_audit.json",
        "stage10_audit": "data/stage10/stage10_audit.json",
        "stage11_exit_audit": "data/stage11/stage11_exit_audit.json",
        "stage6_evidence_bundle": "data/stage6/stage6_evidence_bundle.jsonl",
        "stage11_evaluation_sample_registry": "data/stage11/evaluation_sample_registry.json",
        "stage12_representative_baseline": "data/stage12/stage12_representative_baseline.json",
        "stage12_profile_routing": "config/stage12_profile_routing.json",
    }
    manifest_input_refs = {key: {"path": path, "sha256": manifest.get("inputs", {}).get(key, "")} for key, path in manifest_paths.items()}
    manifest_lineage_current = not verify_input_hashes(ROOT, manifest_input_refs)
    representative_chapter_resolved = baseline.get("scope_kind") == "representative_chapter" and baseline.get("representative_chapter_claim_allowed") is True and baseline.get("section_identity_status") == "resolved"
    representative_input_scope_declared = (
        manifest.get("scope_kind") == baseline.get("scope_kind") == "representative_page_baseline"
        and manifest.get("representative_chapter_claim_allowed") is False
        and baseline.get("representative_chapter_claim_allowed") is False
    )
    reserve_gold_path = STAGE12 / "stage12_reserve_acceptance.json"
    reserve_acceptance = _read(reserve_gold_path) if reserve_gold_path.exists() else {}
    reserve_gold_ready = reserve_acceptance.get("gold_status") == "independently_reviewed_gold"
    reserve_registry_records = [item for item in registry.get("records", []) if item.get("split") == "acceptance_holdout_reserve"]
    reserve_registry_ready = (
        len(reserve_registry_records) == 5
        and all(item.get("frozen") is True and item.get("review_status") == "reserved" for item in reserve_registry_records)
        and all((item.get("independence") or {}).get("statement") is True for item in reserve_registry_records)
    )
    holdout_exclusions = holdout.get("excluded_gold_statements", [])
    holdout_exclusions_accounted = (
        holdout.get("registered_holdout_statement_count") == holdout.get("gold_statement_count", 0) + holdout.get("excluded_gold_statement_count", 0)
        and holdout.get("registered_holdout_statement_count") == 50
        and holdout.get("gold_statement_count") == 48
        and holdout.get("excluded_gold_statement_count") == 2
        and all(
            item.get("statement_id") and item.get("sample_id") and item.get("document_logical_id")
            and item.get("physical_page") and item.get("exclusion_reason")
            and item.get("exclusion_rule") == holdout.get("excluded_gold_contract")
            and item.get("frozen_before_evaluation") is True
            for item in holdout_exclusions
        )
    )

    implementation_checks = {
        "stage11_exit_gate": stage11.get("status") == "complete" and stage11.get("next_stage_allowed") is True,
        "stage11_internal_lineage_current": not verify_input_hashes(ROOT, stage11.get("inputs", {})),
        "label_free_development_manifest": manifest.get("label_free_extractor_view") is True and manifest.get("source_split") == "development_regression_golden" and manifest.get("holdout_used_for_tuning") is False and manifest.get("blind_read") is False,
        "manifest_input_lineage_current": manifest_lineage_current,
        "representative_baseline_frozen": baseline.get("status") == "frozen_page_baseline" and baseline.get("scope_kind") == "representative_page_baseline" and baseline.get("representative_chapter_claim_allowed") is False and baseline_identities == manifest_identities and all(set(page.get("evidence_ids", [])) == set(manifest_pages[key].get("evidence_ids", [])) and page.get("section_path") == manifest_pages[key].get("section_path") for key, page in baseline_pages.items()),
        "representative_input_scope_declared": representative_input_scope_declared,
        "five_documents_represented": len({page.get("document_key") for page in manifest.get("pages", [])}) == 5,
        "representative_coverage": {"numeric_unit", "range", "negation", "condition", "multi_object_or_step", "enumeration"} <= coverage,
        "candidate_only_boundary": candidate.get("status") == "candidate_only" and candidate.get("formal_release") is False and "authorized_action" not in forbidden,
        "no_holdout_or_blind_in_candidate": "acceptance_holdout" not in forbidden and "acceptance_holdout_reserve" not in forbidden and "blind_test" not in forbidden,
        "candidate_pages_are_manifest_pages": {(item.get("document_logical_id"), int(item.get("physical_page"))) for item in candidate.get("candidates", [])} <= set(manifest_pages) and candidate_evidence_ids <= accepted_manifest_evidence,
        "candidate_schema_and_stage9_gate": runtime_report["conforms"],
        "provider_contract_ready": provider_contract_ready,
        "real_llm_execution_verified": real_llm_artifact and validated_real_cache_count == len(expected_cache_keys),
        "producer_reexecution_current": real_llm_artifact and validated_real_cache_count == len(expected_cache_keys),
        "stale_cache_zero": not stale_cache_files,
        "candidate_input_lineage_current": candidate_lineage_current,
        "canonical_evidence_consumed": lineage_ok,
        "profile_routes_are_unique_and_consumed": profile_ok and len(routing.get("entries", [])) == 5 and {item.get("extraction_profile") for item in candidate.get("candidates", [])} == {entry.get("extraction_profile_id") for entry in routing.get("entries", [])},
        "development_evaluation_present": development.get("status") == "completed" and development.get("evaluator_version") == "stage12-field-evaluator-v7" and development.get("holdout_used_for_tuning") is False and development.get("real_llm_execution") is True and development.get("gold_statement_count") == development_gold_count and stored_eval_matches and dev_input_hashes_match,
        "development_adjudication_current": adjudication.get("artifact_kind") == "stage12_development_disagreement_adjudication" and adjudication.get("status") == "completed" and adjudication.get("source_sha256", {}).get("gold") == _sha(ROOT / "data/stage11/stage11_statement_development_samples.jsonl") and adjudication.get("source_sha256", {}).get("candidate") == _sha(STAGE12 / "stage12_development_candidates.json") and development.get("raw_evaluation_sha256") == adjudication.get("source_sha256", {}).get("evaluation"),
        "independent_holdout_evaluation_present": holdout.get("status") == "completed" and holdout.get("evaluation_entrypoint") == "scripts/evaluate_stage12_holdout.py" and holdout.get("evaluator_version") == "stage12-holdout-evaluator-v5" and holdout.get("frozen_extractor_profile") == "profile_routing_v1" and holdout.get("holdout_used_for_tuning") is False and holdout.get("blind_read") is False and holdout_input_hashes_match,
        "holdout_result_not_written_to_development": holdout.get("result_written_to_development") is False and holdout.get("candidate_artifact_written") is False and holdout.get("runtime_cache_written") is False,
        "holdout_exclusions_accounted": holdout_exclusions_accounted,
        "grounding_zero_tolerance": development.get("error_counts", {}).get("unsupported_claim") == 0,
        "historical_holdout_grounding_observed": holdout.get("error_counts", {}).get("unsupported_claim") == 0,
        "no_ontology_or_release_write": candidate.get("inputs", {}).get("stage12_statement_contract") == "config/stage12_statement_contract.json",
        "robustness_evaluation_present": (STAGE12 / "stage12_robustness_evaluation.json").exists() and robustness.get("status") == "completed" and robustness.get("real_llm_execution") is True and robustness_input_hashes_match,
        "real_llm_failure_summary_current": failure_summary.get("artifact_kind") == "stage12_real_llm_failure_summary" and failure_summary.get("source_split") == "development_regression_golden" and failure_summary.get("holdout_used_for_tuning") is False and failure_summary.get("blind_read") is False,
        "semantic_coverage_matrix_current": (
            coverage_matrix.get("status") == "current"
            and coverage_matrix.get("producer") == "scripts/build_stage12_semantic_coverage_matrix.py"
            and coverage_matrix.get("gold_artifact") == "data/stage11/stage11_statement_development_samples.jsonl"
            and coverage_matrix.get("gold_sha256") == _sha(ROOT / "data/stage11/stage11_statement_development_samples.jsonl")
            and coverage_matrix.get("gold_statement_count") == development_gold_count
            and coverage_matrix.get("generator_sha256") == _sha(ROOT / "scripts/build_stage12_semantic_coverage_matrix.py")
        ),
        "reserve_registry_ready_for_independent_preparation": reserve_registry_ready,
    }
    development_quality_gate, development_gate_details = _development_quality_gate(development, quality_thresholds)
    quality_checks = {
        "development_quality_gate": development_quality_gate,
        "robustness_quality_gate": robustness_input_hashes_match and robustness.get("status") == "completed" and robustness.get("case_count", 0) > 0 and robustness.get("failed_count") == 0,
        "acceptance_quality_gate": _reserve_acceptance_gate(reserve_gold_ready, reserve_acceptance, acceptance_policy),
    }
    checks = {**implementation_checks, **quality_checks}
    # A prior real run under an invalidated contract is historical evidence
    # only.  Current single-call verification requires either a cache carrying
    # current contract fingerprints or a successful current producer replay.
    real_llm_single_call_verified = validated_real_cache_count > 0
    real_llm_batch_execution = (
        real_llm_artifact
        and development.get("status") == "completed"
        and development.get("candidate_count", 0) > 0
        and checks["producer_reexecution_current"]
        and checks["stale_cache_zero"]
    )
    development_quality_gate = quality_checks["development_quality_gate"]
    production_llm_pipeline_ready = real_llm_batch_execution and provider_contract_ready and checks["candidate_schema_and_stage9_gate"] and checks["canonical_evidence_consumed"]
    lifecycle_blockers = set()
    if not reserve_gold_ready:
        lifecycle_blockers.add("upstream_reserve_gold_not_ready")
    blockers = sorted({name for name, passed in checks.items() if not passed} | lifecycle_blockers)
    pipeline_checks = {
        name: implementation_checks[name]
        for name in (
            "provider_contract_ready", "real_llm_execution_verified", "producer_reexecution_current",
            "stale_cache_zero", "candidate_input_lineage_current", "candidate_schema_and_stage9_gate",
            "canonical_evidence_consumed", "profile_routes_are_unique_and_consumed",
            "development_evaluation_present", "development_adjudication_current", "holdout_result_not_written_to_development",
            "real_llm_failure_summary_current", "semantic_coverage_matrix_current",
        )
    }
    pipeline_ready = all(pipeline_checks.values())
    quality_accepted = all(quality_checks.values()) and not lifecycle_blockers
    status = "complete" if pipeline_ready and quality_accepted else "in_progress"
    independent_acceptance = quality_checks["acceptance_quality_gate"]
    gates = {
        "REAL_LLM_SINGLE_CALL_VERIFIED": real_llm_single_call_verified,
        "REAL_LLM_BATCH_EXECUTION": real_llm_batch_execution,
        "PRODUCTION_LLM_PIPELINE_READY": production_llm_pipeline_ready,
        "DEVELOPMENT_QUALITY_GATE": development_quality_gate,
        "ROBUSTNESS_QUALITY_GATE": quality_checks["robustness_quality_gate"],
        "INDEPENDENT_ACCEPTANCE": independent_acceptance,
        "STAGE12_EXIT": status == "complete",
    }
    return {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_exit_audit",
        "status": status,
        "implementation_status": "complete" if pipeline_ready else "in_progress",
        "quality_status": "accepted" if quality_accepted else "quality_not_accepted",
        "pipeline_ready": pipeline_ready,
        "quality_accepted": quality_accepted,
        "reserve_ready": reserve_gold_ready,
        "historical_holdout_status": "exposed_not_eligible",
        "final_acceptance_source": "independent_reserve",
        "reserve_gold_status": reserve_acceptance.get("gold_status", "not_prepared"),
        "reserve_acceptance_status": reserve_acceptance.get("status", "not_prepared"),
        "formal_release": False,
        "producer": "scripts/audit_stage12_exit.py",
        "inputs": {name: {"path": name, "sha256": _sha(ROOT / name)} for name in (
            "data/stage9/stage9_exit_audit.json", "data/stage10/stage10_audit.json", "data/stage11/stage11_exit_audit.json", "data/stage11/evaluation_sample_registry.json", "data/stage12/stage12_representative_baseline.json", "config/stage12_profile_routing.json", "data/stage12/stage12_input_manifest.json", "data/stage6/stage6_evidence_bundle.jsonl", "config/stage12_statement_contract.json", "config/stage12_candidate.schema.json", "config/stage12_provider.json", "config/stage12_prompt.txt", "config/stage12_extraction_response.schema.json", "src/turbine_kg/extraction/semantic.py", "ontology/stage9_core.ttl", "ontology/stage9_shapes.ttl", "data/stage12/stage12_development_candidates.json", "data/stage12/stage12_development_evaluation.json", "data/stage12/stage12_development_disagreement_adjudication.json", "data/stage12/stage12_holdout_evaluation.json", "data/stage12/stage12_semantic_coverage_matrix.json", "scripts/build_stage12_semantic_coverage_matrix.py", "scripts/build_stage12_development_adjudication.py", "data/stage12/stage12_robustness_cases.json", "data/stage12/stage12_robustness_evaluation.json", "data/stage12/stage12_fixture_robustness_evaluation.json", "data/stage12/stage12_real_llm_failure_summary.json", "data/registry/source_assets.jsonl", "data/registry/source_manual_findings.jsonl", "data/project_state.json",
        )},
        "outputs": {"input_manifest": "data/stage12/stage12_input_manifest.json", "development_candidates": "data/stage12/stage12_development_candidates.json", "development_evaluation": "data/stage12/stage12_development_evaluation.json", "development_adjudication": "data/stage12/stage12_development_disagreement_adjudication.json", "holdout_evaluation": "data/stage12/stage12_holdout_evaluation.json", "reserve_acceptance": "data/stage12/stage12_reserve_acceptance.json", "real_llm_failure_summary": "data/stage12/stage12_real_llm_failure_summary.json", "exit_audit": "data/stage12/stage12_exit_audit.json", "runtime_cache": "var/model_runs/stage12"},
        "checks": checks,
        "development_gate_details": development_gate_details,
        "pipeline_checks": pipeline_checks,
        "gates": gates,
        "failure_isolation": "Invalid candidates remain outside accepted Gold, formal knowledge, Release and Neo4j. Holdout evaluation writes metrics only; failed runtime validation never replaces a successful cache entry.",
        "rollback": "Restore the previous verified Stage 11/Stage 12 artifacts and rerun the same development input manifest; do not tune against holdout results.",
        "zero_tolerance_errors": [name for name, count in {"development_unsupported_claim": development.get("error_counts", {}).get("unsupported_claim", 0), "holdout_unsupported_claim": holdout.get("error_counts", {}).get("unsupported_claim", 0)}.items() if count],
        "blockers": blockers,
        "next_stage_allowed": False,
        "next_stage": "Stage 13 formal entry blocked by user freeze; no Stage 13 preparation in this run",
        "next_stage_inputs": {},
        "consumers": ["tests/stage12"],
    }


if __name__ == "__main__":
    result = audit()
    (STAGE12 / "stage12_exit_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "blockers": result["blockers"]}, ensure_ascii=False))

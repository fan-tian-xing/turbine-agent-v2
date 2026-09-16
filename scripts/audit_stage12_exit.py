"""Audit the Stage 12 representative semantic-extraction closed loop."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from turbine_kg.extraction.semantic import (
    ProfileRouter,
    compare_candidates,
    to_stage9_runtime_payload,
    validate_candidate_evidence_binding,
    validate_candidate_payload,
)
from turbine_kg.observability.lineage import verify_input_hashes

ROOT = Path(__file__).resolve().parents[1]
STAGE12 = ROOT / "data/stage12"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def audit() -> dict:
    contract = _read(ROOT / "config/stage12_statement_contract.json")
    manifest = _read(STAGE12 / "stage12_input_manifest.json")
    baseline = _read(STAGE12 / "stage12_representative_baseline.json")
    routing = _read(ROOT / "config/stage12_profile_routing.json")
    registry = _read(ROOT / "data/stage11/evaluation_sample_registry.json")
    candidate = _read(STAGE12 / "stage12_development_candidates.json")
    development = _read(STAGE12 / "stage12_development_evaluation.json")
    holdout = _read(STAGE12 / "stage12_holdout_evaluation.json")
    project_state = _read(ROOT / "data/project_state.json")
    stage11 = _read(ROOT / "data/stage11/stage11_exit_audit.json")
    canonical_evidence = {row["evidence"]["evidence_id"]: row["evidence"] for row in _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")}
    dev_gold = _jsonl(ROOT / "data/stage11/stage11_statement_development_samples.jsonl")
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
                profile = router.route(evidence)
                profile_ok = profile_ok and item.get("extraction_profile") == profile.extraction_profile_id
            except (ValueError, KeyError, TypeError):
                lineage_ok = False

    coverage = {item for page in manifest.get("pages", []) for item in page.get("coverage", [])}
    forbidden = json.dumps(candidate, ensure_ascii=False).lower()
    quality_thresholds = contract["evaluation"]["development_quality_gate"]
    acceptance_thresholds = contract["evaluation"]["acceptance_quality_gate"]
    development_quality = development.get("field_accuracy", {})
    holdout_quality = holdout.get("field_accuracy", {})
    dev_recomputed = compare_candidates(candidate.get("candidates", []), dev_gold)
    stored_eval_matches = all(development.get(key) == dev_recomputed.get(key) for key in ("gold_statement_count", "candidate_count", "field_totals", "field_correct", "field_accuracy", "error_counts", "unmatched_gold", "unmatched_candidates"))
    dev_input_hashes_match = all(development.get("input_sha256", {}).get(key) == _sha(path) for key, path in {
        "candidate": STAGE12 / "stage12_development_candidates.json",
        "gold": ROOT / "data/stage11/stage11_statement_development_samples.jsonl",
        "manifest": STAGE12 / "stage12_input_manifest.json",
        "routing": ROOT / "config/stage12_profile_routing.json",
        "baseline": STAGE12 / "stage12_representative_baseline.json",
        "contract": ROOT / "config/stage12_statement_contract.json",
    }.items())
    holdout_input_hashes_match = all(holdout.get("input_sha256", {}).get(key) == _sha(path) for key, path in {
        "registry": ROOT / "data/stage11/evaluation_sample_registry.json",
        "routing": ROOT / "config/stage12_profile_routing.json",
        "evidence": ROOT / "data/stage11/stage11_holdout_evidence.jsonl",
        "gold": ROOT / "data/stage11/stage11_statement_holdout.jsonl",
        "contract": ROOT / "config/stage12_statement_contract.json",
    }.items())
    candidate_input_refs = {key: {"path": key, "sha256": value} for key, value in candidate.get("input_sha256", {}).items() if key != "evaluator"}
    candidate_lineage_current = not verify_input_hashes(ROOT, candidate_input_refs)
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
    reserve_gold_path = STAGE12 / "stage12_reserve_acceptance.json"
    reserve_gold_ready = reserve_gold_path.exists() and _read(reserve_gold_path).get("status") == "independently_reviewed_gold"
    stage13 = project_state.get("stages", {}).get("13", {})
    stage13_frozen_by_user = (
        stage13.get("status") == "blocked"
        and stage13.get("reason") == "user_requested_stage13_freeze"
        and stage13.get("verification_mode") == "FROZEN_BY_USER"
        and stage13.get("not_executed") is True
        and stage13.get("not_modified") is True
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
        "representative_chapter_scope_resolved": representative_chapter_resolved,
        "five_documents_represented": len({page.get("document_key") for page in manifest.get("pages", [])}) == 5,
        "representative_coverage": {"numeric_unit", "range", "negation", "condition", "multi_object_or_step", "enumeration"} <= coverage,
        "candidate_only_boundary": candidate.get("status") == "candidate_only" and candidate.get("formal_release") is False and "authorized_action" not in forbidden,
        "no_holdout_or_blind_in_candidate": "acceptance_holdout" not in forbidden and "acceptance_holdout_reserve" not in forbidden and "blind_test" not in forbidden,
        "candidate_pages_are_manifest_pages": {(item.get("document_logical_id"), int(item.get("physical_page"))) for item in candidate.get("candidates", [])} <= set(manifest_pages) and candidate_evidence_ids <= accepted_manifest_evidence,
        "candidate_schema_and_stage9_gate": runtime_report["conforms"],
        "candidate_input_lineage_current": candidate_lineage_current,
        "canonical_evidence_consumed": lineage_ok,
        "profile_routes_are_unique_and_consumed": profile_ok and len(routing.get("entries", [])) == 5 and {item.get("extraction_profile") for item in candidate.get("candidates", [])} == {entry.get("extraction_profile_id") for entry in routing.get("entries", [])},
        "development_evaluation_present": development.get("status") == "completed" and development.get("holdout_used_for_tuning") is False and stored_eval_matches and dev_input_hashes_match,
        "independent_holdout_evaluation_present": holdout.get("status") == "completed" and holdout.get("evaluation_entrypoint") == "scripts/evaluate_stage12_holdout.py" and holdout.get("evaluator_version") == "stage12-holdout-evaluator-v2" and holdout.get("frozen_extractor_profile") == "profile_routing_v1" and holdout.get("holdout_used_for_tuning") is False and holdout.get("blind_read") is False and holdout_input_hashes_match,
        "holdout_result_not_written_to_development": holdout.get("result_written_to_development") is False and holdout.get("candidate_artifact_written") is False and holdout.get("runtime_cache_written") is False,
        "holdout_exclusions_accounted": holdout_exclusions_accounted,
        "grounding_zero_tolerance": development.get("error_counts", {}).get("unsupported_claim") == 0 and holdout.get("error_counts", {}).get("unsupported_claim") == 0,
        "no_ontology_or_release_write": candidate.get("inputs", {}).get("stage12_statement_contract") == "config/stage12_statement_contract.json",
        "stage13_frozen_by_user": stage13_frozen_by_user,
    }
    quality_checks = {
        "development_quality_gate": all(development_quality.get(field, 0.0) >= threshold for field, threshold in quality_thresholds.items()),
        "acceptance_quality_gate": holdout.get("eligible_for_final_acceptance") is True and all(holdout_quality.get(field, 0.0) >= threshold for field, threshold in acceptance_thresholds.items()) and holdout.get("error_counts", {}).get("unsupported_claim") == 0,
    }
    checks = {**implementation_checks, **quality_checks}
    lifecycle_blockers = {"acceptance_holdout_exposed"}
    if not reserve_gold_ready:
        lifecycle_blockers.add("reserve_independent_gold_not_ready")
    blockers = sorted({name for name, passed in checks.items() if not passed} | lifecycle_blockers)
    pipeline_ready = all(implementation_checks.values())
    quality_accepted = all(quality_checks.values()) and not lifecycle_blockers
    status = "complete" if pipeline_ready and quality_accepted else "in_progress"
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
        "formal_release": False,
        "producer": "scripts/audit_stage12_exit.py",
        "inputs": {name: {"path": name, "sha256": _sha(ROOT / name)} for name in (
            "data/stage9/stage9_exit_audit.json", "data/stage10/stage10_audit.json", "data/stage11/stage11_exit_audit.json", "data/stage11/evaluation_sample_registry.json", "data/stage12/stage12_representative_baseline.json", "config/stage12_profile_routing.json", "data/stage12/stage12_input_manifest.json", "data/stage6/stage6_evidence_bundle.jsonl", "config/stage12_statement_contract.json", "config/stage12_candidate.schema.json", "ontology/stage9_core.ttl", "ontology/stage9_shapes.ttl", "data/stage12/stage12_development_candidates.json", "data/stage12/stage12_development_evaluation.json", "data/stage12/stage12_holdout_evaluation.json", "data/project_state.json",
        )},
        "outputs": {"input_manifest": "data/stage12/stage12_input_manifest.json", "development_candidates": "data/stage12/stage12_development_candidates.json", "development_evaluation": "data/stage12/stage12_development_evaluation.json", "holdout_evaluation": "data/stage12/stage12_holdout_evaluation.json", "exit_audit": "data/stage12/stage12_exit_audit.json", "runtime_cache": "var/model_runs/stage12"},
        "checks": checks,
        "execution_evidence": {
            "pytest": "Regression tests are a separate verification layer and are not evidence that the production-like extraction pipeline ran.",
            "stage12_production_like_pipeline": {"status": "executed", "scope": "representative_page_baseline", "entrypoints": ["scripts/build_stage12_candidates.py --force --evaluate-development", "scripts/evaluate_stage12_holdout.py", "scripts/audit_stage12_exit.py"]},
            "runtime_semantic_gate": "executed_in_memory_via_to_stage9_runtime_payload",
            "holdout": "executed_for_independent_metrics_only; historical exposure keeps final acceptance ineligible",
        },
        "counts": {"representative_pages": len(manifest.get("pages", [])), "documents": len({page.get("document_key") for page in manifest.get("pages", [])}), "candidates": len(candidate.get("candidates", [])), "development_gold_statements": development.get("gold_statement_count", 0), "holdout_gold_statements_registered": holdout.get("registered_holdout_statement_count", 0), "holdout_gold_statements_evaluated": holdout.get("gold_statement_count", 0), "holdout_gold_statements_excluded": holdout.get("excluded_gold_statement_count", 0)},
        "regression_matrix": {
            "0": {"verification_mode": "STATICALLY_VERIFIED", "evidence": "data/project_state.json v2 boundary"},
            "1": {"verification_mode": "STATICALLY_VERIFIED", "evidence": "data/project_state.json stage 1 state"},
            "2": {"verification_mode": "LINEAGE_VERIFIED", "evidence": "data/project_state.json and Stage 2 exit audit"},
            "3": {"verification_mode": "REPLAY_VERIFIED", "evidence": "data/stage3/real_trial_execution.json"},
            "4": {"verification_mode": "REPLAY_VERIFIED", "evidence": "data/stage4/stage4_full_parse_audit_2026-09-12.json"},
            "5": {"verification_mode": "LINEAGE_VERIFIED", "evidence": "data/stage5/stage5_exit_audit_2026-09-12.json; frozen outputs not rerun"},
            "6": {"verification_mode": "LINEAGE_VERIFIED", "evidence": "data/stage6/stage6_exit_audit.json"},
            "7": {"verification_mode": "REPLAY_VERIFIED", "evidence": "data/stage7/stage7_exit_audit.json"},
            "8": {"verification_mode": "STATICALLY_VERIFIED", "evidence": "data/stage8/stage8_exit_audit.json"},
            "9": {"verification_mode": "EXECUTED", "evidence": "data/stage9/stage9_exit_audit.json and current Stage 9 gate"},
            "10": {"verification_mode": "EXECUTED", "evidence": "data/stage10/stage10_audit.json runtime consumer"},
            "11": {"verification_mode": "REPLAY_VERIFIED", "evidence": "data/stage11/stage11_exit_audit.json"},
            "12": {"verification_mode": "EXECUTED", "scope": "representative_page_baseline only", "evidence": "Stage 12 manifest, candidates, evaluations and exit audit"},
            "13": {"verification_mode": "FROZEN_BY_USER", "not_executed": True, "not_modified": True},
        },
        "quality_observation": {"development_field_accuracy": development_quality, "holdout_field_accuracy": holdout_quality, "development_thresholds": quality_thresholds, "acceptance_thresholds": acceptance_thresholds, "development_recomputed": dev_recomputed.get("field_accuracy", {}), "scale_gate": quality_checks["development_quality_gate"], "interpretation": "development metrics may guide extractor work; the exposed holdout is historical only, reserve Gold is not generated here, and no candidate is promoted by Stage 12"},
        "failure_isolation": "Invalid candidates remain outside accepted Gold, formal knowledge, Release and Neo4j. Holdout evaluation writes metrics only; failed runtime validation never replaces a successful cache entry.",
        "rollback": "Restore the previous verified Stage 11/Stage 12 artifacts and rerun the same development input manifest; do not tune against holdout results.",
        "zero_tolerance_errors": [name for name, count in {"development_unsupported_claim": development.get("error_counts", {}).get("unsupported_claim", 0), "holdout_unsupported_claim": holdout.get("error_counts", {}).get("unsupported_claim", 0)}.items() if count],
        "blockers": blockers,
        "next_stage_allowed": False,
        "next_stage": "Stage 13 formal entry blocked by user freeze; no Stage 13 preparation in this run",
        "next_stage_inputs": {},
        "stage13_formal_entry": "blocked",
        "stage13_verification_mode": "FROZEN_BY_USER",
        "consumers": ["tests/stage12"],
    }


if __name__ == "__main__":
    result = audit()
    (STAGE12 / "stage12_exit_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "blockers": result["blockers"]}, ensure_ascii=False))

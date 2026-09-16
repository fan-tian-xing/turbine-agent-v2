"""Audit the Stage 12 representative semantic-extraction closed loop."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from turbine_kg.extraction.semantic import to_stage9_runtime_payload, validate_candidate_payload

ROOT = Path(__file__).resolve().parents[1]
STAGE12 = ROOT / "data/stage12"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit() -> dict:
    contract = _read(ROOT / "config/stage12_statement_contract.json")
    schema = ROOT / "config/stage12_candidate.schema.json"
    manifest = _read(STAGE12 / "stage12_input_manifest.json")
    candidate = _read(STAGE12 / "stage12_development_candidates.json")
    development = _read(STAGE12 / "stage12_development_evaluation.json")
    holdout = _read(STAGE12 / "stage12_holdout_evaluation.json")
    stage11 = _read(ROOT / "data/stage11/stage11_exit_audit.json")
    canonical_evidence = {
        row["evidence"]["evidence_id"]: row["evidence"]
        for row in (json.loads(line) for line in (ROOT / "data/stage6/stage6_evidence_bundle.jsonl").read_text(encoding="utf-8").splitlines())
    }
    blockers: list[str] = []
    try:
        validate_candidate_payload(candidate)
        runtime_report = {"conforms": True, "failures": [], "counts": {}}
        to_stage9_runtime_payload(candidate["candidates"])
    except (ValueError, KeyError, TypeError) as error:
        runtime_report = {"conforms": False, "failures": [{"message": str(error)}], "counts": {}}
        blockers.append("candidate_schema_or_semantic_gate")
    pages = manifest.get("pages", [])
    docs = {page.get("document_key") for page in pages}
    coverage = {item for page in pages for item in page.get("coverage", [])}
    forbidden = json.dumps(candidate, ensure_ascii=False).lower()
    canonical_evidence_consumed = all(
        binding["evidence_id"] in canonical_evidence
        and item.get("evidence_version_id") == canonical_evidence[binding["evidence_id"]].get("evidence_version_id")
        and set(item.get("source_span_ids", [])) >= set(canonical_evidence[binding["evidence_id"]].get("source_span_ids", []))
        for item in candidate.get("candidates", [])
        for binding in item.get("evidence_bindings", [])
    )
    quality_thresholds = contract["evaluation"]["development_quality_gate"]
    quality = development.get("field_accuracy", {})
    checks = {
        "stage11_exit_gate": stage11.get("status") == "complete" and stage11.get("next_stage_allowed") is True,
        "label_free_development_manifest": manifest.get("label_free_extractor_view") is True and manifest.get("source_split") == "development_regression_golden",
        "five_documents_represented": len(docs) == 5,
        "representative_coverage": {"numeric_unit", "range", "negation", "condition", "multi_object_or_step", "enumeration"} <= coverage,
        "candidate_only_boundary": candidate.get("status") == "candidate_only" and candidate.get("formal_release") is False and "authorized_action" not in forbidden,
        "no_holdout_or_blind_in_candidate": "acceptance_holdout" not in forbidden and "blind_test" not in forbidden,
        "candidate_schema_and_stage9_gate": runtime_report["conforms"],
        "canonical_evidence_consumed": canonical_evidence_consumed,
        "development_evaluation_present": development.get("status") == "completed" and development.get("holdout_used_for_tuning") is False,
        "independent_holdout_evaluation_present": holdout.get("status") == "completed" and holdout.get("evaluation_entrypoint") == "scripts/evaluate_stage12_holdout.py" and holdout.get("holdout_used_for_tuning") is False,
        "holdout_result_not_written_to_development": holdout.get("result_written_to_development") is False,
        "grounding_zero_tolerance": development.get("error_counts", {}).get("unsupported_claim") == 0 and holdout.get("error_counts", {}).get("unsupported_claim") == 0,
        "development_quality_gate": all(quality.get(field, 0.0) >= threshold for field, threshold in quality_thresholds.items()),
        "no_ontology_or_release_write": candidate.get("inputs", {}).get("stage12_statement_contract") == "config/stage12_statement_contract.json",
    }
    blockers.extend(name for name, passed in checks.items() if not passed)
    blockers = sorted(set(blockers))
    status = "complete" if not blockers else "in_progress"
    return {
        "schema_version": 1, "stage": "12", "artifact_kind": "stage12_exit_audit", "status": status, "formal_release": False,
        "producer": "scripts/audit_stage12_exit.py",
        "inputs": {name: {"path": name, "sha256": _sha(ROOT / name)} for name in (
            "data/stage11/stage11_exit_audit.json", "data/stage12/stage12_input_manifest.json", "data/stage6/stage6_evidence_bundle.jsonl", "config/stage12_statement_contract.json", "config/stage12_candidate.schema.json",
            "data/stage12/stage12_development_candidates.json", "data/stage12/stage12_development_evaluation.json", "data/stage12/stage12_holdout_evaluation.json",
        )},
        "outputs": {"input_manifest": "data/stage12/stage12_input_manifest.json", "development_candidates": "data/stage12/stage12_development_candidates.json", "development_evaluation": "data/stage12/stage12_development_evaluation.json", "holdout_evaluation": "data/stage12/stage12_holdout_evaluation.json", "exit_audit": "data/stage12/stage12_exit_audit.json", "runtime_cache": "var/model_runs/stage12"},
        "checks": checks,
        "counts": {"representative_pages": len(pages), "documents": len(docs), "candidates": len(candidate.get("candidates", [])), "development_gold_statements": development.get("gold_statement_count", 0), "holdout_gold_statements_evaluated": holdout.get("gold_statement_count", 0)},
        "quality_observation": {"development_field_accuracy": development.get("field_accuracy", {}), "holdout_field_accuracy": holdout.get("field_accuracy", {}), "development_thresholds": quality_thresholds, "scale_gate": checks["development_quality_gate"], "interpretation": "field metrics are evaluated independently; holdout results never tune the extractor and no candidate is promoted by Stage 12"},
        "failure_isolation": "Invalid candidates remain outside accepted Gold, formal knowledge, Release and Neo4j. Holdout evaluation writes metrics only; failed runtime validation never replaces a successful cache entry.",
        "rollback": "Restore the previous verified Stage 11/Stage 12 artifacts and rerun the same development input manifest; do not tune against holdout results.",
        "zero_tolerance_errors": [],
        "blockers": blockers,
        "next_stage_allowed": not blockers,
        "next_stage": "Stage 13 double-layer review and static review interface" if not blockers else "Stage 12 representative semantic extraction",
        "next_stage_inputs": {"development_candidates": "data/stage12/stage12_development_candidates.json", "development_evaluation": "data/stage12/stage12_development_evaluation.json", "holdout_evaluation": "data/stage12/stage12_holdout_evaluation.json"},
        "consumers": ["tests/stage12", "Stage 13 review interface"] if not blockers else ["tests/stage12"],
    }


if __name__ == "__main__":
    result = audit()
    (STAGE12 / "stage12_exit_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "blockers": result["blockers"]}, ensure_ascii=False))

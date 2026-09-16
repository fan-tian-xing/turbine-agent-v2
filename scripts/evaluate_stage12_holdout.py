"""Independent Stage 12 holdout evaluator.

This process is intentionally separate from the development builder.  It reads
holdout Evidence and Gold only here, writes metrics only, and never changes the
extractor, cache context, development manifest or candidate artifact.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from turbine_kg.extraction.semantic import ProfileRouter, compare_candidates, to_stage9_runtime_payload, validate_candidate_evidence_binding, validate_candidate_payload

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/stage12/stage12_holdout_evaluation.json"
REGISTRY = ROOT / "data/stage11/evaluation_sample_registry.json"
ROUTING = ROOT / "config/stage12_profile_routing.json"
EVALUATOR_VERSION = "stage12-holdout-evaluator-v2"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _input(evidence: dict) -> dict:
    return {
        "evidence_id": evidence["evidence_id"], "document_logical_id": evidence["document_logical_id"], "revision_id": evidence["revision_id"],
        "physical_page": evidence["physical_page"], "logical_page": evidence.get("logical_page"), "source_span_id": evidence["source_span_id"], "source_span_ids": evidence.get("source_span_ids") or [evidence["source_span_id"]], "evidence_version_id": evidence["evidence_version_id"],
        "source_text_sha256": evidence["source_text_sha256"], "source_text": evidence.get("effective_text") or evidence["source_text"],
        "support_type": evidence.get("support_type", "direct"), "document_key": evidence["document_key"],
    }


def evaluate() -> dict:
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    permission = registry.get("consumer_permissions", {}).get("stage12_holdout_evaluator", {})
    if permission.get("entrypoint") != "scripts/evaluate_stage12_holdout.py" or "acceptance_holdout" not in permission.get("allowed_splits", []):
        raise ValueError("Stage 12 holdout evaluator is not authorized by the Evaluation Sample Registry")
    evidence_rows = _rows(ROOT / "data/stage11/stage11_holdout_evidence.jsonl")
    gold = _rows(ROOT / "data/stage11/stage11_statement_holdout.jsonl")
    holdout_pages = {
        (row["document_logical_id"], int(row["physical_page"]))
        for row in registry.get("records", [])
        if row.get("split") == "acceptance_holdout" and row.get("task") == "statement"
    }
    reserve_pages = {
        (row["document_logical_id"], int(row["physical_page"]))
        for row in registry.get("records", [])
        if row.get("split") == "acceptance_holdout_reserve"
    }
    if not holdout_pages or holdout_pages & reserve_pages:
        raise ValueError("Evaluation Sample Registry has an invalid holdout/reserve boundary")
    for record in registry.get("records", []):
        if record.get("split") == "acceptance_holdout" and "stage12_holdout_evaluator" not in record.get("allowed_consumers", []):
            raise ValueError(f"holdout Registry record does not authorize this evaluator: {record.get('sample_id')}")
    accepted_evidence = []
    for row in evidence_rows:
        page = (row["document_logical_id"], int(row["physical_page"]))
        if page not in holdout_pages or page in reserve_pages:
            raise ValueError(f"holdout evaluator received a non-acceptance page: {page}")
        if row.get("review_status") == "accepted" and row.get("evidence_status") == "accepted":
            accepted_evidence.append(row)
    accepted_ids = {row["evidence_id"] for row in accepted_evidence}
    gold = [row for row in gold if row.get("split") == "acceptance_holdout" and row.get("task") == "statement" and row.get("review_status") == "accepted" and {item["evidence_id"] for item in row.get("evidence_bindings", [])} <= accepted_ids]
    router = ProfileRouter(ROUTING)
    candidates = []
    for evidence in accepted_evidence:
        source = _input(evidence)
        candidates.extend(router.extractor_for(source, split="acceptance_holdout").extract(source))
    evidence_by_id = {row["evidence_id"]: row for row in accepted_evidence}
    for candidate in candidates:
        candidate_evidence = [evidence_by_id[binding.get("evidence_id")] for binding in candidate.get("evidence_bindings", []) if binding.get("evidence_id") in evidence_by_id]
        if not candidate_evidence:
            raise ValueError("holdout candidate has no accepted canonical Evidence binding")
        profile = router.route(candidate_evidence[0])
        if candidate.get("extraction_profile") != profile.extraction_profile_id:
            raise ValueError("holdout candidate profile does not match stable Document/Revision route")
        for binding in candidate.get("evidence_bindings", []):
            evidence = evidence_by_id.get(binding.get("evidence_id"))
            if evidence is None:
                raise ValueError(f"holdout candidate binds unknown Evidence: {binding.get('evidence_id')}")
            validate_candidate_evidence_binding(candidate, evidence)
    candidate_payload = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "engineering_statement_candidates",
        "status": "candidate_only",
        "formal_release": False,
        "producer": "scripts/evaluate_stage12_holdout.py",
        "inputs": {"stage11_registry": "data/stage11/evaluation_sample_registry.json", "holdout_evidence": "data/stage11/stage11_holdout_evidence.jsonl", "holdout_gold": "data/stage11/stage11_statement_holdout.jsonl", "stage12_profile_routing": "config/stage12_profile_routing.json"},
        "extraction_profile": "profile_routing_v1",
        "candidates": candidates,
    }
    validate_candidate_payload(candidate_payload)
    to_stage9_runtime_payload(candidates)
    report = compare_candidates(candidates, gold)
    report.update({
        "schema_version": 1, "stage": "12", "artifact_kind": "stage12_holdout_evaluation", "status": "completed", "formal_release": False,
        "evaluation_entrypoint": "scripts/evaluate_stage12_holdout.py", "evaluator_version": EVALUATOR_VERSION, "frozen_extractor_profile": "profile_routing_v1", "holdout_used_for_tuning": False, "result_written_to_development": False,
        "acceptance_eligibility": "historical_exposed", "eligible_for_final_acceptance": False,
        "exposure_reason": "The detailed result was exposed before this strict repair; it is retained for historical hard-case analysis and cannot be reused as final acceptance for the repaired version.",
        "blind_read": False, "isolated_pages_excluded": sum(row.get("review_status") == "isolated" for row in evidence_rows),
        "accepted_evidence_count": len(accepted_evidence), "gold_artifact": "data/stage11/stage11_statement_holdout.jsonl", "evidence_artifact": "data/stage11/stage11_holdout_evidence.jsonl",
        "input_sha256": {"registry": _sha(REGISTRY), "routing": _sha(ROUTING), "contract": _sha(ROOT / "config/stage12_statement_contract.json"), "evidence": _sha(ROOT / "data/stage11/stage11_holdout_evidence.jsonl"), "gold": _sha(ROOT / "data/stage11/stage11_statement_holdout.jsonl")},
        "candidate_artifact_written": False, "runtime_cache_written": False, "development_artifact_unchanged": True,
    })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    result = evaluate()
    print(json.dumps({"status": result["status"], "gold_statement_count": result["gold_statement_count"], "error_counts": result["error_counts"]}, ensure_ascii=False))

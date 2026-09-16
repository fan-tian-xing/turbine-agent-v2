"""Run the Stage 12 development extractor through the Stage 10 runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from turbine_kg.extraction.semantic import (
    ProfileRouter,
    compare_candidates,
    load_contract,
    provider_from_config,
    to_stage9_runtime_payload,
    validate_candidate_against_evidence,
    validate_candidate_evidence_binding,
    validate_candidate_payload,
)
from turbine_kg.observability.runtime import run_with_cache

ROOT = Path(__file__).resolve().parents[1]
STAGE12 = ROOT / "data/stage12"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _gate(manifest: dict) -> None:
    for upstream in ("data/stage9/stage9_exit_audit.json", "data/stage10/stage10_audit.json"):
        upstream_audit = json.loads((ROOT / upstream).read_text(encoding="utf-8"))
        if upstream_audit.get("status") != "complete" or upstream_audit.get("next_stage_allowed") is not True:
            raise ValueError(f"upstream gate is closed: {upstream}")
    audit = json.loads((ROOT / "data/stage11/stage11_exit_audit.json").read_text(encoding="utf-8"))
    if audit.get("status") != "complete" or not audit.get("checks", {}).get("stage12_entry_allowed"):
        raise ValueError("Stage 11 entry gate is closed")
    if manifest.get("status") != "frozen" or manifest.get("source_split") != "development_regression_golden":
        raise ValueError("Stage 12 development manifest is not frozen development input")
    if not manifest.get("label_free_extractor_view") or manifest.get("holdout_used_for_tuning") or manifest.get("blind_read"):
        raise ValueError("Stage 12 development input is not label-free or violates isolation")
    if any(key in json.dumps(manifest, ensure_ascii=False).lower() for key in ("acceptance_holdout", "blind_test")):
        raise ValueError("Stage 12 development manifest contains a holdout or blind boundary")
    for path, expected in manifest.get("inputs", {}).items():
        paths = {
            "stage11_exit_audit": ROOT / "data/stage11/stage11_exit_audit.json",
            "stage9_exit_audit": ROOT / "data/stage9/stage9_exit_audit.json",
            "stage10_audit": ROOT / "data/stage10/stage10_audit.json",
            "stage6_evidence_bundle": ROOT / "data/stage6/stage6_evidence_bundle.jsonl",
            "stage11_evaluation_sample_registry": ROOT / "data/stage11/evaluation_sample_registry.json",
            "stage12_representative_baseline": ROOT / "data/stage12/stage12_representative_baseline.json",
            "stage12_profile_routing": ROOT / "config/stage12_profile_routing.json",
        }
        if path in paths and _sha(paths[path]) != expected:
            raise ValueError(f"Stage 12 manifest input hash is stale: {path}")


def _build(manifest: dict) -> dict:
    evidence_by_id = {row["evidence"]["evidence_id"]: row["evidence"] for row in _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")}
    router = ProfileRouter()
    provider = provider_from_config()
    candidates = []
    for page in manifest["pages"]:
        for evidence_id in page["evidence_ids"]:
            evidence = dict(evidence_by_id[evidence_id], document_key=page["document_key"])
            if evidence.get("review_status") != "accepted":
                raise ValueError(f"development Evidence is not accepted: {evidence_id}")
            profile = router.route(evidence)
            if page.get("extraction_profile_id") != profile.extraction_profile_id or page.get("semantic_role") != profile.semantic_role:
                raise ValueError(f"manifest Profile route does not match Evidence: {evidence_id}")
            candidates.extend(router.extractor_for(evidence, split=manifest["source_split"], provider=provider).extract(evidence))
    payload = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "engineering_statement_candidates",
        "status": "candidate_only",
        "formal_release": False,
        "producer": "scripts/build_stage12_candidates.py",
        "inputs": {
            "stage11_exit_audit": "data/stage11/stage11_exit_audit.json",
            "stage9_exit_audit": "data/stage9/stage9_exit_audit.json",
            "stage10_audit": "data/stage10/stage10_audit.json",
            "stage12_input_manifest": "data/stage12/stage12_input_manifest.json",
            "stage6_evidence_bundle": "data/stage6/stage6_evidence_bundle.jsonl",
            "stage12_statement_contract": "config/stage12_statement_contract.json",
            "stage12_profile_routing": "config/stage12_profile_routing.json",
            "stage12_representative_baseline": "data/stage12/stage12_representative_baseline.json",
        },
        "extraction_profile": "profile_routing_v1",
        "provider_id": provider.provider_id,
        "prompt_version": "stage12-candidate-prompt-v1",
        "input_sha256": {path: _sha(ROOT / path) for path in (
            "data/stage9/stage9_exit_audit.json", "data/stage10/stage10_audit.json", "data/stage11/stage11_exit_audit.json",
            "data/stage12/stage12_input_manifest.json", "data/stage6/stage6_evidence_bundle.jsonl",
            "config/stage12_statement_contract.json", "config/stage12_candidate.schema.json", "ontology/stage9_core.ttl", "ontology/stage9_shapes.ttl",
            "config/stage12_profile_routing.json", "data/stage12/stage12_representative_baseline.json",
            "config/stage12_provider.json", "config/stage12_prompt.txt", "config/stage12_extraction_response.schema.json",
            "src/turbine_kg/extraction/semantic.py",
        )},
        "candidates": candidates,
    }
    validate_candidate_payload(payload)
    for candidate in candidates:
        for binding in candidate.get("evidence_bindings", []):
            evidence = evidence_by_id.get(binding.get("evidence_id"))
            if evidence is None:
                raise ValueError(f"candidate binds unknown Evidence: {binding.get('evidence_id')}")
            validate_candidate_evidence_binding(candidate, evidence)
            validate_candidate_against_evidence(candidate, evidence)
    to_stage9_runtime_payload(candidates)
    return payload


def build(*, force: bool = False) -> tuple[dict, dict]:
    manifest = json.loads((STAGE12 / "stage12_input_manifest.json").read_text(encoding="utf-8"))
    _gate(manifest)
    contract = load_contract()
    input_refs = tuple(
        {"kind": "file", "path": path, "sha256": _sha(ROOT / path)}
        for path in (
            "data/stage11/stage11_exit_audit.json", "data/stage12/stage12_input_manifest.json",
            "data/stage6/stage6_evidence_bundle.jsonl", "config/stage12_statement_contract.json",
            "config/stage12_candidate.schema.json", "ontology/stage9_core.ttl", "ontology/stage9_shapes.ttl",
            "config/stage12_profile_routing.json", "data/stage12/stage12_representative_baseline.json",
            "config/stage12_provider.json", "config/stage12_prompt.txt", "config/stage12_extraction_response.schema.json",
            "src/turbine_kg/extraction/semantic.py",
        )
    )
    batch, payload = run_with_cache(
        cache_root=ROOT / contract["runtime"]["cache_root"],
        operation=contract["runtime"]["operation"],
        input_refs=input_refs,
        cache_context=({"extractor": "profile_routing_v1", "provider": provider_from_config().provider_id}, {"contract": _sha(ROOT / "config/stage12_statement_contract.json"), "profile_routing": _sha(ROOT / "config/stage12_profile_routing.json"), "provider_config": _sha(ROOT / "config/stage12_provider.json"), "prompt": _sha(ROOT / "config/stage12_prompt.txt"), "extractor_source": _sha(ROOT / "src/turbine_kg/extraction/semantic.py")} ),
        output=lambda: _build(manifest),
        schema_path=ROOT / "config/runtime_run.schema.json",
        force=force,
        validate_input=lambda: _gate(manifest),
        validate_output=lambda result: (validate_candidate_payload(result), to_stage9_runtime_payload(result["candidates"])),
    )
    STAGE12.mkdir(parents=True, exist_ok=True)
    (STAGE12 / "stage12_development_candidates.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return batch.as_dict(), payload


def evaluate_development(payload: dict) -> dict:
    gold = _jsonl(ROOT / "data/stage11/stage11_statement_development_samples.jsonl")
    report = compare_candidates(payload["candidates"], gold, gold_exhaustive=False)
    robustness_path = STAGE12 / "stage12_robustness_evaluation.json"
    report.update({"schema_version": 1, "stage": "12", "artifact_kind": "stage12_development_evaluation", "status": "completed", "formal_release": False, "evaluator_version": "stage12-field-evaluator-v3", "holdout_used_for_tuning": False, "blind_read": False, "candidate_artifact": "data/stage12/stage12_development_candidates.json", "gold_artifact": "data/stage11/stage11_statement_development_samples.jsonl", "input_sha256": {"candidate": _sha(STAGE12 / "stage12_development_candidates.json"), "gold": _sha(ROOT / "data/stage11/stage11_statement_development_samples.jsonl"), "manifest": _sha(STAGE12 / "stage12_input_manifest.json"), "routing": _sha(ROOT / "config/stage12_profile_routing.json"), "baseline": _sha(STAGE12 / "stage12_representative_baseline.json"), "contract": _sha(ROOT / "config/stage12_statement_contract.json"), "provider_config": _sha(ROOT / "config/stage12_provider.json"), "prompt": _sha(ROOT / "config/stage12_prompt.txt"), "response_schema": _sha(ROOT / "config/stage12_extraction_response.schema.json"), "evaluator": "stage12-field-evaluator-v3"}})
    report["coverage_matrix"] = "data/stage12/stage12_semantic_coverage_matrix.json"
    report["robustness_artifact"] = "data/stage12/stage12_robustness_evaluation.json"
    report["robustness_executed"] = robustness_path.exists()
    if robustness_path.exists():
        robustness = json.loads(robustness_path.read_text(encoding="utf-8"))
        report["robustness"] = {"case_count": robustness.get("case_count", 0), "passed_count": robustness.get("passed_count", 0), "failed_count": robustness.get("failed_count", 0)}
    (STAGE12 / "stage12_development_evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--evaluate-development", action="store_true")
    args = parser.parse_args()
    batch, payload = build(force=args.force)
    result = {"status": "completed", "extraction_batch_id": batch["extraction_batch_id"], "candidate_count": len(payload["candidates"])}
    if args.evaluate_development:
        evaluation = evaluate_development(payload)
        result["field_accuracy"] = evaluation["field_accuracy"]
    print(json.dumps(result, ensure_ascii=False))

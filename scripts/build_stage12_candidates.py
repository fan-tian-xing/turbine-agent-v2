"""Run the Stage 12 development extractor through the Stage 10 runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from turbine_kg.extraction.semantic import (
    HeuristicSemanticExtractor,
    compare_candidates,
    load_contract,
    to_stage9_runtime_payload,
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
    audit = json.loads((ROOT / "data/stage11/stage11_exit_audit.json").read_text(encoding="utf-8"))
    if audit.get("status") != "complete" or not audit.get("checks", {}).get("stage12_entry_allowed"):
        raise ValueError("Stage 11 entry gate is closed")
    if manifest.get("status") != "frozen" or manifest.get("source_split") != "development_regression_golden":
        raise ValueError("Stage 12 development manifest is not frozen development input")
    if not manifest.get("label_free_extractor_view") or manifest.get("holdout_used_for_tuning") or manifest.get("blind_read"):
        raise ValueError("Stage 12 development input is not label-free or violates isolation")
    if any(key in json.dumps(manifest, ensure_ascii=False).lower() for key in ("acceptance_holdout", "blind_test")):
        raise ValueError("Stage 12 development manifest contains a holdout or blind boundary")


def _build(manifest: dict) -> dict:
    evidence_by_id = {row["evidence"]["evidence_id"]: row["evidence"] for row in _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")}
    extractor = HeuristicSemanticExtractor()
    candidates = []
    for page in manifest["pages"]:
        for evidence_id in page["evidence_ids"]:
            evidence = evidence_by_id[evidence_id]
            if evidence.get("review_status") != "accepted":
                raise ValueError(f"development Evidence is not accepted: {evidence_id}")
            candidates.extend(extractor.extract(evidence))
    payload = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "engineering_statement_candidates",
        "status": "candidate_only",
        "formal_release": False,
        "producer": "scripts/build_stage12_candidates.py",
        "inputs": {
            "stage11_exit_audit": "data/stage11/stage11_exit_audit.json",
            "stage12_input_manifest": "data/stage12/stage12_input_manifest.json",
            "stage6_evidence_bundle": "data/stage6/stage6_evidence_bundle.jsonl",
            "stage12_statement_contract": "config/stage12_statement_contract.json",
        },
        "extraction_profile": extractor.profile_id,
        "candidates": candidates,
    }
    validate_candidate_payload(payload)
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
        )
    )
    batch, payload = run_with_cache(
        cache_root=ROOT / contract["runtime"]["cache_root"],
        operation=contract["runtime"]["operation"],
        input_refs=input_refs,
        cache_context=({"extractor": HeuristicSemanticExtractor.profile_id}, {"contract": _sha(ROOT / "config/stage12_statement_contract.json")}),
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
    report = compare_candidates(payload["candidates"], gold)
    report.update({"schema_version": 1, "stage": "12", "artifact_kind": "stage12_development_evaluation", "status": "completed", "formal_release": False, "holdout_used_for_tuning": False, "blind_read": False, "candidate_artifact": "data/stage12/stage12_development_candidates.json", "gold_artifact": "data/stage11/stage11_statement_development_samples.jsonl"})
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

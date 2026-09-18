"""Run the Stage 12 development extractor through the Stage 10 runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from turbine_kg.extraction.semantic import (
    ProfileRouter,
    ProviderBackedExtractor,
    compare_candidates,
    load_contract,
    provider_from_config,
    to_stage9_runtime_payload,
    validate_candidate_against_evidence,
    validate_candidate_evidence_binding,
    validate_candidate_payload,
)
from turbine_kg.observability.runtime import canonical_json, run_with_cache
try:
    from scripts.stage12_failure_summary import decorate_event, write_failure_summary
except ModuleNotFoundError:  # direct execution from the scripts directory
    from stage12_failure_summary import decorate_event, write_failure_summary

ROOT = Path(__file__).resolve().parents[1]
STAGE12 = ROOT / "data/stage12"


def _evidence_cache_key(evidence: dict, profile, provider, split: str) -> str:
    material = {
        "evidence": evidence,
        "profile": {
            "semantic_role": profile.semantic_role,
            "extraction_profile_id": profile.extraction_profile_id,
            "source_profile_id": profile.source_profile_id,
            "source_applicability_scope": list(profile.source_applicability_scope),
            "external_llm_allowed": profile.external_llm_allowed,
        },
        "split": split,
        "provider_id": provider.provider_id,
        "provider_metadata": getattr(provider, "metadata", {}),
        "prompt_sha256": _sha(ROOT / "config/stage12_prompt.txt"),
        "response_schema_sha256": _sha(ROOT / "config/stage12_extraction_response.schema.json"),
        "semantic_source_sha256": _sha(ROOT / "src/turbine_kg/extraction/semantic.py"),
    }
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def _cache_path(evidence: dict, profile, provider, split: str, cache_root: Path) -> Path:
    return cache_root / "evidence" / f"{_evidence_cache_key(evidence, profile, provider, split)}.json"


def _write_evidence_cache(cache_path: Path, provider, candidates: list[dict]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "schema_version": 1,
        "cache_key": cache_path.stem,
        "provider_id": provider.provider_id,
        "provider_mode": "real_llm",
        "candidates": candidates,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _extract_with_evidence_cache(
    evidence: dict,
    profile,
    provider,
    split: str,
    cache_root: Path,
    *,
    force: bool = False,
    attempt_observer=None,
) -> list[dict]:
    """Reuse only fully validated structured candidates; never persist raw model text."""
    if getattr(provider, "metadata", {}).get("mode") != "real_llm":
        return ProviderBackedExtractor(provider, profile=profile, split=split).extract(evidence)
    cache_key = _evidence_cache_key(evidence, profile, provider, split)
    cache_path = cache_root / "evidence" / f"{cache_key}.json"
    if cache_path.exists() and not force:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("schema_version") == 1 and cached.get("cache_key") == cache_key and cached.get("provider_id") == provider.provider_id:
                candidates = cached.get("candidates") or []
                for candidate in candidates:
                    validate_candidate_evidence_binding(candidate, evidence)
                    validate_candidate_against_evidence(candidate, evidence)
                return candidates
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
    extractor = ProviderBackedExtractor(provider, profile=profile, split=split)
    candidates = extractor.extract(evidence, attempt_observer=attempt_observer)
    _write_evidence_cache(cache_path, provider, candidates)
    return candidates


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def prune_stale_evidence_cache(manifest: dict, cache_root: Path) -> dict[str, int]:
    """Keep only current, revalidated Development Evidence cache entries."""
    evidence_by_id = {row["evidence"]["evidence_id"]: row["evidence"] for row in _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")}
    router = ProfileRouter()
    provider = provider_from_config()
    current: dict[str, tuple[dict, Any]] = {}
    for page in manifest.get("pages", []):
        for evidence_id in page.get("evidence_ids", []):
            evidence = dict(evidence_by_id[evidence_id], document_key=page["document_key"])
            profile = router.route(evidence)
            current[_evidence_cache_key(evidence, profile, provider, manifest["source_split"])] = (evidence, profile)
    evidence_dir = cache_root / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    files = list(evidence_dir.glob("*.json"))
    deleted = 0
    migrated = 0
    kept = set()
    for key, (evidence, profile) in current.items():
        target = evidence_dir / f"{key}.json"
        if target.exists():
            try:
                cached = json.loads(target.read_text(encoding="utf-8"))
                candidates = cached.get("candidates") or []
                if cached.get("cache_key") == key and cached.get("provider_id") == provider.provider_id:
                    for candidate in candidates:
                        validate_candidate_evidence_binding(candidate, evidence)
                        validate_candidate_against_evidence(candidate, evidence)
                    kept.add(target.name)
                    continue
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                pass
            target.unlink()
            deleted += 1
        for legacy in files:
            if legacy == target or not legacy.exists():
                continue
            try:
                cached = json.loads(legacy.read_text(encoding="utf-8"))
                candidates = cached.get("candidates") or []
                if cached.get("provider_id") != provider.provider_id or not candidates:
                    continue
                for candidate in candidates:
                    validate_candidate_evidence_binding(candidate, evidence)
                    validate_candidate_against_evidence(candidate, evidence)
                _write_evidence_cache(target, provider, candidates)
                legacy.unlink()
                migrated += 1
                kept.add(target.name)
                break
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    for path in files:
        if path.exists() and path.name not in kept and path.stem not in current:
            path.unlink()
            deleted += 1
    return {"current_valid": len(kept), "stale_deleted": deleted, "legacy_migrated": migrated}


def prune_stale_batch_records(cache_root: Path, keep_batch_id: str) -> int:
    """Retain the current completed batch record and remove superseded runtime records."""
    batches_root = cache_root / "batches"
    index_path = cache_root / "cache_index.json"
    if not batches_root.exists() or not index_path.exists():
        return 0
    raw_index = json.loads(index_path.read_text(encoding="utf-8"))
    kept_index = {}
    for key, entry in raw_index.items():
        if entry.get("batch_record") == f"batches/{keep_batch_id}/batch.json":
            kept_index[key] = entry
    deleted = 0
    for batch_dir in batches_root.iterdir():
        if batch_dir.is_dir() and batch_dir.name != keep_batch_id:
            for child in batch_dir.iterdir():
                if child.is_file():
                    child.unlink()
            batch_dir.rmdir()
            deleted += 1
    index_path.write_text(json.dumps(kept_index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return deleted


def latest_completed_batch_id(cache_root: Path) -> str | None:
    batches_root = cache_root / "batches"
    candidates = []
    if not batches_root.exists():
        return None
    for batch_dir in batches_root.iterdir():
        record_path = batch_dir / "batch.json"
        if not batch_dir.is_dir() or not record_path.exists():
            continue
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if record.get("status") == "completed":
                candidates.append((record_path.stat().st_mtime, record.get("extraction_batch_id")))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    candidates = [(mtime, batch_id) for mtime, batch_id in candidates if batch_id]
    return max(candidates, default=(0, None))[1]


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


def _build(manifest: dict, cache_root: Path, *, attempt_observer=None) -> dict:
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
            observer = None if attempt_observer is None else lambda event, item=evidence: attempt_observer(item, event)
            candidates.extend(_extract_with_evidence_cache(evidence, profile, provider, manifest["source_split"], cache_root, attempt_observer=observer))
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
        "prompt_version": "stage12-candidate-prompt-v5",
        "provider_metadata": provider.metadata,
        "input_sha256": {path: _sha(ROOT / path) for path in (
            "data/stage9/stage9_exit_audit.json", "data/stage10/stage10_audit.json", "data/stage11/stage11_exit_audit.json",
            "data/stage12/stage12_input_manifest.json", "data/stage6/stage6_evidence_bundle.jsonl",
            "config/stage12_statement_contract.json", "config/stage12_candidate.schema.json", "ontology/stage9_core.ttl", "ontology/stage9_shapes.ttl",
            "config/stage12_profile_routing.json", "data/stage12/stage12_representative_baseline.json",
            "config/stage12_provider.json", "config/stage12_prompt.txt", "config/stage12_extraction_response.schema.json",
            "data/registry/source_assets.jsonl",
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
    cache_root = ROOT / contract["runtime"]["cache_root"]
    cache_maintenance = prune_stale_evidence_cache(manifest, cache_root)
    events = []
    evidence_ids = [evidence_id for page in manifest.get("pages", []) for evidence_id in page.get("evidence_ids", [])]
    observer = lambda evidence, event: events.append(decorate_event(evidence, dict(event)))
    input_refs = tuple(
        {"kind": "file", "path": path, "sha256": _sha(ROOT / path)}
        for path in (
            "data/stage11/stage11_exit_audit.json", "data/stage12/stage12_input_manifest.json",
            "data/stage6/stage6_evidence_bundle.jsonl", "config/stage12_statement_contract.json",
            "config/stage12_candidate.schema.json", "ontology/stage9_core.ttl", "ontology/stage9_shapes.ttl",
            "config/stage12_profile_routing.json", "data/stage12/stage12_representative_baseline.json",
            "config/stage12_provider.json", "config/stage12_prompt.txt", "config/stage12_extraction_response.schema.json",
            "data/registry/source_assets.jsonl",
            "src/turbine_kg/extraction/semantic.py",
        )
    )
    try:
        batch, payload = run_with_cache(
        cache_root=cache_root,
        operation=contract["runtime"]["operation"],
        input_refs=input_refs,
        cache_context=({"extractor": "profile_routing_v1", "provider": provider_from_config().provider_id}, {"contract": _sha(ROOT / "config/stage12_statement_contract.json"), "profile_routing": _sha(ROOT / "config/stage12_profile_routing.json"), "provider_config": _sha(ROOT / "config/stage12_provider.json"), "prompt": _sha(ROOT / "config/stage12_prompt.txt"), "extractor_source": _sha(ROOT / "src/turbine_kg/extraction/semantic.py")} ),
        output=lambda: _build(manifest, cache_root, attempt_observer=observer),
        schema_path=ROOT / "config/runtime_run.schema.json",
        force=force,
        validate_input=lambda: _gate(manifest),
        validate_output=lambda result: (validate_candidate_payload(result), to_stage9_runtime_payload(result["candidates"])),
        )
    except Exception:
        if events:
            write_failure_summary(events, run_kind="development_batch", evidence_ids=evidence_ids, cache_maintenance=cache_maintenance, status="failed")
        raise
    cache_maintenance["batch_records_deleted"] = prune_stale_batch_records(cache_root, batch.extraction_batch_id)
    if events:
        write_failure_summary(events, run_kind="development_batch", evidence_ids=evidence_ids, cache_maintenance=cache_maintenance)
    STAGE12.mkdir(parents=True, exist_ok=True)
    (STAGE12 / "stage12_development_candidates.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return batch.as_dict(), payload


def evaluate_development(payload: dict) -> dict:
    gold = _jsonl(ROOT / "data/stage11/stage11_statement_development_samples.jsonl")
    report = compare_candidates(payload["candidates"], gold, gold_exhaustive=False)
    robustness_path = STAGE12 / "stage12_robustness_evaluation.json"
    report.update({"schema_version": 1, "stage": "12", "artifact_kind": "stage12_development_evaluation", "status": "completed", "formal_release": False, "evaluator_version": "stage12-field-evaluator-v3", "holdout_used_for_tuning": False, "blind_read": False, "real_llm_execution": payload.get("provider_metadata", {}).get("mode") == "real_llm", "candidate_artifact": "data/stage12/stage12_development_candidates.json", "gold_artifact": "data/stage11/stage11_statement_development_samples.jsonl", "input_sha256": {"candidate": _sha(STAGE12 / "stage12_development_candidates.json"), "gold": _sha(ROOT / "data/stage11/stage11_statement_development_samples.jsonl"), "manifest": _sha(STAGE12 / "stage12_input_manifest.json"), "routing": _sha(ROOT / "config/stage12_profile_routing.json"), "baseline": _sha(STAGE12 / "stage12_representative_baseline.json"), "contract": _sha(ROOT / "config/stage12_statement_contract.json"), "provider_config": _sha(ROOT / "config/stage12_provider.json"), "prompt": _sha(ROOT / "config/stage12_prompt.txt"), "response_schema": _sha(ROOT / "config/stage12_extraction_response.schema.json"), "registry": _sha(ROOT / "data/registry/source_assets.jsonl"), "evaluator": "stage12-field-evaluator-v3"}})
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

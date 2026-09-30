"""Guarded Stage 12 Reserve execution and evaluation path.

The real Reserve path is intentionally unavailable while the freeze manifest
is only prepared.  Tests may exercise the same orchestration with synthetic
Evidence and an injected fixture provider; synthetic runs never read the
Reserve Evidence file and never write a Reserve Candidate artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from turbine_kg.extraction.semantic import (
    ExternalLLMProvider,
    ProfileRouter,
    compare_candidates,
    provider_from_config,
    to_stage9_runtime_payload,
    validate_candidate_against_evidence,
    validate_candidate_evidence_binding,
    validate_candidate_payload,
)

try:
    from scripts.build_stage12_reserve_freeze_manifest import MANIFEST_PATH, sha256, verify_manifest
except ModuleNotFoundError:
    from build_stage12_reserve_freeze_manifest import MANIFEST_PATH, sha256, verify_manifest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data/stage11/evaluation_sample_registry.json"
EVIDENCE_PATH = ROOT / "data/stage12/stage12_reserve_evidence.jsonl"
GOLD_PATH = ROOT / "data/stage12/stage12_reserve_gold.jsonl"
ADJUDICATION_AUDIT_PATH = ROOT / "data/stage12/stage12_reserve_gold_audit.json"
RESERVE_SPLIT = "acceptance_holdout_reserve"
RUN_MARKER = ROOT / "data/stage12/stage12_reserve_execution_marker.json"
RESERVE_CANDIDATE_PATH = ROOT / "data/stage12/stage12_reserve_candidates.json"
RESERVE_ACCEPTANCE_PATH = ROOT / "data/stage12/stage12_reserve_acceptance.json"
RESERVE_DISAGREEMENT_PATH = ROOT / "data/stage12/stage12_reserve_disagreement_adjudication.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def provider_runtime_fingerprint(provider: Any) -> str:
    """Pin endpoint/model identifiers and failover policy, never credentials."""
    if hasattr(provider, "primary") and hasattr(provider, "backup"):
        identity = {
            "primary": provider.primary.model_config_identifier,
            "backup": provider.backup.model_config_identifier,
            "failover_policy": provider.policy_fingerprint,
        }
    else:
        identity = {"primary": provider.model_config_identifier, "backup": None, "failover_policy": None}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_reserve_registry(registry: dict, evidence_rows: list[dict]) -> list[str]:
    issues: list[str] = []
    records = [item for item in registry.get("records", []) if item.get("split") == RESERVE_SPLIT and item.get("task") == "statement"]
    if len(records) != 5:
        issues.append("reserve_record_count")
    registry_keys = [(item.get("document_logical_id"), item.get("revision_id"), item.get("physical_page")) for item in records]
    evidence_keys = [(item.get("document_logical_id"), item.get("revision_id"), item.get("physical_page")) for item in evidence_rows]
    if len(registry_keys) != len(set(registry_keys)) or len(evidence_keys) != len(set(evidence_keys)) or set(registry_keys) != set(evidence_keys):
        issues.append("reserve_page_identity_set")
    evidence_by_key = {(item.get("document_logical_id"), item.get("revision_id"), item.get("physical_page")): item for item in evidence_rows}
    for record in records:
        if record.get("frozen") is not True or record.get("review_status") != "reserved" or (record.get("independence") or {}).get("statement") is not True:
            issues.append(str(record.get("sample_id")) + ":not_frozen")
        key = (record.get("document_logical_id"), record.get("revision_id"), record.get("physical_page"))
        evidence = evidence_by_key.get(key)
        if (
            evidence is None
            or evidence.get("sample_id") != record.get("sample_id")
            or evidence.get("page_analysis_text_sha256") != record.get("source_text_sha256")
            or evidence.get("source_text_sha256") != hashlib.sha256(
                str(evidence.get("source_text") or "").encode("utf-8")
            ).hexdigest()
            or evidence.get("coverage_scope") != "selected_region_only"
        ):
            issues.append(str(record.get("sample_id")) + ":evidence_lineage")
    return sorted(set(issues))


def build_execution_plan(*, manifest_path: Path = MANIFEST_PATH, registry_path: Path = REGISTRY_PATH, evidence_path: Path = EVIDENCE_PATH) -> dict:
    if not manifest_path.is_file():
        return {"source_split": RESERVE_SPLIT, "manifest_check": {"valid": False, "issues": ["missing_manifest"], "human_approval_present": False}, "registry_issues": [], "evidence_ids": [], "evidence_count": 0, "gold_status": "unknown", "freeze_status": "missing", "execution_allowed": False}
    manifest = _read(manifest_path)
    manifest_check = verify_manifest(manifest, ROOT)
    registry = _read(registry_path)
    evidence_rows = _jsonl(evidence_path)
    registry_issues = validate_reserve_registry(registry, evidence_rows)
    audit = _read(ADJUDICATION_AUDIT_PATH) if ADJUDICATION_AUDIT_PATH.is_file() else {}
    adjudication_ready = (
        audit.get("freeze_eligibility") == "READY_TO_FREEZE"
        and (audit.get("adjudication_validation") or {}).get("complete_current_and_independently_provenanced") is True
    )
    artifacts_absent = not any(path.exists() for path in (RUN_MARKER, RESERVE_CANDIDATE_PATH, RESERVE_ACCEPTANCE_PATH))
    return {
        "source_split": RESERVE_SPLIT,
        "manifest_check": manifest_check,
        "registry_issues": registry_issues,
        "evidence_ids": [row.get("evidence_id") for row in evidence_rows],
        "evidence_count": len(evidence_rows),
        "gold_status": audit.get("gold_status", "unknown"),
        "adjudication_ready": adjudication_ready,
        "freeze_status": manifest.get("freeze_status"),
        "execution_allowed": bool(manifest_check["valid"] and not registry_issues and manifest_check["human_approval_present"] and adjudication_ready and artifacts_absent and len(evidence_rows) == 5),
    }


def execute_reserve(
    *,
    provider: Any = None,
    synthetic: bool = False,
    evidence_rows: list[dict] | None = None,
    gold_rows: list[dict] | None = None,
    router: ProfileRouter | None = None,
    write_artifacts: bool = False,
) -> dict:
    """Run one guarded extraction/evaluation batch.

    A real run requires a human-approved manifest and a one-shot marker.  The
    only pre-freeze path is synthetic and fixture-only.
    """
    if synthetic:
        if not evidence_rows or any(row.get("split") != "synthetic" for row in evidence_rows):
            raise PermissionError("synthetic Reserve test requires synthetic-only Evidence")
        if provider is None or getattr(provider, "metadata", {}).get("mode") != "fixture":
            raise PermissionError("synthetic Reserve test requires an injected fixture provider")
        if write_artifacts:
            raise PermissionError("synthetic execution cannot write Reserve artifacts")
    else:
        if not write_artifacts:
            raise PermissionError("real Reserve extraction requires the one-shot artifact path")
        plan = build_execution_plan()
        if not plan["execution_allowed"]:
            raise PermissionError("Reserve execution is blocked until the freeze manifest is human-approved")
        if provider is None:
            provider = provider_from_config()
        if getattr(provider, "metadata", {}).get("mode") != "real_llm":
            raise PermissionError("real Reserve execution requires the configured real LLM provider")
        if provider_runtime_fingerprint(provider) != _read(MANIFEST_PATH).get("provider_runtime_fingerprint"):
            raise PermissionError("Reserve provider/model does not match the approved freeze manifest")
        evidence_rows = _jsonl(EVIDENCE_PATH)
        gold_rows = _jsonl(GOLD_PATH)
    if router is None:
        router = ProfileRouter()
    evidence_rows = evidence_rows or []
    gold_rows = gold_rows or []
    # Resolve every route before consuming the one-shot Reserve attempt. Route
    # construction only inspects local configuration and does not expose text.
    extractors = [router.extractor_for(evidence, split=RESERVE_SPLIT, provider=provider) for evidence in evidence_rows]
    if isinstance(provider, ExternalLLMProvider):
        denied = [evidence.get("evidence_id") for evidence, extractor in zip(evidence_rows, extractors, strict=True) if not extractor.profile.external_llm_allowed]
        if denied:
            raise PermissionError(f"Reserve source permission denies external processing: {denied}")
    if not synthetic:
        # Reserve exposure starts immediately before the first provider call.
        # Exclusive creation makes a provider failure consume the attempt.
        marker = {"status": "started", "source_split": RESERVE_SPLIT, "freeze_manifest_sha256": sha256(MANIFEST_PATH), "provider_runtime_fingerprint": provider_runtime_fingerprint(provider), "evidence_ids": plan["evidence_ids"]}
        with RUN_MARKER.open("x", encoding="utf-8") as handle:
            json.dump(marker, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    candidates: list[dict] = []
    review_diagnostics: list[dict] = []
    for evidence, extractor in zip(evidence_rows, extractors, strict=True):
        extracted = extractor.extract(evidence)
        for candidate in extracted:
            validate_candidate_evidence_binding(candidate, evidence)
            validate_candidate_against_evidence(candidate, evidence)
        candidates.extend(extracted)
        review_diagnostics.extend(getattr(extractor, "last_review_diagnostics", []) or [])
    if not candidates:
        raise ValueError("Reserve extraction returned no candidates")
    payload = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "engineering_statement_candidates",
        "status": "candidate_only",
        "formal_release": False,
        "producer": "scripts/stage12_reserve_pipeline.py",
        "inputs": {"source_split": RESERVE_SPLIT},
        "extraction_profile": "profile_routing_v1",
        "provider_id": getattr(provider, "provider_id", "injected_fixture"),
        "prompt_version": getattr(provider, "metadata", {}).get("prompt_version", "synthetic"),
        "provider_metadata": getattr(provider, "metadata", {}),
        "candidates": candidates,
        "review_diagnostics": sorted(review_diagnostics, key=lambda item: (item["evidence_id"], item.get("candidate_id", ""), item["code"], item["message"])),
    }
    validate_candidate_payload(payload)
    to_stage9_runtime_payload(candidates)
    evidence_by_id = {row["evidence_id"]: row for row in evidence_rows}
    evaluation = compare_candidates(candidates, gold_rows, gold_exhaustive=False, evidence_by_id=evidence_by_id)
    report = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_reserve_acceptance",
        "status": "awaiting_semantic_adjudication" if not synthetic else "synthetic_completed",
        "formal_release": False,
        "source_split": RESERVE_SPLIT,
        "one_shot": True,
        "synthetic": synthetic,
        "candidate_count": len(candidates),
        "candidate_artifact": "data/stage12/stage12_reserve_candidates.json" if not synthetic else None,
        "input_sha256": {"freeze_manifest": sha256(MANIFEST_PATH), "gold": sha256(GOLD_PATH), "evidence": sha256(EVIDENCE_PATH)} if not synthetic else {},
        "reserve_extraction_invocations": 0 if synthetic else len(evidence_rows),
        "eligible_for_final_acceptance": False,
        **evaluation,
    }
    if write_artifacts:
        RESERVE_CANDIDATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["candidate_sha256"] = sha256(RESERVE_CANDIDATE_PATH)
        RESERVE_ACCEPTANCE_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        RUN_MARKER.write_text(json.dumps({"status": "candidate_generated", "source_split": RESERVE_SPLIT, "freeze_manifest_sha256": sha256(MANIFEST_PATH), "provider_runtime_fingerprint": provider_runtime_fingerprint(provider), "candidate_sha256": sha256(RESERVE_CANDIDATE_PATH)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def finalize_reserve_acceptance(adjudication_path: Path) -> dict:
    """Apply a separately reviewed semantic disagreement record after one shot.

    The record must target the exact preliminary evaluation, Candidate and
    Gold files.  This never invokes the provider or changes Candidate/Gold.
    """
    if adjudication_path.resolve() != RESERVE_DISAGREEMENT_PATH.resolve():
        raise ValueError("Reserve disagreement adjudication must use its fixed artifact path")
    if not all(path.is_file() for path in (RESERVE_CANDIDATE_PATH, RESERVE_ACCEPTANCE_PATH, RUN_MARKER, adjudication_path)):
        raise FileNotFoundError("Reserve candidate, preliminary evaluation, marker or adjudication is missing")
    preliminary = _read(RESERVE_ACCEPTANCE_PATH)
    if preliminary.get("status") != "awaiting_semantic_adjudication":
        raise ValueError("Reserve acceptance is not awaiting semantic adjudication")
    adjudication = _read(adjudication_path)
    source_hashes = adjudication.get("source_sha256") or {}
    if (
        adjudication.get("status") != "completed"
        or not adjudication.get("reviewer")
        or source_hashes.get("candidate") != sha256(RESERVE_CANDIDATE_PATH)
        or source_hashes.get("gold") != sha256(GOLD_PATH)
        or source_hashes.get("evaluation") != sha256(RESERVE_ACCEPTANCE_PATH)
    ):
        raise ValueError("semantic adjudication is missing, incomplete or stale")
    candidate_payload = _read(RESERVE_CANDIDATE_PATH)
    validate_candidate_payload(candidate_payload)
    evidence_rows = _jsonl(EVIDENCE_PATH)
    evidence_by_id = {row["evidence_id"]: row for row in evidence_rows}
    evaluation = compare_candidates(
        candidate_payload["candidates"], _jsonl(GOLD_PATH),
        gold_exhaustive=False, evidence_by_id=evidence_by_id,
        adjudication=adjudication,
    )
    validation = evaluation.get("adjudication_validation") or {}
    if validation.get("pending_review_count") != 0 or validation.get("extra_adjudication_count") != 0:
        raise ValueError("semantic adjudication has pending or extra decisions")
    report = {
        **preliminary,
        **evaluation,
        "status": "completed",
        "gold_status": "independently_reviewed_gold",
        "semantic_adjudication_path": adjudication_path.relative_to(ROOT).as_posix(),
        "semantic_adjudication_sha256": sha256(adjudication_path),
        "preliminary_evaluation_sha256": source_hashes["evaluation"],
        "eligible_for_final_acceptance": True,
    }
    try:
        from scripts.audit_stage12_exit import _reserve_acceptance_gate, _reserve_acceptance_lineage
    except ModuleNotFoundError:
        from audit_stage12_exit import _reserve_acceptance_gate, _reserve_acceptance_lineage
    contract = _read(ROOT / "config/stage12_statement_contract.json")
    policy = contract["evaluation"]["acceptance_quality_gate"]
    report["eligible_for_final_acceptance"] = bool(
        _reserve_acceptance_lineage(report)
        and _reserve_acceptance_gate(True, report, policy)
    )
    RESERVE_ACCEPTANCE_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Guarded one-shot Stage 12 Reserve pipeline")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--execute", action="store_true", help="run the approved one-shot Reserve extraction")
    action.add_argument("--finalize", action="store_true", help="apply the independent post-run disagreement adjudication")
    action.add_argument("--provider-fingerprint", action="store_true", help="show the configured provider/model fingerprint without extraction")
    args = parser.parse_args()
    if args.provider_fingerprint:
        print(provider_runtime_fingerprint(provider_from_config()))
    elif args.execute:
        result = execute_reserve(write_artifacts=True)
        print(json.dumps({"status": result["status"], "candidate_count": result["candidate_count"]}, ensure_ascii=False))
    elif args.finalize:
        result = finalize_reserve_acceptance(RESERVE_DISAGREEMENT_PATH)
        print(json.dumps({"status": result["status"], "eligible_for_final_acceptance": result["eligible_for_final_acceptance"]}, ensure_ascii=False))
    else:
        print(json.dumps(build_execution_plan(), ensure_ascii=False, indent=2))

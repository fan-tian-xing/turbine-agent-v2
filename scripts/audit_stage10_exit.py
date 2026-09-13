"""Minimal Stage 10 exit audit for runtime identity, cache and reproducibility."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGE10 = ROOT / "data" / "stage10"
RUNTIME_ROOT = ROOT / "var" / "model_runs" / "stage10"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _run_tests() -> dict:
    command = [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "tests/stage10", "tests/stage8", "tests/stage3/test_llm_contract.py",
        "tests/registry", "tests/unit/test_project_state.py",
    ]
    result = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
    return {
        "command": command,
        "project_python": sys.executable,
        "status": "passed" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-2000:],
    }


def _audit() -> dict:
    sys.path.insert(0, str(ROOT / "src"))
    from turbine_kg.documents.identity import load_revision_catalog
    from turbine_kg.observability.runtime import RunRecord, prov_o_mapping
    from turbine_kg.registry.identity import load_asset_revision_map
    from turbine_kg.stage3.llm import _evidence_payload

    contract = _read("config/runtime_contract.json")
    schema = _read("config/runtime_run.schema.json")
    stage9 = _read("data/stage9/stage9_exit_audit.json")
    mapping = _read("data/stage8/ontology_mapping_shortlist.json")
    queue = [json.loads(line) for line in (ROOT / "data/stage8/ontology_mapping_review_queue.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    # Exercise the actual Stage 7 consumer in its isolated runtime mode.  A
    # valid cache hit is enough here; the runtime module and its gate perform
    # input, Registry, output and fingerprint validation on every access.
    runtime_command = [sys.executable, "scripts/build_stage7_terminology.py", "--runtime"]
    runtime_result = subprocess.run(runtime_command, cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
    runtime_runs = []
    index_path = RUNTIME_ROOT / "cache_index.json"
    cache_index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    for entry in cache_index.values():
        run_path = RUNTIME_ROOT / entry["run_record"]
        output_path = RUNTIME_ROOT / entry["output"]
        if run_path.is_file() and output_path.is_file():
            runtime_runs.append((run_path, output_path))
    runtime_record = None
    prov_ok = False
    structured_output_ok = False
    if runtime_runs:
        run_payload = json.loads(runtime_runs[-1][0].read_text(encoding="utf-8"))
        output_payload = json.loads(runtime_runs[-1][1].read_text(encoding="utf-8"))
        runtime_record = RunRecord(**{
            key: tuple(value) if key in {"input_refs", "config_refs", "model_attempts", "review_refs", "legacy_refs"} else value
            for key, value in run_payload.items() if key != "schema_version"
        })
        prov = prov_o_mapping(runtime_record)
        prov_ok = {item["type"] for item in prov["relations"]} >= {"used", "wasGeneratedBy", "wasDerivedFrom"}
        structured_output_ok = output_payload.get("artifact_kind") == "terminology_candidates" and isinstance(output_payload.get("candidates"), list)

    payload = _evidence_payload([{
        "statement": {"id": "statement-fixture", "text": "fixture", "object_id": "object-fixture", "scope": "{}"},
        "sources": [{"evidence": {"id": "evidence-fixture", "text": "quote"}, "page": {"physical_page": 1}}],
        "document": {"title": "fixture"},
    }])
    payload_minimal_ok = "relative_path" not in json.dumps(payload, ensure_ascii=False)

    revisions = load_revision_catalog(ROOT / "config/revision_identity.tsv")
    asset_assignments = load_asset_revision_map(ROOT / "config/asset_revision_identity.tsv")
    registry_asset_count = sum(1 for line in (ROOT / "data/registry/source_assets.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
    checks = {
        "stage9_gate": stage9.get("status") == "complete" and stage9.get("next_stage_allowed") is True,
        "runtime_contract_and_schema": contract.get("stage") == "10" and schema.get("$schema", "").endswith("2020-12/schema"),
        "stage8_single_review_state": all("review_status" not in row and row.get("mapping_review_decision") in {"accepted", "deferred"} for row in mapping.get("shortlist", []) + mapping.get("deferred_candidates", [])),
        "stage8_original_confirmation_blocks_queue": len(queue) == 0,
        "llm_payload_minimal": payload_minimal_ok,
        "runtime_consumer_executed": runtime_result.returncode == 0 and bool(runtime_runs),
        "runtime_structured_cache": structured_output_ok and all(json.loads(path.read_text(encoding="utf-8")).get("status") == "succeeded" for path, _ in runtime_runs),
        "runtime_prov_mapping": prov_ok,
        "revision_catalog_readable": len(revisions) >= 1 and all(record.revision_id.startswith("rev-") for record in revisions),
        "asset_revision_assignments_cover_registry": len(asset_assignments) == registry_asset_count and all(revision.startswith("rev-") for _, revision in asset_assignments.values()),
    }
    targeted = _run_tests()
    checks["targeted_tests"] = targeted["status"] == "passed"
    full = {"status": "not_run"}
    if checks["targeted_tests"]:
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
        result = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True)
        full = {"command": command, "project_python": sys.executable, "status": "passed" if result.returncode == 0 else "failed", "returncode": result.returncode, "stdout_tail": result.stdout[-5000:], "stderr_tail": result.stderr[-2000:]}
        checks["full_project_tests"] = result.returncode == 0
    else:
        checks["full_project_tests"] = False
    blockers = [name for name, passed in checks.items() if not passed]
    inputs = {
        relative: {"path": relative, "sha256": _sha(ROOT / relative)}
        for relative in (
            "config/runtime_contract.json", "config/runtime_run.schema.json",
            "data/stage9/stage9_exit_audit.json", "data/stage8/ontology_mapping_shortlist.json",
            "data/stage8/ontology_mapping_review_queue.jsonl", "src/turbine_kg/observability/runtime.py",
            "scripts/build_stage7_terminology.py", "src/turbine_kg/stage3/llm.py",
            "scripts/audit_stage10_exit.py", "data/project_state.json",
            "config/asset_revision_identity.tsv",
            "tests/stage10/test_stage10_runtime.py",
        )
    }
    return {
        "schema_version": 1,
        "stage": "10",
        "artifact_kind": "stage10_exit_audit",
        "status": "complete" if not blockers else "blocked",
        "formal_release": False,
        "producer": "scripts/audit_stage10_exit.py",
        "inputs": inputs,
        "outputs": {"runtime_cache": "var/model_runs/stage10", "run_schema": "config/runtime_run.schema.json", "runtime_contract": "config/runtime_contract.json", "exit_audit": "data/stage10/stage10_audit.json"},
        "counts": {"runtime_cache_entries": len(runtime_runs), "stage8_shortlist": len(mapping.get("shortlist", [])), "stage8_queue": len(queue), "revision_records": len(revisions)},
        "checks": checks,
        "runtime_result": {"command": runtime_command, "returncode": runtime_result.returncode, "stdout_tail": runtime_result.stdout[-2000:], "stderr_tail": runtime_result.stderr[-2000:]},
        "test_result": {"targeted": targeted, "full_project": full},
        "review": {"mapping_acceptance_is_separate_from_knowledge_approval": True, "formal_release": False, "namespace": "research namespace retained; freeze before formal Release"},
        "failure_isolation": "缓存损坏、输入变化或失败运行不得替换已有成功结果；原始资料、冻结 Evidence、旧版目录和 Neo4j 均不写入。",
        "zero_tolerance_errors": blockers,
        "blockers": blockers,
        "next_stage_allowed": not blockers,
        "next_stage": "Stage 11 controlled Evidence/Statement preparation" if not blockers else "Stage 10 runtime provenance and reproducibility",
    }


def main() -> int:
    try:
        audit = _audit()
    except Exception as error:
        audit = {"schema_version": 1, "stage": "10", "artifact_kind": "stage10_exit_audit", "status": "blocked", "formal_release": False, "producer": "scripts/audit_stage10_exit.py", "checks": {"audit_completed": False}, "zero_tolerance_errors": ["audit_execution_failed"], "blockers": ["audit_execution_failed"], "failure": {"type": type(error).__name__, "message": str(error)}, "next_stage_allowed": False, "next_stage": "Stage 10 runtime provenance and reproducibility"}
    STAGE10.mkdir(parents=True, exist_ok=True)
    (STAGE10 / "stage10_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "blockers": audit["blockers"], "next_stage_allowed": audit["next_stage_allowed"]}, ensure_ascii=False))
    return 0 if not audit["blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

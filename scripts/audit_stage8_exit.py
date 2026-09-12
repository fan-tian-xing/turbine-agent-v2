"""Independently audit the Stage 8 generated ontology and review boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAGE7 = ROOT / "data" / "stage7"
STAGE8 = ROOT / "data" / "stage8"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_tests() -> dict:
    command = [sys.executable, "-m", "pytest", "-q", "tests/stage8", "tests/unit/test_project_state.py"]
    result = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": "src"},
        text=True,
        capture_output=True,
    )
    return {
        "command": " ".join(command),
        "project_python": sys.executable,
        "status": "passed" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }


def _audit() -> dict:
    contract = _read(ROOT / "config" / "ontology_contract.json")
    candidates_payload = _read(STAGE7 / "terminology_candidates.json")
    mapping = _read(STAGE8 / "ontology_mapping_shortlist.json")
    overlay = _jsonl(STAGE8 / "ontology_mapping_review_overlay.jsonl")
    queue = _jsonl(STAGE8 / "ontology_mapping_review_queue.jsonl")
    ttl = (ROOT / "ontology" / "minimal_turbine.ttl").read_text(encoding="utf-8")

    candidates = candidates_payload.get("candidates", [])
    candidate_by_id = {row.get("candidate_id"): row for row in candidates}
    mapped_rows = mapping.get("shortlist", []) + mapping.get("deferred_candidates", [])
    mapped_by_id = {row.get("candidate_id"): row for row in mapped_rows}
    allowed_types = set(contract["candidate_mapping"]["allowed_candidate_types"])
    top_classes = {row["id"] for row in contract["top_level_classes"]}
    runtime_classes = {row["id"] for row in contract["runtime_classes"]}
    class_ids = top_classes | runtime_classes
    object_properties = {row["id"] for row in contract["object_properties"]}
    datatype_properties = {row["id"] for row in contract["datatype_properties"]}
    all_property_ids = object_properties | datatype_properties
    known_ids = class_ids | all_property_ids

    path_shape_ok = True
    for definition in contract.get("capability_paths", {}).values():
        path = definition.get("path", [])
        if definition.get("status") == "deferred":
            path_shape_ok = path_shape_ok and path == []
        else:
            path_shape_ok = path_shape_ok and len(path) >= 3 and all(token in known_ids for token in path)
            path_shape_ok = path_shape_ok and all(
                (token in class_ids) == (index % 2 == 0) for index, token in enumerate(path)
            )

    overlay_binding_ok = all(
        item.get("candidate_id") in mapped_by_id
        and item.get("candidate_content_fingerprint") == candidate_by_id.get(item.get("candidate_id"), {}).get("content_fingerprint")
        for item in overlay
    )
    mapped_binding_ok = all(
        row.get("candidate_id") in candidate_by_id
        and row.get("candidate_content_fingerprint") == candidate_by_id.get(row.get("candidate_id"), {}).get("content_fingerprint")
        and candidate_by_id[row["candidate_id"]].get("review_status") == "candidate_only"
        for row in mapped_rows
    )
    content_clean_ok = all(
        row.get("candidate_type") in allowed_types
        and not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:\s*[A-Za-z°μ%]+)?", row.get("normalized_form", ""))
        and not row.get("normalized_form", "").startswith(("当", "应", "如果", "若", "如", "在", "并", "且", "则", "不得", "第"))
        for row in mapping.get("shortlist", [])
    )
    active_ids = {row.get("candidate_id") for row in mapping.get("shortlist", [])}
    queue_binding_ok = all(
        item.get("candidate_id") in active_ids
        and item.get("candidate_content_fingerprint") == mapped_by_id.get(item.get("candidate_id"), {}).get("candidate_content_fingerprint")
        for item in queue
    )
    decisions_ok = all(
        row.get("candidate_disposition") in contract["candidate_mapping"]["candidate_disposition_schema"]
        for row in mapped_rows
    )
    hierarchy_relations_ok = all(
        relation.get("relation") in {"broader_than", "narrower_than"}
        and relation.get("target_candidate_id") in mapped_by_id
        and relation.get("target_candidate_id") != row.get("candidate_id")
        for row in mapped_rows
        for relation in row.get("hierarchy_relations", [])
    )
    no_early_promotion_ok = (
        candidates_payload.get("status") == "candidate_only"
        and candidates_payload.get("automatic_promotion") is False
        and mapping.get("formal_release") is False
        and mapping.get("automatic_promotion") is False
        and all(row.get("promotion_status", "candidate_only") == "candidate_only" for row in mapped_rows)
    )
    ttl_classes_ok = all(f"tv2:{class_id} a owl:Class" in ttl for class_id in class_ids)
    ttl_properties_ok = all(
        f"tv2:{property_id} a owl:{'ObjectProperty' if property_id in object_properties else 'DatatypeProperty'}" in ttl
        for property_id in all_property_ids
    )
    ttl_future_scope_ok = not any(
        token in ttl for token in ("tv2:EngineeringCase", "tv2:Release", "owl:sameAs")
    )
    checks = {
        "contract_stage8": contract.get("stage") == "8" and contract.get("artifact_kind") == "ontology_contract",
        "capability_paths_are_explicit": path_shape_ok and set(contract.get("capability_paths", {})) == {f"cap-{index:02d}" for index in range(1, 11)},
        "stage7_candidate_only_input": candidates_payload.get("status") == "candidate_only",
        "shortlist_and_deferred_bind_to_stage7": mapped_binding_ok,
        "review_overlay_fingerprints_bind_to_candidates": overlay_binding_ok,
        "review_queue_is_bound_to_active_shortlist": queue_binding_ok,
        "shortlist_content_quality_gate": content_clean_ok,
        "candidate_dispositions_recorded": decisions_ok,
        "reviewed_hierarchy_relations_bind_to_candidates": hierarchy_relations_ok,
        "automatic_promotion_and_formal_release_disabled": no_early_promotion_ok,
        "owl_classes_match_contract": ttl_classes_ok,
        "owl_properties_match_contract": ttl_properties_ok,
        "future_scope_not_generated": ttl_future_scope_ok,
    }
    test_result = _run_tests()
    checks["stage8_focused_tests_passed"] = test_result["status"] == "passed"
    blockers = [name for name, passed in checks.items() if not passed]
    status = "complete" if not blockers and not queue else "in_progress"
    return {
        "schema_version": 1,
        "stage": "8",
        "artifact_kind": "stage8_exit_audit",
        "status": status,
        "formal_release": False,
        "producer": "scripts/audit_stage8_exit.py",
        "inputs": {
            "ontology_contract": {"path": "config/ontology_contract.json", "sha256": _sha(ROOT / "config" / "ontology_contract.json")},
            "terminology_candidates": {"path": "data/stage7/terminology_candidates.json", "sha256": _sha(STAGE7 / "terminology_candidates.json")},
            "mapping_shortlist": {"path": "data/stage8/ontology_mapping_shortlist.json", "sha256": _sha(STAGE8 / "ontology_mapping_shortlist.json")},
            "review_overlay": {"path": "data/stage8/ontology_mapping_review_overlay.jsonl", "sha256": _sha(STAGE8 / "ontology_mapping_review_overlay.jsonl")},
            "review_queue": {"path": "data/stage8/ontology_mapping_review_queue.jsonl", "sha256": _sha(STAGE8 / "ontology_mapping_review_queue.jsonl")},
            "ontology": {"path": "ontology/minimal_turbine.ttl", "sha256": _sha(ROOT / "ontology" / "minimal_turbine.ttl")},
        },
        "outputs": {
            "ontology": "ontology/minimal_turbine.ttl",
            "mapping_shortlist": "data/stage8/ontology_mapping_shortlist.json",
            "review_queue": "data/stage8/ontology_mapping_review_queue.jsonl",
            "exit_audit": "data/stage8/stage8_exit_audit.json",
        },
        "counts": {
            "stage7_candidate_count": len(candidates),
            "shortlist_count": len(mapping.get("shortlist", [])),
            "deferred_count": len(mapping.get("deferred_candidates", [])),
            "review_pending_count": len(queue),
            "accepted_count": sum(row.get("mapping_review_decision") == "accepted" for row in mapping.get("shortlist", [])),
        },
        "checks": checks,
        "failure_isolation": "不合格候选保留在 deferred_candidates 或 review_queue，不写入正式词汇、Release 或 Neo4j。",
        "rollback": "重建 Stage 8 生成产物；保留 Stage 7 candidates 与人工 review overlay 不变。",
        "zero_tolerance_errors": blockers,
        "blockers": blockers,
        "test_result": test_result,
        "next_stage_allowed": not blockers and not queue,
        "next_stage": "Stage 9 OWL/SHACL semantic authority package" if not blockers and not queue else "Stage 8 manual ontology mapping review",
    }


def main() -> None:
    audit = _audit()
    (STAGE8 / "stage8_exit_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status": audit["status"],
        "blockers": audit["blockers"],
        "review_pending_count": audit["counts"]["review_pending_count"],
        "next_stage_allowed": audit["next_stage_allowed"],
    }, ensure_ascii=False))
    if audit["blockers"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

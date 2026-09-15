"""Validate the Stage 11 sample boundary and write its exit record."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/stage11/stage11_entry_audit.json"
DEV_PATH = ROOT / "data/stage11/stage11_statement_development_samples.jsonl"
HOLDOUT_PATH = ROOT / "data/stage11/stage11_statement_holdout.jsonl"
HOLDOUT_EVIDENCE_PATH = ROOT / "data/stage11/stage11_holdout_evidence.jsonl"
REGISTRY_PATH = ROOT / "data/stage11/evaluation_sample_registry.json"


def _read(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _required_row_fields(row: dict) -> bool:
    required = {
        "statement_id", "statement_type", "statement_text", "subject_entity_id", "predicate",
        "object_value", "evidence_bindings", "applicability_scope", "quantities",
        "normative_modality", "negation_scope", "entity_alignment", "review_status",
        "reviewer", "reviewer_type", "review_reason", "source_text_sha256",
    }
    return required <= row.keys()


def audit() -> dict:
    contract = _read("config/stage11_statement_contract.json")
    stage6 = _read("data/stage6/stage6_exit_audit.json")
    stage7 = _read("data/stage7/terminology_input_manifest.json")
    stage9 = _read("data/stage9/stage9_exit_audit.json")
    stage10 = _read("data/stage10/stage10_audit.json")
    dev = _jsonl(DEV_PATH)
    holdout = _jsonl(HOLDOUT_PATH)
    holdout_evidence = _jsonl(HOLDOUT_EVIDENCE_PATH)
    registry = _read("data/stage11/evaluation_sample_registry.json")
    bundle = _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")
    canonical = {row["evidence"]["evidence_id"]: row["evidence"] for row in bundle}
    holdout_canonical = {row["evidence_id"]: row for row in holdout_evidence}
    docs = {"DL5190.3", "D300N", "DLT863", "HAF103", "auxiliary_installation_book"}
    dev_pages = {(r.get("document_key"), r.get("physical_page")) for r in registry["records"] if r.get("split") == "development_regression_golden"}
    holdout_pages = {(r.get("document_key"), r.get("physical_page")) for r in holdout if r.get("split") == "acceptance_holdout"}
    excluded = stage7.get("input_boundary", {}).get("excluded_source_classes", {})
    isolation = set(excluded) == {"formal_case_materials", "holdout_materials", "blind_test_materials"}
    stage9_gate = stage9.get("status") == "complete" and stage9.get("next_stage_allowed") is True
    stage10_gate = stage10.get("status") == "complete" and stage10.get("next_stage_allowed") is True
    contract_ok = (
        contract.get("stage") == "11" and contract.get("formal_release") is False
        and set(contract.get("statement_types", [])) >= {"fact", "requirement", "procedure", "condition", "observation", "verification", "limitation"}
        and set(contract.get("evidence_support_types", [])) >= {"direct", "partial", "context"}
        and {"holdout_isolation_is_task_specific", "holdout_is_not_used_for_statement_tuning"} <= set(contract.get("requirements", {}))
    )
    dev_ids = [r.get("statement_id") for r in dev]
    holdout_ids = [r.get("statement_id") for r in holdout]
    dev_evidence_ids = [b.get("evidence_id") for r in dev for b in r.get("evidence_bindings", [])]
    holdout_evidence_ids = [b.get("evidence_id") for r in holdout for b in r.get("evidence_bindings", [])]
    dev_ok = (
        len(dev) >= 9 and {r.get("document_key") for r in dev} == docs
        and len(dev_ids) == len(set(dev_ids)) and all(_required_row_fields(r) for r in dev)
        and all(r.get("object_value") and r.get("applicability_scope") for r in dev)
        and all(r.get("review_status") == "accepted" and r.get("formal_release") is False for r in dev)
        and all(eid in canonical for eid in dev_evidence_ids)
        and all(r.get("source_text_sha256") == canonical[eid].get("source_text_sha256") for r in dev for eid in [b.get("evidence_id") for b in r.get("evidence_bindings", [])] if eid in canonical)
    )
    holdout_ok = (
        len(holdout) == 15 and len(holdout_pages) == 15 and {r.get("document_key") for r in holdout} == docs
        and all(sum(r.get("document_key") == d for r in holdout) == 3 for d in docs)
        and not (dev_pages & holdout_pages) and len(holdout_ids) == len(set(holdout_ids))
        and all(_required_row_fields(r) for r in holdout)
        and all(r.get("object_value") and r.get("applicability_scope") for r in holdout)
        and all(r.get("review_status") == "accepted" and r.get("formal_release") is False and r.get("independent_for_statement") is True for r in holdout)
        and all(eid in holdout_canonical for eid in holdout_evidence_ids)
        and all(r.get("source_text_sha256") == holdout_canonical[eid].get("source_text_sha256") for r in holdout for eid in [b.get("evidence_id") for b in r.get("evidence_bindings", [])] if eid in holdout_canonical)
        and all(len(r.get("review_rounds", [])) == 2 and all(round_.get("status") == "accepted" and round_.get("reviewer_type") == "ai_cross_review" for round_ in r["review_rounds"]) for r in holdout)
    )
    records = registry.get("records", [])
    registry_splits = {split: [r for r in records if r.get("split") == split] for split in {r.get("split") for r in records}}
    registry_ok = (
        len(records) == 56 and len(registry_splits.get("development_regression_golden", [])) == 36
        and len(registry_splits.get("acceptance_holdout", [])) == 15 and len(registry_splits.get("acceptance_holdout_reserve", [])) == 5
        and registry.get("development", {}).get("trial_page_subset_count") == 15
        and len({(r.get("document_key"), r.get("physical_page")) for r in records}) == len(records)
        and registry.get("blind_test", {}).get("read_by_stage11") is False
    )
    checks = {
        "stage6_boundary_preserved": stage6.get("golden_sample_page_count") == 36,
        "stage7_and_case_isolation_preserved": isolation,
        "stage9_gate": stage9_gate,
        "stage10_gate": stage10_gate,
        "statement_contract_present": contract_ok,
        "development_statement_samples_frozen": dev_ok,
        "holdout_frozen": holdout_ok,
        "holdout_independence": holdout_ok and all(r.get("independent_for_entity_alignment_algorithm") is True for r in holdout),
        "evidence_bindings_resolve": dev_ok and holdout_ok,
        "source_hashes_match": dev_ok and holdout_ok,
        "sample_registry_consistent": registry_ok,
        "blind_materials_excluded": registry.get("blind_test", {}).get("read_by_stage11") is False,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    checks["stage12_entry_allowed"] = not blockers
    return {
        "schema_version": 1, "stage": "11", "artifact_kind": "stage11_entry_audit", "status": "complete" if not blockers else "in_progress", "formal_release": False,
        "producer": "scripts/audit_stage11_exit.py",
        "inputs": {
            "stage6_exit_audit": "data/stage6/stage6_exit_audit.json", "stage6_canonical_bundle": "data/stage6/stage6_evidence_bundle.jsonl", "stage7_input_manifest": "data/stage7/terminology_input_manifest.json", "stage9_exit_audit": "data/stage9/stage9_exit_audit.json", "stage10_exit_audit": "data/stage10/stage10_audit.json", "statement_contract": "config/stage11_statement_contract.json", "development_statement_samples": "data/stage11/stage11_statement_development_samples.jsonl", "holdout_statement_samples": "data/stage11/stage11_statement_holdout.jsonl", "holdout_evidence": "data/stage11/stage11_holdout_evidence.jsonl", "evaluation_sample_registry": "data/stage11/evaluation_sample_registry.json",
        },
        "input_sha256": {"statement_contract": _sha256(ROOT / "config/stage11_statement_contract.json"), "evaluation_sample_registry": _sha256(REGISTRY_PATH), "development_statement_samples": _sha256(DEV_PATH), "holdout_statement_samples": _sha256(HOLDOUT_PATH), "holdout_evidence": _sha256(HOLDOUT_EVIDENCE_PATH)},
        "sample_registry": {"development_regression_golden": {"page_count": 36, "trial_page_subset_count": 15, "statement_sample_count": len(dev), "source": "data/stage6/stage6_evidence_golden_sample.json"}, "acceptance_holdout": {"page_count": len(holdout), "pages_per_document": 3, "reserve_page_count": 5, "source": "data/stage7/terminology_input_manifest.json"}, "blind_test": {"status": "excluded", "read_by_stage11": False, "owner": "user-held evaluation boundary"}},
        "checks": checks, "counts": {"development_statement_samples": len(dev), "holdout_statement_samples": len(holdout), "holdout_evidence": len(holdout_evidence), "registry_records": len(records), "stage6_development_pages": len(dev_pages)}, "blockers": blockers,
        "next_stage_allowed": not blockers, "next_stage": "Stage 12 representative chapter semantic extraction" if not blockers else "Stage 11 controlled Statement and entity sample review", "consumers": ["tests/stage11/test_stage11_contract.py", "Stage 12 development loader", "Stage 12 holdout evaluator"],
    }


if __name__ == "__main__":
    result = audit()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "blockers": result["blockers"]}, ensure_ascii=False))

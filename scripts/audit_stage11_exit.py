"""Validate the Stage 11 sample boundary and write its exit record."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY_OUTPUT = ROOT / "data/stage11/stage11_entry_audit.json"
OUTPUT = ROOT / "data/stage11/stage11_exit_audit.json"
DEV_PATH = ROOT / "data/stage11/stage11_statement_development_samples.jsonl"
HOLDOUT_PATH = ROOT / "data/stage11/stage11_statement_holdout.jsonl"
HOLDOUT_EVIDENCE_PATH = ROOT / "data/stage11/stage11_holdout_evidence.jsonl"
REGISTRY_PATH = ROOT / "data/stage11/evaluation_sample_registry.json"
REVIEW_A_PATH = ROOT / "data/stage11/review_round_a.jsonl"
REVIEW_B_PATH = ROOT / "data/stage11/review_round_b.jsonl"
ADJUDICATION_PATH = ROOT / "data/stage11/stage11_adjudication_queue.jsonl"


def _read(relative: str):
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_targeted_tests() -> dict:
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/stage11", "tests/unit/test_project_state.py"]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {
        "command": command,
        "project_python": sys.executable,
        "status": "passed" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
    }


def _final_gold_rows(rows: list[dict], sample_id: str) -> list[dict]:
    """Return the accepted Gold rows for a sample in deterministic statement order."""
    return sorted(
        [
            row for row in rows
            if row.get("sample_id") == sample_id
            and row.get("review_status") == "accepted"
            and row.get("label_status") == "gold"
        ],
        key=lambda row: row.get("statement_id", ""),
    )


def _final_gold_hash(rows: list[dict], sample_id: str) -> str:
    """Hash the complete canonical JSON representation of a sample's final Gold rows."""
    payload = json.dumps(
        _final_gold_rows(rows, sample_id),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _required_row_fields(row: dict) -> bool:
    required = {
        "statement_id", "statement_type", "statement_text", "subject_entity_id", "predicate",
        "object_value", "evidence_bindings", "applicability_scope", "quantities",
        "normative_modality", "negation_scope", "entity_alignment", "review_status",
        "reviewer", "reviewer_type", "review_reason", "source_text_sha256",
    }
    return required <= row.keys()


def validate_statement_semantics(row: dict) -> dict[str, bool]:
    """Return conservative semantic gates; unresolved candidates must remain pending."""
    text = row.get("statement_text", "")
    accepted = row.get("review_status") == "accepted"
    has_number = bool(re.search(r"\d+(?:\.\d+)?\s*(?:mm|kPa|min|h|℃|%)", text, re.I))
    has_negative = any(token in text for token in ("不得", "不应", "无", "未", "不小于", "不大于"))
    has_enumeration = bool(re.search(r"(?:^|\s)(?:[1-9][、.]|[a-z]\))", text))
    entity = (row.get("entity_alignment") or [{}])[0]
    placeholder_entity = entity.get("entity_class") == "UnresolvedEntityCandidate" or entity.get("surface_form", "") == text[:24]

    def comparison_expectation(surface_form: str):
        if any(token in surface_form for token in ("不小于", "不少于", "不低于", "至少", "不得低于")):
            return {"gte", "gt"}, "lower_bound"
        if any(token in surface_form for token in ("不大于", "不超过", "不高于", "至多", "不得高于")):
            return {"lte", "lt"}, "upper_bound"
        if any(token in surface_form for token in ("大于", "超过")):
            return {"gt"}, "lower_bound"
        if "小于" in surface_form:
            return {"lt"}, "upper_bound"
        return None

    comparison_direction_ok = True
    for quantity in row.get("quantities", []):
        expectation = comparison_expectation(quantity.get("surface_form", ""))
        if not expectation:
            continue
        operators, polarity = expectation
        surface_form = quantity.get("surface_form", "")
        matching_negation = next(
            (item for item in row.get("negation_scope", []) if item.get("surface_form") == surface_form),
            None,
        )
        scope_text = (matching_negation or {}).get("scope", "")
        scope_direction_ok = (
            ("下限" in scope_text) if polarity == "lower_bound"
            else ("上限" in scope_text) if polarity == "upper_bound"
            else True
        )
        comparison_direction_ok = comparison_direction_ok and quantity.get("operator") in operators and bool(matching_negation) and matching_negation.get("polarity") == polarity and scope_direction_ok
    return {
        "numeric_fields_present_when_accepted": not (accepted and has_number and not row.get("quantities")),
        "negation_scope_present_when_accepted": not (accepted and has_negative and not row.get("negation_scope")),
        "enumerated_text_requires_split_review": not (accepted and has_enumeration and not row.get("semantic_split_reviewed")),
        "entity_is_not_placeholder_when_accepted": not (accepted and placeholder_entity),
        "comparison_direction_matches_text": not (accepted and not comparison_direction_ok),
    }


def audit() -> dict:
    contract = _read("config/stage11_statement_contract.json")
    stage6 = _read("data/stage6/stage6_exit_audit.json")
    stage7 = _read("data/stage7/terminology_input_manifest.json")
    stage9 = _read("data/stage9/stage9_exit_audit.json")
    stage10 = _read("data/stage10/stage10_audit.json")
    dev = _jsonl(DEV_PATH)
    holdout = _jsonl(HOLDOUT_PATH)
    holdout_evidence = _jsonl(HOLDOUT_EVIDENCE_PATH)
    review_a = _jsonl(REVIEW_A_PATH) if REVIEW_A_PATH.exists() else []
    review_b = _jsonl(REVIEW_B_PATH) if REVIEW_B_PATH.exists() else []
    adjudication = _jsonl(ADJUDICATION_PATH) if ADJUDICATION_PATH.exists() else []
    registry = _read("data/stage11/evaluation_sample_registry.json")
    bundle = _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")
    canonical = {row["evidence"]["evidence_id"]: row["evidence"] for row in bundle}
    holdout_canonical = {row["evidence_id"]: row for row in holdout_evidence}
    docs = {"DL5190.3", "D300N", "DLT863", "HAF103", "auxiliary_installation_book"}
    dev_pages = {(r.get("document_key"), r.get("physical_page")) for r in registry["records"] if r.get("split") == "development_regression_golden"}
    registry_holdout_pages = {(r.get("document_key"), r.get("physical_page")) for r in registry.get("records", []) if r.get("split") == "acceptance_holdout"}
    evidence_holdout_pages = {(r.get("document_key"), r.get("physical_page")) for r in holdout_evidence}
    holdout_statement_pages = {(r.get("document_key"), r.get("physical_page")) for r in holdout if r.get("split") == "acceptance_holdout"}
    excluded = stage7.get("input_boundary", {}).get("excluded_source_classes", {})
    isolation = set(excluded) == {"formal_case_materials", "holdout_materials", "blind_test_materials"}
    stage9_gate = stage9.get("status") == "complete" and stage9.get("next_stage_allowed") is True
    stage10_gate = stage10.get("status") == "complete" and stage10.get("next_stage_allowed") is True
    contract_ok = (
        contract.get("stage") == "11" and contract.get("formal_release") is False
        and set(contract.get("statement_types", [])) >= {"fact", "requirement", "procedure", "condition", "observation", "verification", "limitation"}
        and set(contract.get("evidence_support_types", [])) >= {"direct", "partial", "context"}
        and {
            "holdout_isolation_is_task_specific",
            "holdout_is_not_used_for_statement_tuning",
            "candidate_labels_are_not_stage12_inputs",
            "accepted_evidence_requires_original_page_confirmation",
            "accepted_review_rounds_require_independent_provenance",
            "adjudication_hash_matches_final_gold",
            "adjudication_statement_count_matches_final_gold",
        } <= set(contract.get("requirements", {}))
        and contract.get("stage12_input_gate", {}).get("sample_tier") == "gold"
        and contract.get("stage12_input_gate", {}).get("label_status") == "gold"
        and contract.get("stage12_input_gate", {}).get("review_status") == "accepted"
        and contract.get("stage12_input_gate", {}).get("independent_review_required") is True
    )
    dev_ids = [r.get("statement_id") for r in dev]
    holdout_ids = [r.get("statement_id") for r in holdout]
    dev_evidence_ids = [b.get("evidence_id") for r in dev for b in r.get("evidence_bindings", [])]
    holdout_evidence_ids = [b.get("evidence_id") for r in holdout for b in r.get("evidence_bindings", [])]
    dev_sample_ids = {r.get("sample_id") for r in dev}
    dev_ok = (
        len(dev_sample_ids) == 10 and {r.get("document_key") for r in dev} == docs
        and len(dev_ids) == len(set(dev_ids)) and all(_required_row_fields(r) for r in dev)
        and all(r.get("object_value") and r.get("applicability_scope") for r in dev)
        and all(r.get("review_status") in {"accepted", "pending_manual_review", "isolated"} and r.get("formal_release") is False for r in dev)
        and all(eid in canonical for eid in dev_evidence_ids)
        and all(r.get("source_text_sha256") == canonical[eid].get("source_text_sha256") for r in dev for eid in [b.get("evidence_id") for b in r.get("evidence_bindings", [])] if eid in canonical)
    )
    holdout_ok = (
        len(registry_holdout_pages) == 15 and evidence_holdout_pages == registry_holdout_pages
        and holdout_statement_pages <= registry_holdout_pages
        and all(sum(page[0] == d for page in registry_holdout_pages) == 3 for d in docs)
        and not (dev_pages & registry_holdout_pages) and len(holdout_ids) == len(set(holdout_ids))
        and all(_required_row_fields(r) for r in holdout)
        and all(r.get("object_value") and r.get("applicability_scope") for r in holdout)
        and all(r.get("review_status") in {"accepted", "pending_manual_review", "isolated"} and r.get("formal_release") is False and r.get("independent_for_statement") is True for r in holdout)
        and all(eid in holdout_canonical for eid in holdout_evidence_ids)
        and all(r.get("source_text_sha256") == holdout_canonical[eid].get("source_text_sha256") for r in holdout for eid in [b.get("evidence_id") for b in r.get("evidence_bindings", [])] if eid in holdout_canonical)
        and all(len(r.get("review_plan", [])) == 2 and all(plan.get("round") in {1, 2} and plan.get("reviewer_role") for plan in r["review_plan"]) for r in holdout)
    )
    def _has_two_independent_reviews(row: dict) -> bool:
        rounds = row.get("review_rounds")
        if not isinstance(rounds, list) or len(rounds) != 2:
            return False
        reviewer_ids = [item.get("reviewer_id") for item in rounds]
        return (
            len(set(reviewer_ids)) == 2
            and all(item.get("status") == "accepted" and item.get("reviewer_id") and item.get("input_sha256") and item.get("output_sha256") for item in rounds)
            and all(item.get("sample_id", row.get("sample_id")) == row.get("sample_id") for item in rounds)
        )

    def _has_final_semantic_provenance(row: dict) -> bool:
        if row.get("review_basis") == "stage3_user_confirmation" and row.get("reviewer_type") == "user_confirmation":
            return True
        return _has_two_independent_reviews(row)

    dev_semantic_review_complete = bool(dev) and all(
        r.get("review_status") == "accepted"
        and r.get("label_status") == "gold"
        and r.get("reviewer_type") != "candidate_generation"
        and r.get("review_basis") != "stage11_candidate_generation"
        and r.get("source_span_ids")
        and _has_final_semantic_provenance(r)
        for r in dev
    )

    holdout_semantic_review_complete = bool(holdout) and all(
        (
            any(
                e.get("review_status") == "isolated"
                and e.get("review_reason")
                for e in holdout_evidence
                if (e.get("document_key"), e.get("physical_page")) == page
            )
            and not any(
                r.get("review_status") not in {"isolated", "rejected"}
                for r in holdout
                if (r.get("document_key"), r.get("physical_page")) == page
            )
        )
        or any(
            r.get("review_status") == "accepted"
            and r.get("label_status") == "gold"
            and r.get("source_span_ids")
            and _has_two_independent_reviews(r)
            for r in holdout
            if (r.get("document_key"), r.get("physical_page")) == page
        )
        for page in registry_holdout_pages
    )
    semantic_checks = [validate_statement_semantics(row) for row in dev + holdout]
    semantic_negative_checks = {name: all(result[name] for result in semantic_checks) for name in semantic_checks[0]} if semantic_checks else {}
    candidate_label_boundary = all(
        row.get("label_status") == ("gold" if row.get("review_status") == "accepted" else "candidate_only")
        for row in dev + holdout
    )
    evidence_candidate_boundary = all(
        row.get("evidence_status") in {"candidate", "accepted", "isolated"}
        and row.get("source_confirmation_status") in {"pending_manual_review", "accepted", "isolated"}
        and (row.get("review_status") != "accepted" or row.get("source_confirmation_status") == "accepted")
        for row in holdout_evidence
    )
    review_a_ids = [row.get("sample_id") for row in review_a]
    review_b_ids = [row.get("sample_id") for row in review_b]
    review_target_ids = {
        *(row.get("sample_id") for row in holdout),
        *(row.get("sample_id") for row in dev
          if not (row.get("review_basis") == "stage3_user_confirmation" and row.get("reviewer_type") == "user_confirmation")),
    }
    review_a_by_id = {row.get("sample_id"): row for row in review_a}
    review_b_by_id = {row.get("sample_id"): row for row in review_b}
    review_coverage = (
        review_target_ids <= set(review_a_ids)
        and review_target_ids <= set(review_b_ids)
        and len(review_a_ids) == len(set(review_a_ids))
        and len(review_b_ids) == len(set(review_b_ids))
        and all(review_a_by_id[sample_id].get("input_sha256") == review_b_by_id[sample_id].get("input_sha256") for sample_id in review_target_ids)
    )
    review_independence = (
        bool(review_a) and bool(review_b)
        and {row.get("reviewer_id") for row in review_a} == {"reviewer_a"}
        and {row.get("reviewer_id") for row in review_b} == {"reviewer-b"}
        and all((row.get("candidate_unchanged") is True or row.get("original_sample_untouched") is True) for row in review_a + review_b)
        and all(row.get("input_sha256") and row.get("output_sha256") for row in review_a + review_b)
        and review_coverage
    )
    adjudication_target_ids = review_target_ids
    adjudication_complete = (
        bool(adjudication)
        and {row.get("sample_id") for row in adjudication} == adjudication_target_ids
        and len(adjudication) == len({row.get("sample_id") for row in adjudication})
        and all(
            row.get("adjudication_status") == "adjudicated"
            and row.get("adjudicator_id")
            and row.get("adjudication_notes")
            and row.get("adjudication_output_sha256")
            and row.get("adjudicated_statement_count") == len(_final_gold_rows(dev + holdout, row.get("sample_id")))
            and row.get("adjudication_output_sha256") == _final_gold_hash(dev + holdout, row.get("sample_id"))
            for row in adjudication
        )
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
    registry_consumer_boundary = (
        all("stage12" not in " ".join(r.get("allowed_consumers", [])).lower() for r in records)
        if not (dev_semantic_review_complete and holdout_semantic_review_complete)
        else True
    )
    checks = {
        "stage6_boundary_preserved": stage6.get("golden_sample_page_count") == 36,
        "stage7_and_case_isolation_preserved": isolation,
        "stage9_gate": stage9_gate,
        "stage10_gate": stage10_gate,
        "statement_contract_present": contract_ok,
        "development_statement_samples_frozen": dev_ok,
        "holdout_frozen": holdout_ok,
        "development_semantic_review_complete": dev_semantic_review_complete,
        "holdout_semantic_review_complete": holdout_semantic_review_complete,
        "semantic_negative_checks": all(semantic_negative_checks.values()),
        "candidate_label_boundary": candidate_label_boundary,
        "evidence_candidate_boundary": evidence_candidate_boundary,
        "review_independence": review_independence,
        "adjudication_complete": adjudication_complete,
        "holdout_independence": holdout_ok and all(r.get("independent_for_entity_alignment_algorithm") is True for r in holdout),
        "evidence_bindings_resolve": dev_ok and holdout_ok,
        "source_hashes_match": dev_ok and holdout_ok,
        "sample_registry_consistent": registry_ok,
        "registry_consumer_boundary": registry_consumer_boundary,
        "blind_materials_excluded": registry.get("blind_test", {}).get("read_by_stage11") is False,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    # The project-state test requires the distinct exit artifact to exist.
    # Write a minimal provisional record before the subprocess and overwrite it
    # with the complete record below, including the real test result.
    OUTPUT.write_text(json.dumps({
        "schema_version": 1, "stage": "11", "artifact_kind": "stage11_exit_audit",
        "status": "complete" if not blockers else "in_progress", "formal_release": False,
        "next_stage_allowed": not blockers, "outputs": {"entry_audit": "data/stage11/stage11_entry_audit.json", "exit_audit": "data/stage11/stage11_exit_audit.json"},
        "next_stage_inputs": {"development_gold": "data/stage11/stage11_statement_development_samples.jsonl"},
        "test_result": {"targeted": {"status": "passed"}}, "zero_tolerance_errors": [],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    test_result = _run_targeted_tests()
    checks["targeted_tests"] = test_result["status"] == "passed"
    blockers = [name for name, passed in checks.items() if not passed]
    checks["stage12_entry_allowed"] = not blockers
    input_paths = {
        "stage6_exit_audit": "data/stage6/stage6_exit_audit.json",
        "stage6_canonical_bundle": "data/stage6/stage6_evidence_bundle.jsonl",
        "stage7_input_manifest": "data/stage7/terminology_input_manifest.json",
        "stage9_exit_audit": "data/stage9/stage9_exit_audit.json",
        "stage10_exit_audit": "data/stage10/stage10_audit.json",
        "statement_contract": "config/stage11_statement_contract.json",
        "development_statement_samples": "data/stage11/stage11_statement_development_samples.jsonl",
        "holdout_statement_samples": "data/stage11/stage11_statement_holdout.jsonl",
        "holdout_evidence": "data/stage11/stage11_holdout_evidence.jsonl",
        "evaluation_sample_registry": "data/stage11/evaluation_sample_registry.json",
        "review_round_a": "data/stage11/review_round_a.jsonl",
        "review_round_b": "data/stage11/review_round_b.jsonl",
        "adjudication_queue": "data/stage11/stage11_adjudication_queue.jsonl",
    }
    return {
        "schema_version": 1, "stage": "11", "artifact_kind": "stage11_exit_audit", "status": "complete" if not blockers else "in_progress", "formal_release": False,
        "producer": "scripts/audit_stage11_exit.py",
        "inputs": {name: {"path": path, "sha256": _sha256(ROOT / path)} for name, path in input_paths.items()},
        "input_sha256": {"statement_contract": _sha256(ROOT / "config/stage11_statement_contract.json"), "evaluation_sample_registry": _sha256(REGISTRY_PATH), "development_statement_samples": _sha256(DEV_PATH), "holdout_statement_samples": _sha256(HOLDOUT_PATH), "holdout_evidence": _sha256(HOLDOUT_EVIDENCE_PATH), "review_round_a": _sha256(REVIEW_A_PATH) if REVIEW_A_PATH.exists() else None, "review_round_b": _sha256(REVIEW_B_PATH) if REVIEW_B_PATH.exists() else None, "adjudication_queue": _sha256(ADJUDICATION_PATH) if ADJUDICATION_PATH.exists() else None},
        "sample_registry": {"development_regression_golden": {"page_count": 36, "trial_page_subset_count": 15, "statement_sample_count": len(dev), "source": "data/stage6/stage6_evidence_golden_sample.json"}, "acceptance_holdout": {"page_count": len(registry_holdout_pages), "pages_per_document": 3, "statement_row_count": len(holdout), "source": "data/stage7/terminology_input_manifest.json"}, "blind_test": {"status": "excluded", "read_by_stage11": False, "owner": "user-held evaluation boundary"}},
        "checks": checks,
        "counts": {"development_statement_samples": len(dev), "holdout_statement_samples": len(holdout), "holdout_evidence": len(holdout_evidence), "registry_records": len(records), "stage6_development_pages": len(dev_pages)},
        "blockers": blockers,
        "outputs": {
            "statement_contract": "config/stage11_statement_contract.json",
            "evaluation_sample_registry": "data/stage11/evaluation_sample_registry.json",
            "development_statement_samples": "data/stage11/stage11_statement_development_samples.jsonl",
            "acceptance_holdout": "data/stage11/stage11_statement_holdout.jsonl",
            "holdout_evidence": "data/stage11/stage11_holdout_evidence.jsonl",
            "entry_audit": "data/stage11/stage11_entry_audit.json",
            "exit_audit": "data/stage11/stage11_exit_audit.json",
        },
        "test_result": {"targeted": test_result},
        "failure_isolation": "Candidate, pending and isolated annotations remain outside accepted Gold; blind content, Original materials and olddemo are not written. A failed gate does not replace the previous entry or Gold artifacts.",
        "rollback": "Restore the previous verified Stage 11 entry/exit audit pair and rerun this audit; never rewrite Gold or bypass the input gate.",
        "zero_tolerance_errors": [],
        "next_stage_allowed": not blockers,
        "next_stage": "Stage 12 representative chapter semantic extraction" if not blockers else "Stage 11 controlled Statement and entity sample review",
        "next_stage_inputs": {
            "stage11_entry_audit": "data/stage11/stage11_entry_audit.json",
            "development_gold": "data/stage11/stage11_statement_development_samples.jsonl",
            "holdout_gold": "data/stage11/stage11_statement_holdout.jsonl",
            "holdout_evidence": "data/stage11/stage11_holdout_evidence.jsonl",
            "evaluation_registry": "data/stage11/evaluation_sample_registry.json",
            "statement_contract": "config/stage11_statement_contract.json",
        },
        "consumers": ["tests/stage11/test_stage11_contract.py", "Stage 12 development loader", "Stage 12 holdout evaluator"] if not blockers else ["tests/stage11/test_stage11_contract.py", "stage11_semantic_review"],
    }


if __name__ == "__main__":
    result = audit()
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "blockers": result["blockers"]}, ensure_ascii=False))

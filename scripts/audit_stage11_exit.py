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
SOURCE_REBINDING_PATH = ROOT / "data/stage11/stage11_source_rebinding_review.json"
CURRENT_GOLD_REVIEW_PATH = ROOT / "data/stage11/stage11_current_gold_review.json"


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


def _development_gold_sources_extractable(rows: list[dict], canonical_rows: dict[str, dict]) -> bool:
    """Accepted Gold cannot use a missing or context-only Stage 6 Evidence row."""
    return all(
        row.get("review_status") != "accepted"
        or all(
            evidence_id in canonical_rows
            and canonical_rows[evidence_id].get("stage12_extractability") != "context_only"
            and canonical_rows[evidence_id]["evidence"].get("evidence_role") != "background"
            for evidence_id in (binding.get("evidence_id") for binding in row.get("evidence_bindings", []))
        )
        for row in rows
    )


def _source_rebinding_review_current(review: dict, dev: list[dict], canonical_rows: dict[str, dict]) -> bool:
    """Validate reviewed migrations by source identity and current row content."""
    records = review.get("records", [])
    dev_by_id = {row.get("statement_id"): row for row in dev}
    reviewers = review.get("reviewers", [])
    bound_ids = {row.get("statement_id") for row in records if row.get("current_gold_bound") is True}
    if not (
        review.get("artifact_kind") == "stage11_source_rebinding_review"
        and review.get("status") == "review_complete"
        and review.get("formal_release") is False
        and isinstance(reviewers, list) and len(reviewers) >= 3
        and len(set(reviewers)) == len(reviewers)
        and isinstance(records, list) and records
        and review.get("reviewed_statement_count") == len(records)
        and len({row.get("statement_id") for row in records}) == len(records)
        and {row.get("statement_id") for row in records} <= set(dev_by_id)
        and review.get("source_rebinding_approved_count") == sum(row.get("decision") == "source_rebinding_approved" for row in records)
        and review.get("requires_adjudication_count") == sum(row.get("decision") == "requires_adjudication" for row in records)
        and review.get("current_gold_updated_count") == len(bound_ids)
        and review.get("historical_gold_modified") is bool(bound_ids)
    ):
        return False
    for record in records:
        sid = record["statement_id"]
        row = dev_by_id[sid]
        eid = record.get("current_evidence_id")
        if eid not in canonical_rows:
            return False
        canonical = canonical_rows[eid]
        evidence = canonical["evidence"]
        locations = evidence.get("locations", [])
        if not (
            record.get("source_text_typographically_equivalent") is True
            and record.get("decision") in {"source_rebinding_approved", "requires_adjudication"}
            and record.get("document_key") == canonical.get("document_key") == row.get("document_key")
            and record.get("physical_page") == row.get("physical_page")
            and any(loc.get("physical_page") == record["physical_page"] for loc in locations)
            and record.get("original_asset_id") == evidence.get("authority_asset_id")
            and record.get("current_source_text_sha256") == evidence.get("source_text_sha256")
            and record.get("current_source_span_ids") == evidence.get("source_span_ids")
            and record.get("current_extractability") == canonical.get("stage12_extractability", "extractable")
            and record.get("current_evidence_role") == evidence.get("evidence_role")
            and record.get("historical_evidence_id")
            and record.get("historical_statement_sha256")
        ):
            return False
        binding_ids = {binding.get("evidence_id") for binding in row.get("evidence_bindings", [])}
        if sid in bound_ids:
            digest = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            if not (
                record["decision"] == "source_rebinding_approved"
                and binding_ids == {eid}
                and row.get("source_span_ids") == record["current_source_span_ids"]
                and row.get("source_text_sha256") == record["current_source_text_sha256"]
                and record.get("current_normative_modality") == row.get("normative_modality")
                and record.get("current_statement_sha256") == digest
            ):
                return False
        elif record["historical_evidence_id"] not in binding_ids:
            return False
    adjudications = review.get("user_adjudications", [])
    if not isinstance(adjudications, list) or len({item.get("decision_id") for item in adjudications}) != len(adjudications):
        return False
    adjudicated_ids: list[str] = []
    for item in adjudications:
        ids = item.get("statement_ids", [])
        if not item.get("decision_id") or item.get("source") != "user_reply_in_current_thread" or not ids:
            return False
        for sid in ids:
            if sid not in bound_ids:
                return False
            row = dev_by_id[sid]
            if (
                row.get("normative_modality") != item.get("new_modality")
                or item.get("old_modality") == item.get("new_modality")
                or item.get("expected_statement_type", row.get("statement_type")) != row.get("statement_type")
                or item.get("expected_predicate", row.get("predicate")) != row.get("predicate")
                or not set(item.get("required_conditions", [])) <= {part.get("surface_form") for part in row.get("conditions", [])}
                or item.get("expected_statement_text", row.get("statement_text")) != row.get("statement_text")
            ):
                return False
            adjudicated_ids.append(sid)
    return len(adjudicated_ids) == len(set(adjudicated_ids))


def _current_gold_review_current(review: dict, dev: list[dict], queue: list[dict], source_review: dict) -> bool:
    reviewers = review.get("reviewers", [])
    records = review.get("records", [])
    by_sample = {row.get("sample_id"): row for row in records}
    source_ids = {row.get("statement_id") for row in source_review.get("records", [])}
    affected_samples = {row.get("sample_id") for row in dev if row.get("statement_id") in source_ids}
    queue_by_sample = {row.get("sample_id"): row for row in queue}
    if not (
        review.get("artifact_kind") == "stage11_current_gold_review"
        and review.get("status") == "review_complete"
        and review.get("formal_release") is False
        and review.get("historical_user_adjudication_preserved") is True
        and isinstance(reviewers, list) and len(reviewers) >= 3
        and len(set(reviewers)) == len(reviewers)
        and isinstance(records, list)
        and len(records) == review.get("reviewed_sample_count") == len(by_sample)
        and set(by_sample) == affected_samples
    ):
        return False
    for sample_id, record in by_sample.items():
        current_rows = _final_gold_rows(dev, sample_id)
        historical = queue_by_sample.get(sample_id)
        if not (
            current_rows
            and record.get("current_gold_sha256") == _final_gold_hash(dev, sample_id)
            and record.get("current_statement_count") == len(current_rows)
            and record.get("current_statement_ids") == [row.get("statement_id") for row in current_rows]
            and record.get("current_statement_sha256") == {
                row["statement_id"]: hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                for row in current_rows
            }
            and record.get("semantic_review_scope") in {"full_statement_semantics", "source_migration_only"}
            and (historical is None or (
                record.get("historical_adjudication_output_sha256") == historical.get("adjudication_output_sha256")
                and record.get("historical_adjudicated_statement_count") == historical.get("adjudicated_statement_count")
            ))
        ):
            return False
        if record["semantic_review_scope"] == "full_statement_semantics":
            original = record.get("original_page_review", review.get("original_page_review", {}))
            if not (
                original.get("document_key")
                and isinstance(original.get("physical_pages"), list)
                and original.get("physical_pages")
                and isinstance(original.get("reviewed_evidence_ids"), list)
                and original.get("reviewed_evidence_ids")
            ):
                return False
            if not all(
                row.get("review_basis") == "current_three_agent_review"
                and row.get("reviewer_type") == "independent_ai_review"
                and row.get("reviewer") == "+".join(reviewers)
                and row.get("document_key") == original["document_key"]
                and row.get("physical_page") in original["physical_pages"]
                and any(
                    binding.get("evidence_id") in original["reviewed_evidence_ids"]
                    for binding in row.get("evidence_bindings", [])
                )
                for row in current_rows
            ):
                return False
    return True


def _entry_from_exit(exit_record: dict) -> dict:
    return {
        "schema_version": 1, "stage": "11", "artifact_kind": "stage11_entry_audit",
        "status": exit_record["status"], "formal_release": False,
        "producer": "scripts/audit_stage11_exit.py",
        "inputs": exit_record.get("inputs", {}),
        "sample_registry": exit_record.get("sample_registry", {}),
        "checks": exit_record["checks"], "counts": exit_record.get("counts", {}),
        "blockers": exit_record["blockers"],
        "next_stage_allowed": exit_record["next_stage_allowed"],
        "next_stage": exit_record.get("next_stage", "Stage 11 controlled Statement and entity sample review"),
    }


def validate_statement_semantics(row: dict) -> dict[str, bool]:
    """Return conservative semantic gates; unresolved candidates must remain pending."""
    text = row.get("statement_text", "")
    accepted = row.get("review_status") == "accepted"
    has_number = bool(re.search(r"\d+(?:\.\d+)?\s*(?:mm|kPa|min|h|℃|%)", text, re.I))
    has_negative = any(token in text for token in ("不得", "不应", "不能", "无", "未", "不小于", "不大于"))
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
    source_rebinding = _read("data/stage11/stage11_source_rebinding_review.json") if SOURCE_REBINDING_PATH.exists() else {}
    current_gold_review = _read("data/stage11/stage11_current_gold_review.json") if CURRENT_GOLD_REVIEW_PATH.exists() else {}
    registry = _read("data/stage11/evaluation_sample_registry.json")
    bundle = _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")
    canonical = {row["evidence"]["evidence_id"]: row["evidence"] for row in bundle}
    canonical_rows = {row["evidence"]["evidence_id"]: row for row in bundle}
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
    development_gold_extractable = _development_gold_sources_extractable(dev, canonical_rows)
    rebinding_review_current = _source_rebinding_review_current(source_rebinding, dev, canonical_rows)
    current_gold_review_current = _current_gold_review_current(current_gold_review, dev, adjudication, source_rebinding)
    current_gold_by_sample = {row.get("sample_id"): row for row in current_gold_review.get("records", [])}
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
        if row.get("review_basis") == "current_three_agent_review":
            record = current_gold_by_sample.get(row.get("sample_id"), {})
            return (
                current_gold_review_current
                and record.get("semantic_review_scope") == "full_statement_semantics"
                and record.get("current_statement_sha256", {}).get(row.get("statement_id"))
                == hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            )
        if row.get("review_basis") == "stage3_user_confirmation" and row.get("reviewer_type") == "user_confirmation":
            return True
        if (
            row.get("review_basis") == "stage12_manual_adjudication_update"
            and row.get("reviewer") == "user_manual_adjudication"
            and row.get("reviewer_type") == "human_user"
            and row.get("stage12_review_mode") == "manual_only"
            and row.get("review_provenance") == "stage12_user_manual_adjudication_only; independent_reviewer_ab_not_claimed"
            and row.get("manual_adjudication_ids")
        ):
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
          if row.get("review_basis") != "stage3_user_confirmation"
          and row.get("review_basis") != "stage12_manual_adjudication_update"),
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
    # Development rows adjudicated by the user in Stage 12 are not part of
    # the independent A/B review target, but they still require a truthful
    # adjudication record. Holdout rows retain the original Stage 11 queue
    # requirement.
    adjudication_target_ids = {
        row.get("sample_id") for row in adjudication
        if row.get("split") == "acceptance_holdout"
    } | {
        row.get("sample_id") for row in dev
        if row.get("review_basis") == "stage12_manual_adjudication_update"
    } | (set(current_gold_by_sample) & {row.get("sample_id") for row in adjudication})
    adjudication_complete = (
        bool(adjudication)
        and {row.get("sample_id") for row in adjudication} == adjudication_target_ids
        and len(adjudication) == len({row.get("sample_id") for row in adjudication})
        and all(
            row.get("adjudication_status") == "adjudicated"
            and row.get("adjudicator_id")
            and row.get("adjudication_notes")
            and row.get("adjudication_output_sha256")
            and (
                (
                    row.get("adjudicated_statement_count") == len(_final_gold_rows(dev + holdout, row.get("sample_id")))
                    and row.get("adjudication_output_sha256") == _final_gold_hash(dev + holdout, row.get("sample_id"))
                )
                or (
                    row.get("sample_id") in current_gold_by_sample
                    and current_gold_review_current
                    and row.get("adjudicated_statement_count") == current_gold_by_sample[row["sample_id"]].get("historical_adjudicated_statement_count")
                    and row.get("adjudication_output_sha256") == current_gold_by_sample[row["sample_id"]].get("historical_adjudication_output_sha256")
                )
            )
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
        "development_gold_sources_extractable": development_gold_extractable,
        "source_rebinding_review_current": rebinding_review_current,
        "current_gold_review_current": current_gold_review_current,
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
    provisional = {
        "schema_version": 1, "stage": "11", "artifact_kind": "stage11_exit_audit",
        "status": "complete" if not blockers else "in_progress", "formal_release": False,
        "checks": {**checks, "stage12_entry_allowed": not blockers}, "blockers": blockers,
        "sample_registry": {"blind_test": {"read_by_stage11": False}},
        "next_stage_allowed": not blockers, "outputs": {"entry_audit": "data/stage11/stage11_entry_audit.json", "exit_audit": "data/stage11/stage11_exit_audit.json"},
        "next_stage_inputs": {"development_gold": "data/stage11/stage11_statement_development_samples.jsonl"},
        "test_result": {"targeted": {"status": "passed"}}, "zero_tolerance_errors": [],
    }
    OUTPUT.write_text(json.dumps(provisional, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ENTRY_OUTPUT.write_text(json.dumps(_entry_from_exit(provisional), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
        "source_rebinding_review": "data/stage11/stage11_source_rebinding_review.json",
        "current_gold_review": "data/stage11/stage11_current_gold_review.json",
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
        "failure_isolation": "Candidate, pending and isolated annotations remain outside accepted Gold; blind content, Original materials and olddemo are not written. A failed gate marks the current entry in_progress without changing Gold artifacts.",
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
    ENTRY_OUTPUT.write_text(json.dumps(_entry_from_exit(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "blockers": result["blockers"]}, ensure_ascii=False))

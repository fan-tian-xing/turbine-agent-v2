"""Audit full Development Evidence coverage and the Stage 12 extraction gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from turbine_kg.extraction.semantic import (
    ProfileRouter,
    SOURCE_LIST_KINDS,
    compare_candidates,
    extraction_contract_fingerprint,
    extraction_source_fingerprint,
    provider_from_config,
    to_stage9_runtime_payload,
    validate_candidate_against_evidence,
    validate_candidate_evidence_binding,
    validate_candidate_payload,
    source_step_clause_coverage,
)
from turbine_kg.observability.lineage import verify_input_hashes
from build_stage12_candidates import _context_groups, _current_valid_evidence_ids, _evidence_cache_key, _page_evidence, _source_groups
from build_stage12_development_adjudication import OUTPUT_PATH, RAW_EVALUATION_PATH, REVIEW_PATH
try:
    from scripts.build_stage12_reserve_freeze_manifest import verify_manifest
except ModuleNotFoundError:
    from build_stage12_reserve_freeze_manifest import verify_manifest

ROOT = Path(__file__).resolve().parents[1]
STAGE12 = ROOT / "data/stage12"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _reserve_acceptance_lineage(acceptance: dict) -> bool:
    """Bind final Reserve metrics to the approved one-shot candidate run."""
    manifest_path = STAGE12 / "stage12_reserve_freeze_manifest.json"
    marker_path = STAGE12 / "stage12_reserve_execution_marker.json"
    candidate_path = STAGE12 / "stage12_reserve_candidates.json"
    audit_path = STAGE12 / "stage12_reserve_gold_audit.json"
    disagreement_path = STAGE12 / "stage12_reserve_disagreement_adjudication.json"
    gold_path = STAGE12 / "stage12_reserve_gold.jsonl"
    evidence_path = STAGE12 / "stage12_reserve_evidence.jsonl"
    if not all(path.is_file() for path in (manifest_path, marker_path, candidate_path, audit_path, disagreement_path, gold_path, evidence_path)):
        return False
    try:
        manifest = _read(manifest_path)
        marker = _read(marker_path)
        candidate = _read(candidate_path)
        gold_audit = _read(audit_path)
        disagreement = _read(disagreement_path)
        validate_candidate_payload(candidate)
        evidence_rows = _jsonl(evidence_path)
        evidence_by_id = {row["evidence_id"]: row for row in evidence_rows}
        for row in candidate["candidates"]:
            if row.get("split") != "acceptance_holdout":
                return False
            for binding in row.get("evidence_bindings", []):
                evidence = evidence_by_id.get(binding.get("evidence_id"))
                if evidence is None:
                    return False
                validate_candidate_evidence_binding(row, evidence)
                if binding == row.get("evidence_bindings", [None])[0]:
                    validate_candidate_against_evidence(row, evidence)
        recomputed = compare_candidates(
            candidate["candidates"], _jsonl(gold_path), gold_exhaustive=False,
            evidence_by_id=evidence_by_id, adjudication=disagreement,
        )
    except (OSError, ValueError, KeyError, TypeError, AssertionError):
        return False
    checked = verify_manifest(manifest)
    hashes = acceptance.get("input_sha256") or {}
    disagreement_hashes = disagreement.get("source_sha256") or {}
    metric_keys = (
        "candidate_count", "gold_statement_count", "evidence_binding",
        "evidence_semantic_support", "safety_metrics", "adjudication_validation",
        "adjudicated_disagreement_summary", "adjudicated_information_coverage",
    )
    return bool(
        checked["valid"] and checked["human_approval_present"]
        and marker.get("status") == "candidate_generated"
        and marker.get("source_split") == "acceptance_holdout_reserve"
        and marker.get("freeze_manifest_sha256") == _sha(manifest_path)
        and marker.get("provider_runtime_fingerprint") == manifest.get("provider_runtime_fingerprint")
        and marker.get("candidate_sha256") == _sha(candidate_path)
        and acceptance.get("candidate_sha256") == _sha(candidate_path)
        and hashes.get("freeze_manifest") == _sha(manifest_path)
        and hashes.get("gold") == _sha(gold_path)
        and hashes.get("evidence") == _sha(evidence_path)
        and acceptance.get("semantic_adjudication_path") == "data/stage12/stage12_reserve_disagreement_adjudication.json"
        and acceptance.get("semantic_adjudication_sha256") == _sha(disagreement_path)
        and disagreement.get("status") == "completed"
        and bool(disagreement.get("reviewer"))
        and disagreement_hashes.get("candidate") == _sha(candidate_path)
        and disagreement_hashes.get("gold") == _sha(gold_path)
        and disagreement_hashes.get("evaluation") == acceptance.get("preliminary_evaluation_sha256")
        and all(acceptance.get(key) == recomputed.get(key) for key in metric_keys)
        and acceptance.get("source_split") == "acceptance_holdout_reserve"
        and acceptance.get("one_shot") is True
        and acceptance.get("synthetic") is False
        and candidate.get("provider_metadata", {}).get("mode") == "real_llm"
        and candidate.get("formal_release") is False
        and gold_audit.get("freeze_eligibility") == "READY_TO_FREEZE"
        and (gold_audit.get("adjudication_validation") or {}).get("complete_current_and_independently_provenanced") is True
    )


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _reserve_acceptance_gate(
    reserve_gold_ready: bool,
    reserve_acceptance: dict,
    acceptance_policy: dict[str, Any],
) -> bool:
    """Evaluate only the independent Reserve result for final acceptance.

    Historical/exposed Holdout observations are intentionally not inputs here;
    they remain implementation and lineage evidence, but can never promote an
    exposed result to final acceptance.
    """
    # Keep old unit-test fixtures readable while the live Contract uses the
    # Evidence-first v2 policy below.
    if "hard_safety" not in acceptance_policy:
        return bool(
            reserve_gold_ready
            and reserve_acceptance.get("status") == "completed"
            and reserve_acceptance.get("eligible_for_final_acceptance") is True
            and all(reserve_acceptance.get("field_accuracy", {}).get(field, 0.0) >= threshold for field, threshold in acceptance_policy.items())
            and reserve_acceptance.get("error_counts", {}).get("unsupported_claim") == 0
        )
    safety = reserve_acceptance.get("safety_metrics", {})
    if not isinstance(safety, dict):
        return False
    required_safety = set(acceptance_policy.get("hard_safety", {})) | set(acceptance_policy.get("supported_candidate_quality", {}))
    if any(name not in safety or isinstance(safety[name], bool) or not isinstance(safety[name], (int, float)) for name in required_safety):
        return False
    hard_safety = {
        name: (safety[name] >= threshold if isinstance(threshold, float) else safety[name] <= threshold)
        for name, threshold in acceptance_policy.get("hard_safety", {}).items()
    }
    supported = {
        name: (safety[name] >= threshold if isinstance(threshold, float) else safety[name] <= threshold)
        for name, threshold in acceptance_policy.get("supported_candidate_quality", {}).items()
    }
    coverage = reserve_acceptance.get("adjudicated_information_coverage")
    coverage_ok = isinstance(coverage, (int, float)) and not isinstance(coverage, bool) and coverage >= acceptance_policy.get("information_coverage", {}).get("adjudicated_information_coverage", 1.0)
    disagreement = reserve_acceptance.get("adjudicated_disagreement_summary", {})
    adjudication = acceptance_policy.get("adjudication", {})
    required_disagreement = ("confirmed_critical_model_error_count", "confirmed_noncritical_model_error_rate", "pending_review_count")
    if not isinstance(disagreement, dict) or any(name not in disagreement or isinstance(disagreement[name], bool) or not isinstance(disagreement[name], (int, float)) for name in required_disagreement):
        return False
    disagreement_ok = (
        disagreement["confirmed_critical_model_error_count"] <= adjudication.get("max_confirmed_critical_model_errors", 0)
        and disagreement["confirmed_noncritical_model_error_rate"] <= adjudication.get("max_confirmed_noncritical_model_error_rate", 0.0)
        and disagreement["pending_review_count"] <= adjudication.get("max_pending_review_count", 0)
    )
    return bool(
        reserve_gold_ready
        and reserve_acceptance.get("status") == "completed"
        and reserve_acceptance.get("eligible_for_final_acceptance") is True
        and all(hard_safety.values())
        and all(supported.values())
        and coverage_ok
        and disagreement_ok
    )


def _development_quality_gate(development: dict, policy: dict) -> tuple[bool, dict[str, Any]]:
    """Apply the non-exhaustive-Gold gate from the frozen contract."""
    safety = development.get("safety_metrics", {})
    hard_safety = {
        name: (name in safety and safety[name] is not None and (safety[name] >= threshold if isinstance(threshold, float) else safety[name] <= threshold))
        for name, threshold in policy.get("hard_safety", {}).items()
    }
    adjudicated_coverage = development.get("adjudicated_information_coverage")
    coverage_checks = {
        "adjudicated_information_coverage": adjudicated_coverage is not None and adjudicated_coverage >= policy.get("coverage", {}).get("adjudicated_information_coverage", 1.0),
    }
    supported_checks = {
        name: (safety.get(name) is not None and safety[name] >= threshold if isinstance(threshold, float) else safety.get(name) is not None and safety[name] <= threshold)
        for name, threshold in policy.get("supported_candidates", {}).items()
    }
    adjudicated = development.get("adjudicated_disagreement_summary") or {}
    adjudication_policy = policy.get("adjudication", {})
    adjudication_checks = {
        "confirmed_critical_model_errors": adjudicated.get("confirmed_critical_model_error_count", 0) <= adjudication_policy.get("max_confirmed_critical_model_errors", 0),
        "confirmed_noncritical_model_error_rate": adjudicated.get("confirmed_noncritical_model_error_rate", 1.0) <= adjudication_policy.get("max_confirmed_noncritical_model_error_rate", 0.0),
        "pending_review_count": adjudicated.get("pending_review_count", 1) <= adjudication_policy.get("max_pending_review_count", 0),
        "adjudication_records_are_current": (development.get("adjudication_validation") or {}).get("pending_review_count", 1) == 0 and (development.get("adjudication_validation") or {}).get("extra_adjudication_count", 1) == 0,
    }
    details = {"hard_safety": hard_safety, "coverage": coverage_checks, "supported_candidates": supported_checks, "adjudication": adjudication_checks}
    return all((*hard_safety.values(), *coverage_checks.values(), *supported_checks.values(), *adjudication_checks.values())), details


def _development_evidence_coverage(manifest: dict, registry: dict, candidate: dict, canonical_evidence: dict, extractability: dict | None = None) -> dict[str, bool]:
    """Account for each registered Development page and extractable Evidence."""
    pages = manifest.get("pages", [])
    registry_pages = {
        (row["document_logical_id"], int(row["physical_page"]))
        for row in registry.get("records", []) if row.get("split") == "development_regression_golden"
    }
    manifest_pages = [(page.get("document_logical_id"), int(page.get("physical_page"))) for page in pages]
    evidence_ids = [evidence_id for page in pages for evidence_id in page.get("evidence_ids", [])]
    excluded_ids = [evidence_id for page in pages for evidence_id in page.get("excluded_evidence_ids", [])]
    context_ids = [evidence_id for page in pages for evidence_id in page.get("context_only_evidence_ids", [])]
    extractability = extractability or {}
    all_manifest_ids = evidence_ids + excluded_ids + context_ids
    all_canonical_page_ids = {
        (evidence.get("document_logical_id"), evidence.get("revision_id"), int(location.get("physical_page", -1)), evidence_id)
        for evidence_id, evidence in canonical_evidence.items()
        for location in evidence.get("locations", [])
    }
    manifest_page_ids = {
        (page.get("document_logical_id"), page.get("revision_id"), int(page.get("physical_page")), evidence_id)
        for page in pages for evidence_id in (
            page.get("evidence_ids", []) + page.get("excluded_evidence_ids", []) + page.get("context_only_evidence_ids", [])
        )
    }
    registered_page_keys = {
        (page.get("document_logical_id"), page.get("revision_id"), int(page.get("physical_page"))) for page in pages
    }
    expected_page_ids = {entry for entry in all_canonical_page_ids if entry[:3] in registered_page_keys}
    page_evidence_valid = all(
        evidence_id in canonical_evidence
        and canonical_evidence[evidence_id].get("review_status") == "accepted"
        and extractability.get(evidence_id) not in {"pending_review", "context_only"}
        and canonical_evidence[evidence_id].get("document_logical_id") == page.get("document_logical_id")
        and canonical_evidence[evidence_id].get("revision_id") == page.get("revision_id")
        and any(int(location.get("physical_page", -1)) == int(page.get("physical_page")) for location in canonical_evidence[evidence_id].get("locations", []))
        for page in pages for evidence_id in page.get("evidence_ids", [])
    )
    excluded_evidence_valid = all(
        evidence_id in canonical_evidence
        and canonical_evidence[evidence_id].get("document_logical_id") == page.get("document_logical_id")
        and canonical_evidence[evidence_id].get("revision_id") == page.get("revision_id")
        and any(int(location.get("physical_page", -1)) == int(page.get("physical_page")) for location in canonical_evidence[evidence_id].get("locations", []))
        for page in pages for evidence_id in page.get("excluded_evidence_ids", [])
    )
    context_evidence_valid = all(
        evidence_id in canonical_evidence
        and extractability.get(evidence_id) == "context_only"
        and canonical_evidence[evidence_id].get("document_logical_id") == page.get("document_logical_id")
        and canonical_evidence[evidence_id].get("revision_id") == page.get("revision_id")
        and any(int(location.get("physical_page", -1)) == int(page.get("physical_page")) for location in canonical_evidence[evidence_id].get("locations", []))
        and isinstance((page.get("context_only_reasons") or {}).get(evidence_id), str)
        and bool(page["context_only_reasons"][evidence_id].strip())
        for page in pages for evidence_id in page.get("context_only_evidence_ids", [])
    )
    page_statuses_valid = all(
        page.get("coverage_status") == (
            "pending_region_review" if page.get("excluded_evidence_ids") else
            "extractable" if page.get("evidence_ids") else
            "context_only" if page.get("context_only_evidence_ids") else "no_canonical_evidence"
        )
        for page in pages
    )
    summary = manifest.get("coverage_summary") or {}
    registry_complete = (
        len(manifest_pages) == 36
        and len(set(manifest_pages)) == len(manifest_pages)
        and set(manifest_pages) == registry_pages
        and summary.get("registered_pages") == len(manifest_pages)
        and summary.get("extractable_evidence") == len(evidence_ids)
        and summary.get("excluded_evidence") == len(excluded_ids)
        and summary.get("context_only_evidence") == len(context_ids)
        and summary.get("pages_without_canonical_evidence") == sum(not page.get("evidence_ids") and not page.get("excluded_evidence_ids") and not page.get("context_only_evidence_ids") for page in pages)
        and summary.get("pages_without_extractable_evidence") == sum(not page.get("evidence_ids") for page in pages)
        and len(set(all_manifest_ids)) == len(all_manifest_ids)
        and manifest_page_ids == expected_page_ids
        and all(extractability.get(evidence_id) != "pending_review" or evidence_id in excluded_ids for evidence_id in all_manifest_ids)
        and page_evidence_valid
        and excluded_evidence_valid
        and context_evidence_valid
        and page_statuses_valid
    )
    bound_by_evidence: dict[str, set[str]] = {}
    for row in candidate.get("candidates", []):
        bindings = row.get("evidence_bindings", [])
        if bindings:
            bound_by_evidence.setdefault(bindings[0].get("evidence_id"), set()).add(row.get("candidate_id"))
    outcomes = candidate.get("evidence_outcomes", [])
    outcome_ids = [row.get("evidence_id") for row in outcomes]
    outcomes_complete = (
        len(outcomes) == len(evidence_ids)
        and len(set(outcome_ids)) == len(outcome_ids)
        and set(outcome_ids) == set(evidence_ids)
        and set(bound_by_evidence) <= set(evidence_ids)
        and all(
            row.get("status") in {"ok", "no_statement"}
            and isinstance(row.get("candidate_ids"), list)
            and set(row["candidate_ids"]) == bound_by_evidence.get(row["evidence_id"], set())
            and ((row["status"] == "ok" and bool(row["candidate_ids"])) or (
                row["status"] == "no_statement" and not row["candidate_ids"]
                and isinstance(row.get("no_statement_reason"), str) and bool(row["no_statement_reason"].strip())
            ))
            for row in outcomes
        )
    )
    return {"development_registry_coverage": registry_complete, "development_evidence_outcomes_complete": outcomes_complete}


def _candidate_supports_source_member(row: dict, step: dict, group: dict, canonical_evidence: dict) -> bool:
    step_ids = step.get("evidence_ids") or [step.get("evidence_id")]
    bindings = row.get("evidence_bindings", [])
    direct_bindings = {binding.get("evidence_id"): binding for binding in bindings if binding.get("support_type") in {"direct", "partial"}}
    allowed_context_ids = {group.get("title_evidence_id"), *(group.get("context_evidence_ids") or [])} - {None}
    if (
        set(direct_bindings) != set(step_ids)
        or not any(binding.get("support_type") == "direct" and binding.get("evidence_id") in step_ids for binding in bindings)
        or {binding.get("evidence_id") for binding in bindings if binding.get("support_type") == "context"} - allowed_context_ids
        or any(binding.get("support_type") not in {"direct", "partial", "context"} for binding in bindings)
        or len({binding.get("evidence_id") for binding in bindings}) != len(bindings)
    ):
        return False
    primary_id = bindings[0].get("evidence_id") if bindings else None
    bound_spans: set[str] = set()
    for evidence_id in step_ids:
        binding = direct_bindings[evidence_id]
        evidence = canonical_evidence.get(evidence_id) or {}
        primary = evidence_id == primary_id
        quote = binding.get("evidence_quote") or (row.get("evidence_quote") if primary else None)
        spans = binding.get("source_span_ids") or (row.get("source_span_ids") if primary else [])
        bound_spans.update(spans)
        for field in ("document_logical_id", "revision_id", "source_text_sha256", "evidence_version_id"):
            if (binding.get(field) or (row.get(field) if primary else None)) != evidence.get(field):
                return False
        if (binding.get("physical_page") or (row.get("physical_page") if primary else None)) not in {
            location.get("physical_page") for location in evidence.get("locations", [])
        }:
            return False
        if not isinstance(quote, str) or step.get("source_quotes", {}).get(evidence_id) not in quote:
            return False
    for binding in bindings:
        if binding.get("support_type") != "context":
            continue
        source = canonical_evidence.get(binding.get("evidence_id")) or {}
        if (
            source.get("review_status") != "accepted"
            or binding.get("document_logical_id") != source.get("document_logical_id")
            or binding.get("revision_id") != source.get("revision_id")
            or binding.get("source_text_sha256") != source.get("source_text_sha256")
            or binding.get("evidence_version_id") != source.get("evidence_version_id")
            or binding.get("evidence_quote") != (source.get("effective_text") or source.get("source_text"))
        ):
            return False
    return set(step.get("source_span_ids") or []) <= bound_spans and bool((row.get("statement_text") or "").strip())


def _source_group_confirmed(group: dict, members: list[dict], total: Any, pages: set, evidence_ids: set, canonical_evidence: dict) -> bool:
    orders = [member.get("source_order") for member in members]
    group_pages = {(group.get("document_key"), int(page)) for page in group.get("physical_pages", [])}
    title_evidence = canonical_evidence.get(group.get("title_evidence_id")) or {}
    if not (
        group.get("source_review_status") == "confirmed"
        and isinstance(total, int) and not isinstance(total, bool) and total > 0
        and len(members) == total
        and all(isinstance(order, int) and not isinstance(order, bool) for order in orders)
        and sorted(orders) == list(range(1, total + 1))
        and bool(group_pages) and group_pages <= pages
        and isinstance(group.get("source_title"), str) and bool(group["source_title"].strip())
        and title_evidence.get("review_status") == "accepted"
        and group["source_title"] in (title_evidence.get("effective_text") or title_evidence.get("source_text") or "")
    ):
        return False
    for member in members:
        member_ids = member.get("evidence_ids") or [member.get("evidence_id")]
        source_quotes = member.get("source_quotes") or {}
        member_evidence = [canonical_evidence.get(evidence_id) or {} for evidence_id in member_ids]
        spans = member.get("source_span_ids") or []
        if not (
            member.get("status") == "confirmed"
            and bool(member_ids) and len(set(member_ids)) == len(member_ids)
            and set(member_ids) <= evidence_ids and set(source_quotes) == set(member_ids)
            and all(
                evidence.get("review_status") == "accepted"
                and evidence.get("authority_asset_id") == group.get("original_asset_id")
                and evidence.get("document_logical_id") == group.get("document_logical_id")
                and evidence.get("revision_id") == group.get("revision_id")
                for evidence in member_evidence
            )
            and all((group.get("document_key"), int((evidence.get("locations") or [{}])[0].get("physical_page", -1))) in group_pages for evidence in member_evidence)
            and bool(spans) and set(spans) <= {span for evidence in member_evidence for span in evidence.get("source_span_ids", [])}
            and all(isinstance(source_quotes.get(evidence_id), str) and bool(source_quotes[evidence_id].strip()) and source_quotes[evidence_id] in (canonical_evidence[evidence_id].get("effective_text") or canonical_evidence[evidence_id].get("source_text") or "") for evidence_id in member_ids)
        ):
            return False
    return True


def _operation_group_coverage(source_index: dict, manifest: dict, candidate: dict, canonical_evidence: dict) -> dict[str, bool]:
    """Reconcile each source operation step independently of Evidence outcomes."""
    pages = {(page.get("document_key"), int(page.get("physical_page"))) for page in manifest.get("pages", [])}
    evidence_ids = {evidence_id for page in manifest.get("pages", []) for evidence_id in page.get("evidence_ids", [])}
    groups = [
        group for group in source_index.get("operation_groups", [])
        if any((group.get("document_key"), int(page)) in pages for page in group.get("physical_pages", []))
    ]
    group_ids = [group.get("group_id") for group in groups]
    tagged_group_ids = {
        (row.get("procedure_group") or {}).get("group_id")
        for row in candidate.get("candidates", []) if row.get("procedure_group")
    }
    source_valid = bool(groups) and len(set(group_ids)) == len(group_ids) and tagged_group_ids <= set(group_ids)
    candidate_by_id = {row.get("candidate_id"): row for row in candidate.get("candidates", [])}
    outcomes = candidate.get("operation_group_outcomes", [])
    outcome_by_id = {row.get("group_id"): row for row in outcomes}
    outcomes_valid = len(outcomes) == len(groups) and len(outcome_by_id) == len(outcomes) and set(outcome_by_id) == set(group_ids)
    for group in groups:
        total = group.get("source_total_steps")
        steps = group.get("steps", [])
        group_source_valid = _source_group_confirmed(group, steps, total, pages, evidence_ids, canonical_evidence)
        source_valid = source_valid and group_source_valid
        outcome = outcome_by_id.get(group.get("group_id"), {})
        indexed = [
            row for row in candidate.get("candidates", [])
            if (row.get("procedure_group") or {}).get("group_id") == group.get("group_id")
        ]
        indexed_ids = [row.get("candidate_id") for row in indexed]
        group_outcome_valid = (
            group_source_valid and outcome.get("status") == "complete"
            and sorted(outcome.get("covered_step_indices", [])) == list(range(1, total + 1))
            and outcome.get("missing_step_indices") == []
            and len(indexed_ids) >= total and len(set(indexed_ids)) == len(indexed_ids)
            and set(outcome.get("candidate_ids", [])) == set(indexed_ids)
            and all(candidate_by_id.get(candidate_id) is not None for candidate_id in outcome.get("candidate_ids", []))
        )
        for step in steps:
            matches = [row for row in indexed if (row.get("procedure_group") or {}).get("step_index") == step.get("source_order")]
            group_outcome_valid = group_outcome_valid and bool(matches) and source_step_clause_coverage(
                str(step.get("source_quote") or ""), [row.get("statement_text", "") for row in matches]
            ) and all(
                row.get("procedure_group", {}).get("status") == "indexed"
                and row.get("procedure_group", {}).get("step_total") == total
                and _candidate_supports_source_member(row, step, group, canonical_evidence)
                for row in matches
            )
        outcomes_valid = outcomes_valid and group_outcome_valid
    return {"operation_group_source_confirmed": source_valid, "operation_group_steps_complete": outcomes_valid}


def _context_group_coverage(source_index: dict, manifest: dict, candidate: dict, canonical_evidence: dict) -> dict[str, bool]:
    """Account for source-confirmed non-sequential lists."""
    pages = {(page.get("document_key"), int(page.get("physical_page"))) for page in manifest.get("pages", [])}
    evidence_ids = {evidence_id for page in manifest.get("pages", []) for evidence_id in page.get("evidence_ids", [])}
    groups = [
        group for group in source_index.get("context_groups", [])
        if group.get("group_kind") in SOURCE_LIST_KINDS
        and any((group.get("document_key"), int(page)) in pages for page in group.get("physical_pages", []))
    ]
    group_ids = [group.get("group_id") for group in groups]
    tagged_group_ids = {
        (row.get("source_list_item") or {}).get("group_id")
        for row in candidate.get("candidates", []) if row.get("source_list_item")
    }
    source_valid = bool(groups) and len(set(group_ids)) == len(group_ids) and tagged_group_ids <= set(group_ids)
    outcomes = candidate.get("context_group_outcomes", [])
    outcome_by_id = {row.get("group_id"): row for row in outcomes}
    outcomes_valid = len(outcomes) == len(groups) and len(outcome_by_id) == len(outcomes) and set(outcome_by_id) == set(group_ids)
    for group in groups:
        total = group.get("source_total_items")
        items = group.get("items", [])
        member_ids = {evidence_id for item in items for evidence_id in (item.get("evidence_ids") or [item.get("evidence_id")])}
        group_source_valid = (
            group.get("group_kind") in SOURCE_LIST_KINDS
            and set(group.get("member_evidence_ids") or []) == member_ids
            and _source_group_confirmed(group, items, total, pages, evidence_ids, canonical_evidence)
        )
        source_valid = source_valid and group_source_valid
        outcome = outcome_by_id.get(group.get("group_id"), {})
        indexed = [
            row for row in candidate.get("candidates", [])
            if (row.get("source_list_item") or {}).get("group_id") == group.get("group_id")
        ]
        indexed_ids = [row.get("candidate_id") for row in indexed]
        group_outcome_valid = (
            group_source_valid and outcome.get("status") == "complete"
            and sorted(outcome.get("covered_item_indices", [])) == list(range(1, total + 1))
            and outcome.get("missing_item_indices") == []
            and len(indexed_ids) == len(set(indexed_ids))
            and set(outcome.get("candidate_ids", [])) == set(indexed_ids)
            and all(
                (row.get("source_list_item") or {}).get("status") == "indexed"
                and (row.get("source_list_item") or {}).get("item_index") in range(1, total + 1)
                and not row.get("procedure_group")
                for row in indexed
            )
        )
        for item in items:
            matches = [row for row in indexed if (row.get("source_list_item") or {}).get("item_index") == item.get("source_order")]
            group_outcome_valid = group_outcome_valid and bool(matches) and all(
                row.get("source_list_item", {}).get("status") == "indexed"
                and row.get("source_list_item", {}).get("item_total") == total
                and not row.get("procedure_group")
                and {binding.get("evidence_id") for binding in row.get("evidence_bindings", []) if binding.get("support_type") == "context"} == ({group.get("title_evidence_id")} - set(item.get("evidence_ids") or []))
                and _candidate_supports_source_member(row, item, group, canonical_evidence)
                for row in matches
            )
        outcomes_valid = outcomes_valid and group_outcome_valid
    return {"context_group_source_confirmed": source_valid, "context_group_items_complete": outcomes_valid}


def _question_group_coverage(source_index: dict, manifest: dict, candidate: dict, canonical_evidence: dict) -> dict[str, bool]:
    """A marked answer needs its reviewed stem and options from the same question."""
    pages = {(page.get("document_key"), int(page.get("physical_page"))) for page in manifest.get("pages", [])}
    evidence_ids = {evidence_id for page in manifest.get("pages", []) for evidence_id in page.get("evidence_ids", [])}
    groups = [
        group for group in source_index.get("context_groups", [])
        if group.get("group_kind") == "question_options"
        and any((group.get("document_key"), int(page)) in pages for page in group.get("physical_pages", []))
    ]
    group_ids = [group.get("group_id") for group in groups]
    tagged_ids = {
        (row.get("source_question") or {}).get("group_id")
        for row in candidate.get("candidates", []) if row.get("source_question")
    }
    source_valid = len(group_ids) == len(set(group_ids)) and tagged_ids <= set(group_ids)
    outcomes = candidate.get("question_group_outcomes", [])
    by_group = {row.get("group_id"): row for row in outcomes}
    outcomes_valid = len(outcomes) == len(groups) and len(by_group) == len(outcomes) and set(by_group) == set(group_ids)
    for group in groups:
        items = group.get("items", [])
        member_ids = group.get("member_evidence_ids") or []
        stem_id = group.get("stem_evidence_id")
        options_id = group.get("options_evidence_id")
        answer_id = group.get("answer_evidence_id")
        answer_pair_ids = list(dict.fromkeys([stem_id, answer_id]))
        reviewed_members_valid = (
            bool(member_ids) and len(member_ids) == len(set(member_ids))
            and stem_id == member_ids[0] and answer_id in member_ids
            and options_id in member_ids
            and len(items) == group.get("source_total_items")
            and {evidence_id for item in items for evidence_id in (item.get("evidence_ids") or [item.get("evidence_id")])} == set(member_ids)
        )
        confirmed = (
            group.get("group_kind") == "question_options"
            and group.get("source_review_status") == "confirmed"
            and group.get("answer_marked") is True
            and group.get("answer_option") in {"A", "B", "C", "D"}
            and group.get("question_id") == group.get("source_title")
            and reviewed_members_valid
            and _source_group_confirmed(group, items, len(items), pages, evidence_ids, canonical_evidence)
        )
        source_valid = source_valid and confirmed
        members = [row for row in candidate.get("candidates", []) if (row.get("source_question") or {}).get("group_id") == group.get("group_id")]
        member_candidate_ids = [row.get("candidate_id") for row in members]
        outcome = by_group.get(group.get("group_id"), {})
        merged_source = {
            "evidence_ids": answer_pair_ids,
            "source_quotes": {evidence_id: quote for item in items for evidence_id, quote in (item.get("source_quotes") or {}).items() if evidence_id in answer_pair_ids},
            "source_span_ids": [span for evidence_id in answer_pair_ids for span in canonical_evidence.get(evidence_id, {}).get("source_span_ids", [])],
        }
        outcome_valid = (
            confirmed and bool(members)
            and outcome.get("status") == "complete"
            and len(member_candidate_ids) == len(set(member_candidate_ids))
            and set(outcome.get("candidate_ids", [])) == set(member_candidate_ids)
            and all(
                row.get("source_question", {}).get("status") == "linked"
                and row.get("statement_type") != "procedure"
                and not row.get("procedure_group") and not row.get("source_list_item")
                and [binding.get("evidence_id") for binding in row.get("evidence_bindings", [])][0:1] == [stem_id]
                and all(binding.get("support_type") == "direct" for binding in row.get("evidence_bindings", []))
                and _candidate_supports_source_member(row, merged_source, group, canonical_evidence)
                for row in members
            )
        )
        outcomes_valid = outcomes_valid and outcome_valid
    return {"question_group_source_confirmed": source_valid, "question_group_pairs_complete": outcomes_valid}


def audit() -> dict:
    contract = _read(ROOT / "config/stage12_statement_contract.json")
    provider_config = _read(ROOT / "config/stage12_provider.json")
    manifest = _read(STAGE12 / "stage12_input_manifest.json")
    baseline = _read(STAGE12 / "stage12_representative_baseline.json")
    routing = _read(ROOT / "config/stage12_profile_routing.json")
    registry = _read(ROOT / "data/stage11/evaluation_sample_registry.json")
    candidate = _read(STAGE12 / "stage12_development_candidates.json")
    development = _read(STAGE12 / "stage12_development_evaluation.json")
    coverage_matrix = _read(STAGE12 / "stage12_semantic_coverage_matrix.json")
    failure_summary = _read(STAGE12 / "stage12_real_llm_failure_summary.json") if (STAGE12 / "stage12_real_llm_failure_summary.json").exists() else {}
    stage6_coverage_path = ROOT / "data/stage6/stage6_semantic_coverage_audit.json"
    stage6_coverage = _read(stage6_coverage_path) if stage6_coverage_path.exists() else {}
    source_structure_path = ROOT / "data/stage6/stage6_source_structure_index.json"
    source_structure = _read(source_structure_path) if source_structure_path.exists() else {}
    stage11 = _read(ROOT / "data/stage11/stage11_exit_audit.json")
    canonical_rows = _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")
    canonical_evidence = {row["evidence"]["evidence_id"]: row["evidence"] for row in canonical_rows}
    extractability = {row["evidence"]["evidence_id"]: row.get("stage12_extractability") for row in canonical_rows}
    dev_gold = _jsonl(ROOT / "data/stage11/stage11_statement_development_samples.jsonl")
    development_gold_count = len(dev_gold)
    router = ProfileRouter(ROOT / "config/stage12_profile_routing.json")

    runtime_report = {"conforms": False, "failures": [], "counts": {}}
    try:
        validate_candidate_payload(candidate)
        to_stage9_runtime_payload(candidate["candidates"])
        runtime_report["conforms"] = True
    except (ValueError, KeyError, TypeError) as error:
        runtime_report["failures"] = [{"message": str(error)}]

    manifest_pages = {(page.get("document_logical_id"), int(page.get("physical_page"))): page for page in manifest.get("pages", [])}
    baseline_pages = {(page.get("document_logical_id"), int(page.get("physical_page"))): page for page in baseline.get("pages", [])}
    manifest_identities = set(manifest_pages)
    baseline_identities = set(baseline_pages)
    development_coverage_checks = _development_evidence_coverage(manifest, registry, candidate, canonical_evidence, extractability)
    operation_group_checks = _operation_group_coverage(source_structure, manifest, candidate, canonical_evidence)
    context_group_checks = _context_group_coverage(source_structure, manifest, candidate, canonical_evidence)
    question_group_checks = _question_group_coverage(source_structure, manifest, candidate, canonical_evidence)
    accepted_manifest_evidence = {evidence_id for page in manifest.get("pages", []) for evidence_id in page.get("evidence_ids", [])}
    candidate_evidence_ids = {binding.get("evidence_id") for item in candidate.get("candidates", []) for binding in item.get("evidence_bindings", [])}
    lineage_ok = True
    profile_ok = True
    for item in candidate.get("candidates", []):
        for binding in item.get("evidence_bindings", []):
            evidence = canonical_evidence.get(binding.get("evidence_id"))
            if evidence is None:
                lineage_ok = False
                continue
            try:
                if evidence.get("review_status") != "accepted":
                    raise ValueError("candidate is bound to non-accepted canonical Evidence")
                validate_candidate_evidence_binding(item, evidence)
                if binding == item.get("evidence_bindings", [None])[0]:
                    validate_candidate_against_evidence(item, evidence)
                profile = router.route(evidence)
                profile_ok = profile_ok and item.get("extraction_profile") == profile.extraction_profile_id
            except (ValueError, KeyError, TypeError):
                lineage_ok = False

    coverage = {item for page in manifest.get("pages", []) for item in page.get("coverage", [])}
    forbidden = json.dumps(candidate, ensure_ascii=False).lower()
    quality_thresholds = contract["evaluation"]["development_quality_gate"]
    acceptance_policy = contract["evaluation"]["acceptance_quality_gate"]
    development_quality = development.get("field_accuracy", {})
    adjudication = _read(OUTPUT_PATH)
    raw_evaluation = _read(RAW_EVALUATION_PATH) if RAW_EVALUATION_PATH.is_file() else {}
    review_record = _read(REVIEW_PATH) if REVIEW_PATH.is_file() else {}
    raw_lineage_current = (
        RAW_EVALUATION_PATH.is_file()
        and REVIEW_PATH.is_file()
        and raw_evaluation.get("input_sha256", {}).get("candidate") == _sha(STAGE12 / "stage12_development_candidates.json")
        and raw_evaluation.get("input_sha256", {}).get("gold") == _sha(ROOT / "data/stage11/stage11_statement_development_samples.jsonl")
        and adjudication.get("source_sha256", {}).get("evaluation") == _sha(RAW_EVALUATION_PATH)
        and adjudication.get("source_sha256", {}).get("review") == _sha(REVIEW_PATH)
        and review_record.get("status") == "reviewed_current_candidates"
    )
    dev_recomputed = compare_candidates(candidate.get("candidates", []), dev_gold, gold_exhaustive=False, evidence_by_id=canonical_evidence, adjudication=adjudication)
    provider_contract_ready = (
        provider_config.get("default_provider") == "external_llm"
        and provider_config.get("providers", {}).get("external_llm", {}).get("enabled") is True
    )
    real_llm_artifact = (
        candidate.get("provider_id") == "external_llm_openai_compatible_v1"
        and candidate.get("provider_metadata", {}).get("mode") == "real_llm"
        and development.get("real_llm_execution") is True
    )
    evidence_cache_dir = ROOT / "var/model_runs/stage12/evidence"
    validated_real_cache_count = 0
    expected_cache_key_to_evidence_id = {}
    cache_provider = provider_from_config()
    for page in manifest.get("pages", []):
        for evidence_id, evidence in _page_evidence(page, canonical_evidence, _source_groups(), _context_groups()).items():
            cache_key = _evidence_cache_key(evidence, router.route(evidence), cache_provider, manifest["source_split"])
            expected_cache_key_to_evidence_id[cache_key] = evidence_id
    strictly_valid_evidence_ids = _current_valid_evidence_ids(manifest, evidence_cache_dir.parent)
    # Cache lookup addresses only the current fingerprint keys. Old files are
    # ignored in place; this audit fails only if a current-key file is invalid.
    ignored_historical_cache_files = []
    invalid_current_cache_files = []
    cached_review_diagnostics = []
    if evidence_cache_dir.exists():
        for cache_path in evidence_cache_dir.glob("*.json"):
            if cache_path.stem not in expected_cache_key_to_evidence_id:
                ignored_historical_cache_files.append(cache_path.name)
                continue
            try:
                cached = _read(cache_path)
                if (
                    cached.get("provider_mode") == "real_llm"
                    and expected_cache_key_to_evidence_id[cache_path.stem] in strictly_valid_evidence_ids
                ):
                    validated_real_cache_count += 1
                    cached_review_diagnostics.extend(cached["review_diagnostics"])
                else:
                    invalid_current_cache_files.append(cache_path.name)
            except (OSError, json.JSONDecodeError, TypeError):
                invalid_current_cache_files.append(cache_path.name)
    stored_eval_matches = all(development.get(key) == dev_recomputed.get(key) for key in ("gold_statement_count", "candidate_count", "field_totals", "field_correct", "field_accuracy", "matched_field_totals", "matched_field_correct", "matched_field_accuracy", "coverage_metrics", "safety_metrics", "disagreement_summary", "adjudicated_disagreement_summary", "adjudicated_information_coverage", "adjudication_validation", "error_counts", "unmatched_gold", "unmatched_candidates", "gold_mismatch_count", "gold_mismatch_details", "evidence_binding", "evidence_semantic_support"))
    cached_review_diagnostics.sort(key=lambda item: (item["evidence_id"], item.get("candidate_id", ""), item["code"], item["message"]))
    review_diagnostics_current = candidate.get("review_diagnostics") == cached_review_diagnostics and validated_real_cache_count == len(expected_cache_key_to_evidence_id)
    dev_input_hashes_match = all(development.get("input_sha256", {}).get(key) == _sha(path) for key, path in {
        "candidate": STAGE12 / "stage12_development_candidates.json",
        "gold": ROOT / "data/stage11/stage11_statement_development_samples.jsonl",
        "manifest": STAGE12 / "stage12_input_manifest.json",
        "routing": ROOT / "config/stage12_profile_routing.json",
        "baseline": STAGE12 / "stage12_representative_baseline.json",
        "contract": ROOT / "config/stage12_statement_contract.json",
        "provider_config": ROOT / "config/stage12_provider.json",
        "prompt": ROOT / "config/stage12_prompt.txt",
        "response_schema": ROOT / "config/stage12_extraction_response.schema.json",
        "registry": ROOT / "data/registry/source_assets.jsonl",
        "adjudication": OUTPUT_PATH,
    }.items())
    robustness = _read(STAGE12 / "stage12_robustness_evaluation.json") if (STAGE12 / "stage12_robustness_evaluation.json").exists() else {}
    robustness_hashes = {
        "prompt": _sha(ROOT / "config/stage12_prompt.txt"),
        "contract": extraction_contract_fingerprint(),
        "response_schema": _sha(ROOT / "config/stage12_extraction_response.schema.json"),
        "candidate_schema": _sha(ROOT / "config/stage12_candidate.schema.json"),
        "semantic_source": extraction_source_fingerprint(),
        "provider_config": _sha(ROOT / "config/stage12_provider.json"),
        "cases": _sha(STAGE12 / "stage12_robustness_cases.json"),
    }
    robustness_input_hashes_match = all(robustness.get("input_sha256", {}).get(key) == value for key, value in robustness_hashes.items())
    candidate_input_refs = {
        key: {"path": key, "sha256": value}
        for key, value in candidate.get("input_sha256", {}).items()
        if key not in {"config/stage12_statement_contract.json", "src/turbine_kg/extraction/semantic.py", "evaluator"}
    }
    candidate_extraction_fingerprint = candidate.get("extraction_fingerprint") or {}
    candidate_lineage_current = (
        not verify_input_hashes(ROOT, candidate_input_refs)
        and candidate_extraction_fingerprint == {
            "contract": extraction_contract_fingerprint(),
            "prompt": _sha(ROOT / "config/stage12_prompt.txt"),
            "response_schema": _sha(ROOT / "config/stage12_extraction_response.schema.json"),
            "candidate_schema": _sha(ROOT / "config/stage12_candidate.schema.json"),
            "semantic_source": extraction_source_fingerprint(),
            "provider_config": _sha(ROOT / "config/stage12_provider.json"),
        }
    )
    manifest_paths = {
        "stage9_exit_audit": "data/stage9/stage9_exit_audit.json",
        "stage10_audit": "data/stage10/stage10_audit.json",
        "stage11_exit_audit": "data/stage11/stage11_exit_audit.json",
        "stage6_evidence_bundle": "data/stage6/stage6_evidence_bundle.jsonl",
        "stage6_semantic_coverage_audit": "data/stage6/stage6_semantic_coverage_audit.json",
        "stage6_source_structure_index": "data/stage6/stage6_source_structure_index.json",
        "stage11_evaluation_sample_registry": "data/stage11/evaluation_sample_registry.json",
        "stage12_representative_baseline": "data/stage12/stage12_representative_baseline.json",
        "stage12_profile_routing": "config/stage12_profile_routing.json",
    }
    manifest_input_refs = {key: {"path": path, "sha256": manifest.get("inputs", {}).get(key, "")} for key, path in manifest_paths.items()}
    manifest_lineage_current = not verify_input_hashes(ROOT, manifest_input_refs)
    development_input_scope_declared = (
        manifest.get("scope_kind") == "development_page_registry"
        and baseline.get("scope_kind") == "representative_page_baseline"
        and manifest.get("representative_chapter_claim_allowed") is False
        and baseline.get("representative_chapter_claim_allowed") is False
    )
    reserve_gold_path = STAGE12 / "stage12_reserve_acceptance.json"
    reserve_acceptance = _read(reserve_gold_path) if reserve_gold_path.exists() else {}
    reserve_manifest_path = STAGE12 / "stage12_reserve_freeze_manifest.json"
    reserve_manifest = _read(reserve_manifest_path) if reserve_manifest_path.exists() else {}
    reserve_consistency_path = STAGE12 / "stage12_reserve_gold_audit.json"
    reserve_consistency = _read(reserve_consistency_path) if reserve_consistency_path.exists() else {}
    reserve_execution_consumed = reserve_manifest.get("freeze_status") == "execution_consumed"
    reserve_gold_ready = (
        reserve_consistency.get("freeze_eligibility") == "READY_TO_FREEZE"
        and (reserve_consistency.get("adjudication_validation") or {}).get("complete_current_and_independently_provenanced") is True
        and reserve_manifest.get("freeze_status") in {"prepared_not_approved", "human_approved"}
        and verify_manifest(reserve_manifest).get("valid") is True
    )
    reserve_registry_records = [item for item in registry.get("records", []) if item.get("split") == "acceptance_holdout_reserve"]
    reserve_registry_ready = (
        len(reserve_registry_records) == 5
        and all(item.get("frozen") is True and item.get("review_status") == "reserved" for item in reserve_registry_records)
        and all((item.get("independence") or {}).get("statement") is True for item in reserve_registry_records)
    )

    implementation_checks = {
        "stage11_exit_gate": stage11.get("status") == "complete" and stage11.get("next_stage_allowed") is True,
        "stage11_internal_lineage_current": not verify_input_hashes(ROOT, stage11.get("inputs", {})),
        "stage6_semantic_coverage_complete": stage6_coverage.get("status") == "complete" and stage6_coverage.get("stage12_input_allowed") is True and stage6_coverage.get("unresolved_page_count") == 0 and stage6_coverage.get("confirmed_gap_page_count") == 0 and bool(stage6_coverage.get("inputs")) and not verify_input_hashes(ROOT, stage6_coverage["inputs"]),
        "stage6_source_structure_current": bool(source_structure.get("inputs")) and not verify_input_hashes(ROOT, source_structure["inputs"]),
        **development_coverage_checks,
        **operation_group_checks,
        **context_group_checks,
        **question_group_checks,
        "development_manifest_frozen": manifest.get("status") == "frozen" and not manifest.get("upstream_blockers"),
        "label_free_development_manifest": manifest.get("label_free_extractor_view") is True and manifest.get("source_split") == "development_regression_golden" and manifest.get("holdout_used_for_tuning") is False and manifest.get("blind_read") is False,
        "manifest_input_lineage_current": manifest_lineage_current,
        "representative_baseline_frozen": baseline.get("status") == "frozen_page_baseline" and baseline.get("scope_kind") == "representative_page_baseline" and baseline.get("representative_chapter_claim_allowed") is False and baseline_identities <= manifest_identities and all(set(page.get("evidence_ids", [])) <= set(manifest_pages[key].get("evidence_ids", [])) for key, page in baseline_pages.items()),
        "development_input_scope_declared": development_input_scope_declared,
        "five_documents_represented": len({page.get("document_key") for page in manifest.get("pages", [])}) == 5,
        "representative_coverage": {"numeric_unit", "range", "negation", "condition", "multi_object_or_step", "enumeration"} <= coverage,
        "candidate_only_boundary": candidate.get("status") == "candidate_only" and candidate.get("formal_release") is False and "authorized_action" not in forbidden,
        "no_holdout_or_blind_in_candidate": "acceptance_holdout" not in forbidden and "acceptance_holdout_reserve" not in forbidden and "blind_test" not in forbidden,
        "candidate_pages_are_manifest_pages": {(item.get("document_logical_id"), int(item.get("physical_page"))) for item in candidate.get("candidates", [])} <= set(manifest_pages) and candidate_evidence_ids <= accepted_manifest_evidence,
        "candidate_schema_and_stage9_gate": runtime_report["conforms"],
        "provider_contract_ready": provider_contract_ready,
        "real_llm_execution_verified": real_llm_artifact and validated_real_cache_count == len(expected_cache_key_to_evidence_id),
        "producer_reexecution_current": real_llm_artifact and validated_real_cache_count == len(expected_cache_key_to_evidence_id),
        "stale_cache_zero": not invalid_current_cache_files,
        "review_diagnostics_current": review_diagnostics_current,
        "candidate_input_lineage_current": candidate_lineage_current,
        "canonical_evidence_consumed": lineage_ok,
        "profile_routes_are_unique_and_consumed": profile_ok and len(routing.get("entries", [])) == 5 and {item.get("extraction_profile") for item in candidate.get("candidates", [])} == {entry.get("extraction_profile_id") for entry in routing.get("entries", [])},
        "development_evaluation_present": development.get("status") == "completed" and development.get("evaluator_version") == "stage12-field-evaluator-v7" and development.get("holdout_used_for_tuning") is False and development.get("real_llm_execution") is True and development.get("gold_statement_count") == development_gold_count and stored_eval_matches and dev_input_hashes_match,
        "development_adjudication_current": raw_lineage_current and adjudication.get("artifact_kind") == "stage12_development_disagreement_adjudication" and adjudication.get("status") == "completed" and adjudication.get("source_sha256", {}).get("gold") == _sha(ROOT / "data/stage11/stage11_statement_development_samples.jsonl") and adjudication.get("source_sha256", {}).get("candidate") == _sha(STAGE12 / "stage12_development_candidates.json") and development.get("raw_evaluation_sha256") == adjudication.get("source_sha256", {}).get("evaluation"),
        "exposed_holdout_excluded_from_acceptance": contract.get("evaluation", {}).get("acceptance_lifecycle", {}).get("current_holdout_eligible_for_final_acceptance") is False,
        "grounding_zero_tolerance": development.get("error_counts", {}).get("unsupported_claim") == 0,
        "no_ontology_or_release_write": candidate.get("inputs", {}).get("stage12_statement_contract") == "config/stage12_statement_contract.json",
        "robustness_evaluation_present": (STAGE12 / "stage12_robustness_evaluation.json").exists() and robustness.get("status") == "completed" and robustness.get("real_llm_execution") is True and robustness_input_hashes_match,
        "real_llm_failure_summary_current": failure_summary.get("artifact_kind") == "stage12_real_llm_failure_summary" and failure_summary.get("status") == "completed" and failure_summary.get("run_kind") == "development_batch" and failure_summary.get("source_split") == "development_regression_golden" and failure_summary.get("holdout_used_for_tuning") is False and failure_summary.get("blind_read") is False and failure_summary.get("input_manifest_sha256") == _sha(STAGE12 / "stage12_input_manifest.json") and len(failure_summary.get("evidence_ids", [])) == len(accepted_manifest_evidence) and set(failure_summary.get("evidence_ids", [])) == accepted_manifest_evidence and failure_summary.get("progress", {}).get("batch_complete") is True and failure_summary.get("progress", {}).get("valid_cached_evidence") == len(accepted_manifest_evidence) and "attempts" not in failure_summary and "failures" not in failure_summary,
        "semantic_coverage_matrix_current": (
            coverage_matrix.get("status") == "current"
            and coverage_matrix.get("producer") == "scripts/build_stage12_semantic_coverage_matrix.py"
            and coverage_matrix.get("gold_artifact") == "data/stage11/stage11_statement_development_samples.jsonl"
            and coverage_matrix.get("gold_sha256") == _sha(ROOT / "data/stage11/stage11_statement_development_samples.jsonl")
            and coverage_matrix.get("gold_statement_count") == development_gold_count
            and coverage_matrix.get("generator_sha256") == _sha(ROOT / "scripts/build_stage12_semantic_coverage_matrix.py")
        ),
        "reserve_registry_ready_for_independent_preparation": reserve_registry_ready,
    }
    development_quality_metrics_pass, development_gate_details = _development_quality_gate(development, quality_thresholds)
    development_quality_gate = bool(
        development_quality_metrics_pass
        and implementation_checks["development_evaluation_present"]
        and implementation_checks["development_adjudication_current"]
        and implementation_checks["candidate_input_lineage_current"]
        and implementation_checks["review_diagnostics_current"]
        and not candidate.get("review_diagnostics")
    )
    quality_checks = {
        "development_quality_gate": development_quality_gate,
        "robustness_quality_gate": robustness_input_hashes_match and robustness.get("status") == "completed" and robustness.get("case_count", 0) > 0 and robustness.get("failed_count") == 0,
        "acceptance_quality_gate": _reserve_acceptance_lineage(reserve_acceptance) and _reserve_acceptance_gate(reserve_gold_ready, reserve_acceptance, acceptance_policy),
    }
    checks = {**implementation_checks, **quality_checks}
    # A prior real run under an invalidated contract is historical evidence
    # only.  Current single-call verification requires either a cache carrying
    # current contract fingerprints or a successful current producer replay.
    real_llm_single_call_verified = validated_real_cache_count > 0
    real_llm_batch_execution = (
        real_llm_artifact
        and development.get("status") == "completed"
        and development.get("candidate_count", 0) > 0
        and checks["producer_reexecution_current"]
        and checks["stale_cache_zero"]
    )
    development_quality_gate = quality_checks["development_quality_gate"]
    production_llm_pipeline_ready = real_llm_batch_execution and provider_contract_ready and checks["candidate_schema_and_stage9_gate"] and checks["canonical_evidence_consumed"]
    lifecycle_blockers = set()
    if not reserve_gold_ready:
        lifecycle_blockers.add("upstream_reserve_gold_not_ready")
    blockers = sorted({name for name, passed in checks.items() if not passed} | lifecycle_blockers)
    pipeline_checks = {
        name: implementation_checks[name]
        for name in (
            "stage11_exit_gate", "stage6_semantic_coverage_complete", "stage6_source_structure_current",
            "development_registry_coverage", "development_evidence_outcomes_complete",
            "operation_group_source_confirmed", "operation_group_steps_complete",
            "context_group_source_confirmed", "context_group_items_complete",
            "question_group_source_confirmed", "question_group_pairs_complete",
            "development_manifest_frozen",
            "development_input_scope_declared",
            "provider_contract_ready", "real_llm_execution_verified", "producer_reexecution_current",
            "stale_cache_zero", "candidate_input_lineage_current", "candidate_schema_and_stage9_gate",
            "canonical_evidence_consumed", "profile_routes_are_unique_and_consumed",
            "development_evaluation_present", "development_adjudication_current", "exposed_holdout_excluded_from_acceptance",
            "real_llm_failure_summary_current", "semantic_coverage_matrix_current",
        )
    }
    pipeline_ready = all(pipeline_checks.values())
    prefreeze_ready = pipeline_ready and quality_checks["development_quality_gate"] and quality_checks["robustness_quality_gate"] and checks["stage6_semantic_coverage_complete"] and checks["development_registry_coverage"] and checks["development_evidence_outcomes_complete"] and checks["operation_group_steps_complete"] and checks["context_group_items_complete"] and checks["question_group_pairs_complete"] and checks["stage11_exit_gate"]
    quality_accepted = all(quality_checks.values()) and not lifecycle_blockers
    status = "complete" if pipeline_ready and quality_accepted else "in_progress"
    independent_acceptance = quality_checks["acceptance_quality_gate"]
    gates = {
        "REAL_LLM_SINGLE_CALL_VERIFIED": real_llm_single_call_verified,
        "REAL_LLM_BATCH_EXECUTION": real_llm_batch_execution,
        "PRODUCTION_LLM_PIPELINE_READY": production_llm_pipeline_ready,
        "DEVELOPMENT_QUALITY_GATE": development_quality_gate,
        "ROBUSTNESS_QUALITY_GATE": quality_checks["robustness_quality_gate"],
        "INDEPENDENT_ACCEPTANCE": independent_acceptance,
        "PREFREEZE_READY": prefreeze_ready,
        "STAGE12_EXIT": status == "complete",
    }
    return {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_exit_audit",
        "status": status,
        "implementation_status": "complete" if pipeline_ready else "in_progress",
        "quality_status": "accepted" if quality_accepted else "quality_not_accepted",
        "pipeline_ready": pipeline_ready,
        "prefreeze_ready": prefreeze_ready,
        "quality_accepted": quality_accepted,
        "reserve_ready": reserve_gold_ready,
        "reserve_execution_consumed": reserve_execution_consumed,
        "reserve_consistency_status": reserve_consistency.get("freeze_eligibility", "not_prepared"),
        "reserve_llm_calls": reserve_consistency.get("reserve_model_invocations"),
        "ignored_historical_cache_count": len(ignored_historical_cache_files),
        "invalid_current_key_cache_count": len(invalid_current_cache_files),
        "historical_holdout_status": "exposed_not_eligible",
        "final_acceptance_source": "independent_reserve",
        "reserve_gold_status": reserve_consistency.get("gold_status", "not_prepared"),
        "reserve_acceptance_status": reserve_acceptance.get("status", "not_prepared"),
        "formal_release": False,
        "producer": "scripts/audit_stage12_exit.py",
        "inputs": {name: {"path": name, "sha256": _sha(ROOT / name) if (ROOT / name).is_file() else None} for name in (
            "data/stage9/stage9_exit_audit.json", "data/stage10/stage10_audit.json", "data/stage11/stage11_exit_audit.json", "data/stage11/evaluation_sample_registry.json", "data/stage12/stage12_representative_baseline.json", "config/stage12_profile_routing.json", "data/stage12/stage12_input_manifest.json", "data/stage6/stage6_evidence_bundle.jsonl", "data/stage6/stage6_semantic_coverage_audit.json", "data/stage6/stage6_source_structure_index.json", "config/stage12_statement_contract.json", "config/stage12_candidate.schema.json", "config/stage12_provider.json", "config/stage12_prompt.txt", "config/stage12_extraction_response.schema.json", "src/turbine_kg/extraction/semantic.py", "ontology/stage9_core.ttl", "ontology/stage9_shapes.ttl", "data/stage12/stage12_development_candidates.json", "data/stage12/stage12_development_raw_evaluation.json", "data/stage12/stage12_development_disagreement_review.json", "data/stage12/stage12_development_evaluation.json", "data/stage12/stage12_development_disagreement_adjudication.json", "data/stage12/stage12_semantic_coverage_matrix.json", "scripts/build_stage12_semantic_coverage_matrix.py", "scripts/build_stage12_development_adjudication.py", "data/stage12/stage12_robustness_cases.json", "data/stage12/stage12_robustness_evaluation.json", "data/stage12/stage12_fixture_robustness_evaluation.json", "data/stage12/stage12_real_llm_failure_summary.json", "data/stage12/stage12_reserve_source_regions.json", "data/stage12/stage12_reserve_evidence.jsonl", "data/stage12/stage12_reserve_gold_reviews.json", "data/stage12/stage12_reserve_gold.jsonl", "data/stage12/stage12_reserve_gold_audit.json", "data/stage12/stage12_reserve_freeze_manifest.json", "data/registry/source_assets.jsonl", "data/registry/source_manual_findings.jsonl", "data/project_state.json",
        )},
        "outputs": {"input_manifest": "data/stage12/stage12_input_manifest.json", "development_candidates": "data/stage12/stage12_development_candidates.json", "development_evaluation": "data/stage12/stage12_development_evaluation.json", "development_adjudication": "data/stage12/stage12_development_disagreement_adjudication.json", "reserve_gold": "data/stage12/stage12_reserve_gold.jsonl", "reserve_freeze_manifest": "data/stage12/stage12_reserve_freeze_manifest.json", "reserve_acceptance": "data/stage12/stage12_reserve_acceptance.json", "real_llm_failure_summary": "data/stage12/stage12_real_llm_failure_summary.json", "exit_audit": "data/stage12/stage12_exit_audit.json", "runtime_cache": "var/model_runs/stage12"},
        "checks": checks,
        "development_gate_details": development_gate_details,
        "pipeline_checks": pipeline_checks,
        "gates": gates,
        "failure_isolation": "Invalid candidates remain outside accepted Gold, formal knowledge, Release and Neo4j. Holdout evaluation writes metrics only; failed runtime validation never replaces a successful cache entry.",
        "rollback": "Restore the previous verified Stage 11/Stage 12 artifacts and rerun the same development input manifest; do not tune against holdout results.",
        "zero_tolerance_errors": [name for name, count in {"development_unsupported_claim": development.get("error_counts", {}).get("unsupported_claim", 0)}.items() if count],
        "blockers": blockers,
        "next_stage_allowed": False,
        "next_stage": "Stage 13 formal entry blocked by user freeze; no Stage 13 preparation in this run",
        "next_stage_inputs": {},
        "consumers": ["tests/stage12"],
    }


if __name__ == "__main__":
    result = audit()
    (STAGE12 / "stage12_exit_audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "blockers": result["blockers"]}, ensure_ascii=False))

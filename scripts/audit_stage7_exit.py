"""Audit the Stage 7 candidate-only boundary and its human-review gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from turbine_kg.terminology.analyzer import load_terminology_contract
from turbine_kg.terminology.models import CANDIDATE_TYPES
from turbine_kg.terminology.validation import content_fingerprint, validate_candidates, validate_input_manifest


ROOT = Path(__file__).resolve().parents[1]
STAGE7 = ROOT / "data" / "stage7"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_git_head() -> str:
    head_path = ROOT / ".git" / "HEAD"
    if not head_path.exists():
        return ""
    head = head_path.read_text(encoding="utf-8").strip()
    if head.startswith("ref: "):
        ref_path = ROOT / ".git" / head.removeprefix("ref: ")
        return ref_path.read_text(encoding="utf-8").strip() if ref_path.exists() else ""
    return head


def _review_decisions_are_safe(decisions: list[dict], queue_by_id: dict[str, dict], candidate_by_id: dict[str, dict]) -> bool:
    for decision in decisions:
        queue = queue_by_id.get(decision.get("queue_id"))
        candidate = candidate_by_id.get(decision.get("candidate_id"))
        if not queue or not candidate:
            return False
        if decision.get("candidate_fingerprint") != candidate["content_fingerprint"]:
            return False
        if decision.get("manifest_fingerprint") != queue.get("manifest_fingerprint"):
            return False
        if decision.get("decision") == "accepted":
            if decision.get("human_reviewed") is not True:
                return False
            if decision.get("consumer_permission") != "stage8_mapping":
                return False
            if not decision.get("evidence_refs"):
                return False
        elif decision.get("decision") not in {"rejected", "deferred", "needs_more_evidence", "superseded"}:
            return False
    return True


def main() -> None:
    manifest = _read(STAGE7 / "terminology_input_manifest.json")
    candidates_payload = _read(STAGE7 / "terminology_candidates.json")
    capability = _read(STAGE7 / "business_capability_questions.json")
    review_summary = _read(STAGE7 / "stage7_human_review_summary.json")
    queue = _jsonl(STAGE7 / "terminology_review_queue.jsonl")
    relationship_queue = _jsonl(STAGE7 / "terminology_relationship_review_queue.jsonl")
    decisions = _jsonl(STAGE7 / "terminology_review_decisions.jsonl")
    contract = load_terminology_contract(ROOT / "config" / "terminology_contract.json")
    validate_input_manifest(manifest)
    pages = manifest["pages"]
    accepted_keys = {(row["document_logical_id"], row["physical_page"]) for row in pages if row["page_status"] == "text_accepted"}
    candidates = candidates_payload.get("candidates", [])
    candidate_validation = validate_candidates(candidates, accepted_keys)
    candidate_by_id = {row["candidate_id"]: row for row in candidates}
    page_by_key = {(row["document_logical_id"], row["physical_page"]): row for row in pages}

    traceability_ok = all(
        occurrence["text_fingerprint"] == page_by_key[(occurrence["document_logical_id"], occurrence["physical_page"])]
        ["analysis_text_sha256"]
        and occurrence["text_origin"] in {"native_text", "ocr_text"}
        and (
            occurrence["text_origin"] != "ocr_text"
            or (occurrence["source_kind"] == "accepted_stage6_evidence" and bool(occurrence.get("evidence_ids")))
        )
        for candidate in candidates
        for occurrence in candidate["occurrences"]
    )
    table_isolated = all(
        not page.get("table_candidate") or page["page_status"] != "text_accepted"
        for page in pages
    ) and all(
        not page_by_key[(occurrence["document_logical_id"], occurrence["physical_page"])].get("table_candidate")
        for candidate in candidates
        for occurrence in candidate["occurrences"]
    )
    ocr_candidates_explicit = all(
        occurrence["text_origin"] != "ocr_text" or candidate["candidate_type"] == "ocr_variant_candidate"
        for candidate in candidates
        for occurrence in candidate["occurrences"]
    )
    ocr_pages_have_canonical_source = all(
        page["text_source"] != "stage6_accepted_evidence"
        or (page["stage6_sample_status"] == "accepted_text_evidence" and page["stage6_evidence_ids"])
        for page in pages
    )
    candidate_fingerprints = all(
        content_fingerprint({key: value for key, value in row.items() if key != "content_fingerprint"}) == row["content_fingerprint"]
        for row in candidates
    )
    observed_types = sorted({row["candidate_type"] for row in candidates})
    unobserved_declared_types = sorted(set(contract["candidate_types"]) - set(observed_types))

    queue_ids = [row.get("queue_id") for row in queue]
    queue_candidate_ids = [row.get("candidate_id") for row in queue]
    queue_by_id = {row["queue_id"]: row for row in queue}
    expected_review_ids = {
        row["candidate_id"]
        for row in candidates
        if row["candidate_type"] in set(contract["review"]["blocking_candidate_types"])
        or "ocr_text" in row["text_origins"]
    }
    queue_coverage = (
        len(queue_ids) == len(set(queue_ids))
        and len(queue_candidate_ids) == len(set(queue_candidate_ids))
        and set(queue_candidate_ids) == expected_review_ids
        and all(
            row["blocking"] is True
            and row["status"] == "pending_human_review"
            and row["human_review_required"] is True
            and row["candidate_id"] in candidate_by_id
            and row["candidate_fingerprint"] == candidate_by_id[row["candidate_id"]]["content_fingerprint"]
            and row["manifest_fingerprint"] == manifest["content_fingerprint"]
            for row in queue
        )
    )
    relationship_types = {row.get("candidate_type") for row in relationship_queue}
    relationship_queue_ok = (
        relationship_types == set(contract["review"]["unresolved_relationship_types_require_user_review"])
        and len(relationship_queue) == len(relationship_types)
        and all(row.get("blocking") is True and row.get("status") == "pending_human_review" for row in relationship_queue)
    )
    decisions_ok = (
        len({row.get("queue_id") for row in decisions}) == len(decisions)
        and {row.get("queue_id") for row in decisions} <= set(queue_by_id)
        and _review_decisions_are_safe(decisions, queue_by_id, candidate_by_id)
    )
    capability_questions = capability.get("questions", [])
    capability_ids = {row.get("question_id") for row in capability_questions}
    capability_shape_ok = (
        len(capability_questions) == 10
        and len(capability_ids) == 10
        and all(row.get("question_template") and row.get("required_slots") for row in capability_questions)
    )
    test_evidence = _read(STAGE7 / "stage7_test_evidence.json")
    structural_checks = {
        "input_manifest_frozen": manifest.get("status") == "frozen",
        "all_775_pages_have_one_status": len(pages) == 775 and sum(manifest["status_counts"].values()) == 775,
        "only_five_admitted_units": manifest["input_boundary"].get("source_count") == 5 and manifest["input_boundary"].get("unauthorized_source_count") == 0,
        "case_holdout_blind_materials_explicitly_excluded": set(manifest["input_boundary"].get("excluded_source_classes", {})) == {"formal_case_materials", "holdout_materials", "blind_test_materials"} and bool(manifest["input_boundary"].get("exclusion_enforcement")),
        "non_accepted_pages_not_consumed": traceability_ok,
        "table_pages_are_isolated": table_isolated,
        "ocr_pages_bind_to_stage6_canonical_evidence": ocr_pages_have_canonical_source,
        "ocr_candidates_are_explicit": ocr_candidates_explicit,
        "candidate_types_are_controlled": all(row["candidate_type"] in CANDIDATE_TYPES for row in candidates),
        "candidate_fingerprints_stable": candidate_fingerprints,
        "candidate_traceability_complete": traceability_ok and candidate_validation["candidate_count"] == len(candidates),
        "occurrence_counts_are_complete": all(row["occurrence_count"] == len(row["occurrences"]) for row in candidates),
        "document_frequencies_reconcile": all(row["document_frequency"] == len({item["document_logical_id"] for item in row["occurrences"]}) for row in candidates),
        "review_queue_covers_all_blocking_candidates": queue_coverage,
        "relationship_review_queue_is_explicit": relationship_queue_ok,
        "review_decisions_are_fingerprint_bound": decisions_ok,
        "capability_questions_complete": capability_shape_ok,
        "candidate_only_no_promotion": candidates_payload.get("status") == "candidate_only" and candidates_payload.get("automatic_promotion") is False,
        "formal_release_false": candidates_payload.get("formal_release") is False and capability.get("formal_release") is False,
        "stage6_consumer_fingerprint_recorded": manifest["inputs"].get("stage6_exit_sha256") == _sha(ROOT / "data" / "stage6" / "stage6_exit_audit.json"),
        "runtime_contract_fingerprint_recorded": candidates_payload.get("contract_sha256") == _sha(ROOT / "config" / "terminology_contract.json"),
        "failure_isolation_and_rollback_declared": all(contract.get("failure_handling", {}).get(key) for key in ("changed_input_fingerprint", "invalid_output", "rollback")),
        "test_evidence_is_current_and_passing": test_evidence.get("status") == "passed" and test_evidence.get("commit") == _read_git_head(),
        "formal_artifacts_have_consumers": bool(review_summary.get("consumer")) and bool(candidates_payload.get("consumer")),
    }
    failures = [name for name, passed in structural_checks.items() if not passed]
    open_review = bool(set(queue_candidate_ids) - {row.get("candidate_id") for row in decisions}) or bool(relationship_queue)
    status = "blocked" if failures else "blocked_pending_user_review" if open_review else "complete"
    audit = {
        "schema_version": 1,
        "stage": "7",
        "artifact_kind": "stage7_exit_audit",
        "status": status,
        "scope": "candidate terminology and business capability templates only",
        "formal_release": False,
        "producer": "scripts/audit_stage7_exit.py",
        "inputs": {
            "terminology_input_manifest": "data/stage7/terminology_input_manifest.json",
            "terminology_candidates": "data/stage7/terminology_candidates.json",
            "business_capability_questions": "data/stage7/business_capability_questions.json",
            "review_queue": "data/stage7/terminology_review_queue.jsonl",
            "relationship_review_queue": "data/stage7/terminology_relationship_review_queue.jsonl",
            "review_decisions": "data/stage7/terminology_review_decisions.jsonl",
            "test_evidence": "data/stage7/stage7_test_evidence.json",
        },
        "counts": {
            "page_count": len(pages),
            "text_accepted_page_count": sum(row["page_status"] == "text_accepted" for row in pages),
            "visual_only_page_count": sum(row["page_status"] == "visual_only" for row in pages),
            "ocr_accepted_page_count": sum(row["text_source"] == "stage6_accepted_evidence" for row in pages),
            "table_candidate_page_count": sum(bool(row.get("table_candidate")) for row in pages),
            "candidate_count": len(candidates),
            "review_queue_count": len(queue),
            "relationship_review_queue_count": len(relationship_queue),
            "review_decision_count": len(decisions),
            "capability_question_count": len(capability_questions),
        },
        "candidate_type_counts": {candidate_type: sum(row["candidate_type"] == candidate_type for row in candidates) for candidate_type in sorted(CANDIDATE_TYPES)},
        "unobserved_declared_candidate_types": unobserved_declared_types,
        "candidate_scope": {
            "stage6_evidence_is_sample_cross_check_only": True,
            "full_document_evidence_claim": False,
            "engineering_statements_created": False,
            "owl_or_neo4j_changes_created": False,
            "release_created": False,
        },
        "checks": structural_checks,
        "failures": failures,
        "failure_isolation": contract["failure_handling"],
        "rollback": {"policy": contract["failure_handling"]["rollback"], "automatic_activation": False},
        "dead_code_orphan_output_review": {
            "stage6_canonical_lookup_is_enforced_for_accepted_ocr": True,
            "occurrence_truncation": False,
            "relationship_queue_consumer": "human reviewer",
            "unobserved_candidate_types_are_not_claimed_as_implemented": unobserved_declared_types,
        },
        "test_evidence": test_evidence,
        "user_review_required_now": {
            "candidate_queue_count": len(queue),
            "relationship_queue_count": len(relationship_queue),
            "ocr_pages_to_review": sum(row["text_source"] == "stage6_accepted_evidence" for row in pages),
            "visual_or_table_pages_to_review": sum(row["page_status"] == "visual_only" and row.get("table_candidate") for row in pages),
            "required_candidate_types": sorted(set(contract["review"]["blocking_candidate_types"])),
            "required_relationship_types": sorted(contract["review"]["unresolved_relationship_types_require_user_review"]),
            "instructions": "Record one human decision per candidate queue row; accepted requires reviewer, timestamp, matching fingerprints, evidence_refs, and consumer_permission=stage8_mapping.",
        },
        "review_boundary": "Candidate-only terms remain non-promoted until explicit human review; this audit does not authorize Stage 8 mapping.",
        "next_stage_allowed": False,
        "next_stage": None,
    }
    (STAGE7 / "stage7_exit_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "checks": len(structural_checks), "failures": failures, "candidate_count": len(candidates), "review_queue_count": len(queue)}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

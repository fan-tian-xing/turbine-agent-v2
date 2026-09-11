"""Audit the Stage 7 candidate-only terminology boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

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


def main() -> None:
    manifest = _read(STAGE7 / "terminology_input_manifest.json")
    candidates_payload = _read(STAGE7 / "terminology_candidates.json")
    capability = _read(STAGE7 / "business_capability_questions.json")
    queue = _jsonl(STAGE7 / "terminology_review_queue.jsonl")
    decisions = _jsonl(STAGE7 / "terminology_review_decisions.jsonl")
    validate_input_manifest(manifest)
    pages = manifest["pages"]
    accepted_keys = {(row["document_logical_id"], row["physical_page"]) for row in pages if row["page_status"] == "text_accepted"}
    candidates = candidates_payload.get("candidates", [])
    candidate_validation = validate_candidates(candidates, accepted_keys)
    recomputed_candidate_fingerprints = [
        content_fingerprint({key: value for key, value in row.items() if key != "content_fingerprint"}) == row["content_fingerprint"]
        for row in candidates
    ]
    page_by_key = {(row["document_logical_id"], row["physical_page"]): row for row in pages}
    traceability_ok = all(
        occurrence["text_fingerprint"] == page_by_key[(occurrence["document_logical_id"], occurrence["physical_page"])]
        ["processing_text_sha256"]
        and occurrence["text_origin"] in {"native_text", "ocr_text"}
        for candidate in candidates
        for occurrence in candidate["occurrences"]
    )
    queue_by_candidate = {row["candidate_id"]: row for row in queue}
    decision_by_queue = {row["queue_id"]: row for row in decisions}
    capability_questions = capability.get("questions", [])
    capability_ids = {row.get("question_id") for row in capability_questions}
    capability_shape_ok = (
        len(capability_questions) == 10
        and len(capability_ids) == 10
        and all(row.get("question_template") and row.get("required_slots") for row in capability_questions)
    )
    checks = {
        "input_manifest_frozen": manifest.get("status") == "frozen",
        "all_775_pages_have_one_status": len(pages) == 775 and sum(manifest["status_counts"].values()) == 775,
        "only_five_admitted_units": manifest["input_boundary"].get("source_count") == 5 and manifest["input_boundary"].get("unauthorized_source_count") == 0,
        "non_accepted_pages_not_consumed": traceability_ok,
        "candidate_types_are_controlled": all(row["candidate_type"] in CANDIDATE_TYPES for row in candidates),
        "candidate_fingerprints_stable": all(recomputed_candidate_fingerprints),
        "candidate_traceability_complete": traceability_ok,
        "ocr_candidates_are_explicit": all(
            row["candidate_type"] != "ocr_variant_candidate" or row["is_ocr_variant"] is True
            for row in candidates
        ),
        "review_records_reconcile": set(queue_by_candidate) == {row["candidate_id"] for row in decisions} and set(decision_by_queue) == {row["queue_id"] for row in queue},
        "review_queue_is_non_blocking_boundary": all(not row["blocking"] and row["status"] == "deferred_not_promoted" for row in queue),
        "capability_questions_complete": capability_shape_ok,
        "candidate_only_no_promotion": candidates_payload.get("status") == "candidate_only" and candidates_payload.get("automatic_promotion") is False,
        "formal_release_false": candidates_payload.get("formal_release") is False and capability.get("formal_release") is False,
        "stage6_consumer_fingerprint_recorded": manifest["inputs"].get("stage6_exit_sha256") == _sha(ROOT / "data" / "stage6" / "stage6_exit_audit.json"),
    }
    failures = [name for name, passed in checks.items() if not passed]
    audit = {
        "schema_version": 1,
        "stage": "7",
        "artifact_kind": "stage7_exit_audit",
        "status": "complete" if not failures else "blocked",
        "scope": "candidate terminology and business capability templates only",
        "formal_release": False,
        "producer": "scripts/audit_stage7_exit.py",
        "inputs": {
            "terminology_input_manifest": "data/stage7/terminology_input_manifest.json",
            "terminology_candidates": "data/stage7/terminology_candidates.json",
            "business_capability_questions": "data/stage7/business_capability_questions.json",
            "review_queue": "data/stage7/terminology_review_queue.jsonl",
            "review_decisions": "data/stage7/terminology_review_decisions.jsonl",
        },
        "counts": {
            "page_count": len(pages),
            "text_accepted_page_count": sum(row["page_status"] == "text_accepted" for row in pages),
            "candidate_count": len(candidates),
            "review_queue_count": len(queue),
            "review_decision_count": len(decisions),
            "capability_question_count": len(capability_questions),
        },
        "candidate_scope": {
            "stage6_evidence_is_sample_cross_check_only": True,
            "full_document_evidence_claim": False,
            "engineering_statements_created": False,
            "owl_or_neo4j_changes_created": False,
            "release_created": False,
        },
        "checks": checks,
        "failures": failures,
        "user_review_required_now": [],
        "review_boundary": "Candidate-only terms, especially OCR, numeric, requirement, and applicability candidates, remain non-promoted until separate human review.",
        "next_stage_allowed": not failures,
        "next_stage": "Stage 8 minimal OWL ontology design" if not failures else None,
    }
    (STAGE7 / "stage7_exit_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "checks": len(checks), "failures": failures, "candidate_count": len(candidates)}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

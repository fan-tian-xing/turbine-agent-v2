"""Build the canonical, user-authorized Stage 12 Development adjudication.

This artifact classifies raw evaluator disagreements; it never edits Gold or
Candidate and is consumed only by the deterministic Development evaluator.
"""

from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path
from typing import Any

from turbine_kg.extraction.semantic import compare_candidates

ROOT = Path(__file__).resolve().parents[1]
STAGE12 = ROOT / "data/stage12"
GOLD_PATH = ROOT / "data/stage11/stage11_statement_development_samples.jsonl"
CANDIDATE_PATH = STAGE12 / "stage12_development_candidates.json"
RAW_EVALUATION_PATH = STAGE12 / "stage12_development_raw_evaluation.json"
OUTPUT_PATH = STAGE12 / "stage12_development_disagreement_adjudication.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


REVIEW_PATH = STAGE12 / "stage12_development_disagreement_review.json"


def build_raw_evaluation() -> dict[str, Any]:
    """Evaluate the current Development candidates before any human decisions."""
    candidates = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))["candidates"]
    gold = _jsonl(GOLD_PATH)
    evidence_by_id = {
        row["evidence"]["evidence_id"]: row["evidence"]
        for row in _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")
    }
    report = compare_candidates(candidates, gold, gold_exhaustive=False, evidence_by_id=evidence_by_id)
    report["input_sha256"] = {"candidate": _sha(CANDIDATE_PATH), "gold": _sha(GOLD_PATH)}
    RAW_EVALUATION_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _bind_ids(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gold = {row["statement_id"]: row for row in _jsonl(GOLD_PATH)}
    candidate = {row["candidate_id"]: row for row in json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))["candidates"]}
    matched_candidates = {
        item.get("statement_id"): item.get("candidate_id")
        for item in json.loads(RAW_EVALUATION_PATH.read_text(encoding="utf-8")).get("matched_pairs", [])
    }
    output = []
    for index, item in enumerate(decisions, start=1):
        gold_id = item.get("gold")
        candidate_id = item.get("candidate") or (matched_candidates.get(gold_id) if gold_id else None)
        source = gold.get(gold_id) if gold_id else candidate.get(candidate_id)
        if source is None:
            raise ValueError(f"adjudication reference not found: {gold_id or candidate_id}")
        source_bindings = source.get("evidence_bindings") or []
        if not source_bindings:
            raise ValueError(f"adjudication reference has no Evidence binding: {gold_id or candidate_id}")
        if candidate_id:
            candidate_row = candidate.get(candidate_id)
            if candidate_row is None:
                raise ValueError(f"adjudication candidate reference not found: {candidate_id}")
            candidate_evidence = {str(item.get("evidence_id")) for item in candidate_row.get("evidence_bindings") or []}
            gold_evidence = {str(item.get("evidence_id")) for item in source_bindings}
            if not candidate_evidence & gold_evidence:
                raise ValueError(f"adjudication Gold/Candidate Evidence mismatch: {gold_id}, {candidate_id}")
        critical = bool(item.get("critical", False))
        output.append({
            "disagreement_id": f"stage12-development-disagreement-{index:02d}",
            "gold_statement_id": gold_id,
            "candidate_id": candidate_id,
            "evidence_id": str(source_bindings[0].get("evidence_id")),
            "decision": item["decision"],
            "subtype": item["subtype"],
            "reason": item["reason"],
            "critical": critical,
            "severity": "critical" if critical else ("noncritical" if item["decision"] == "A" else "not_applicable"),
            "information_coverage": bool(item.get("information_coverage", item["decision"] == "D")),
            "review_provenance": "user acceptance policy delegated review: completeness + Evidence support + no fabrication; independent Reviewer A/B not claimed",
        })
    return output


def build() -> dict[str, Any]:
    build_raw_evaluation()
    review = json.loads(REVIEW_PATH.read_text(encoding="utf-8"))
    if review.get("status") != "reviewed_current_candidates":
        raise ValueError("Development decisions have not been rebound to current Candidate and Gold")
    decisions = _bind_ids(list(review.get("decisions") or []))
    artifact = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_development_disagreement_adjudication",
        "status": "completed",
        "formal_release": False,
        "producer": "scripts/build_stage12_development_adjudication.py",
        "decision_basis": "user acceptance policy delegated review: completeness + Evidence support + no fabrication",
        "independent_reviewer_ab_claimed": False,
        "source_sha256": {
            "evaluation": _sha(RAW_EVALUATION_PATH),
            "gold": _sha(GOLD_PATH),
            "candidate": _sha(CANDIDATE_PATH),
            "review": _sha(REVIEW_PATH),
        },
        "decisions": decisions,
        "summary": {
            "total": len(decisions),
            "acceptable_semantic_equivalence": sum(item["decision"] == "D" for item in decisions),
            "confirmed_model_error": sum(item["decision"] == "A" for item in decisions),
            "confirmed_critical_model_error": sum(item["decision"] == "A" and item["critical"] for item in decisions),
            "confirmed_noncritical_model_error": sum(item["decision"] == "A" and not item["critical"] for item in decisions),
            "gold_error": sum(item["decision"] == "G" for item in decisions),
            "evaluator_error": sum(item["decision"] == "E" for item in decisions),
            "pending": 0,
        },
        "consumer": "scripts/build_stage12_candidates.py::evaluate_development and scripts/audit_stage12_exit.py",
    }
    OUTPUT_PATH.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-only", action="store_true")
    args = parser.parse_args()
    if args.raw_only:
        result = build_raw_evaluation()
        print(json.dumps({"gold_statement_count": result.get("gold_statement_count"), "candidate_count": result.get("candidate_count"), "disagreement_summary": result.get("disagreement_summary")}, ensure_ascii=False))
    else:
        result = build()
        print(json.dumps(result["summary"], ensure_ascii=False))

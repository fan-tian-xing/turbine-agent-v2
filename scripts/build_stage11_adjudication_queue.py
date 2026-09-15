"""Build the Stage 11 semantic adjudication queue from two blind review rounds."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REVIEW_A = ROOT / "data/stage11/review_round_a.jsonl"
REVIEW_B = ROOT / "data/stage11/review_round_b.jsonl"
OUTPUT = ROOT / "data/stage11/stage11_adjudication_queue.jsonl"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _digest(row: dict) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build() -> list[dict]:
    review_a = {row["sample_id"]: row for row in _jsonl(REVIEW_A)}
    review_b = {row["sample_id"]: row for row in _jsonl(REVIEW_B)}
    sample_ids = sorted(set(review_a) & set(review_b))
    queue: list[dict] = []
    for sample_id in sample_ids:
        a = review_a[sample_id]
        b = review_b[sample_id]
        a_units = a.get("semantic_units", [])
        b_units = b.get("proposed_statements", [])
        a_count = len(a_units)
        b_count = len(b_units)
        unresolved = bool(a.get("unresolved_issues")) or a.get("decision") in {"needs_manual_review", "isolate"} or b.get("decision") in {"needs_manual_review", "isolate"}
        count_conflict = a_count != b_count
        decision_conflict = a.get("decision") != b.get("decision")
        status = "needs_adjudication" if unresolved or count_conflict or decision_conflict else "aligned_pending_adjudication"
        queue.append({
            "sample_id": sample_id,
            "split": "acceptance_holdout" if sample_id.startswith("stage11-holdout-") else "development_regression_golden",
            "document_key": a.get("document_key") or b.get("document_key"),
            "physical_page": a.get("physical_page") or b.get("physical_page"),
            "reviewer_a_id": a.get("reviewer_id"),
            "reviewer_b_id": b.get("reviewer_id"),
            "reviewer_a_input_sha256": a.get("input_sha256"),
            "reviewer_b_input_sha256": b.get("input_sha256"),
            "reviewer_a_output_sha256": a.get("output_sha256"),
            "reviewer_b_output_sha256": b.get("output_sha256"),
            "reviewer_a_statement_count": a_count,
            "reviewer_b_statement_count": b_count,
            "reviewer_a_decision": a.get("decision"),
            "reviewer_b_decision": b.get("decision"),
            "conflict_flags": {
                "statement_count": count_conflict,
                "decision": decision_conflict,
                "unresolved_source_or_layout": unresolved,
            },
            "adjudication_status": status,
            "adjudicator_id": None,
            "adjudication_notes": None,
            "review_a_record_sha256": _digest(a),
            "review_b_record_sha256": _digest(b),
        })
    return queue


if __name__ == "__main__":
    rows = build()
    OUTPUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"records": len(rows), "pending": sum(row["adjudication_status"] != "adjudicated" for row in rows)}, ensure_ascii=False))

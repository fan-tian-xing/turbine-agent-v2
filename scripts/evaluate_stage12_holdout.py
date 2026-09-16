"""Independent Stage 12 holdout evaluator.

This process is intentionally separate from the development builder.  It reads
holdout Evidence and Gold only here, writes metrics only, and never changes the
extractor, cache context, development manifest or candidate artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

from turbine_kg.extraction.semantic import HeuristicSemanticExtractor, compare_candidates

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/stage12/stage12_holdout_evaluation.json"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _input(evidence: dict) -> dict:
    return {
        "evidence_id": evidence["evidence_id"], "document_logical_id": evidence["document_logical_id"], "revision_id": evidence["revision_id"],
        "physical_page": evidence["physical_page"], "logical_page": evidence.get("logical_page"), "source_span_id": evidence["source_span_id"],
        "source_text_sha256": evidence["source_text_sha256"], "source_text": evidence.get("effective_text") or evidence["source_text"],
        "support_type": evidence.get("support_type", "direct"), "document_key": evidence["document_key"],
    }


def evaluate() -> dict:
    evidence_rows = _rows(ROOT / "data/stage11/stage11_holdout_evidence.jsonl")
    gold = _rows(ROOT / "data/stage11/stage11_statement_holdout.jsonl")
    accepted_evidence = [row for row in evidence_rows if row.get("review_status") == "accepted" and row.get("evidence_status") == "accepted"]
    accepted_ids = {row["evidence_id"] for row in accepted_evidence}
    gold = [row for row in gold if row.get("review_status") == "accepted" and {item["evidence_id"] for item in row.get("evidence_bindings", [])} <= accepted_ids]
    extractor = HeuristicSemanticExtractor()
    candidates = []
    for evidence in accepted_evidence:
        candidate_rows = extractor.extract(_input(evidence))
        for row in candidate_rows:
            row["split"] = "acceptance_holdout"
        candidates.extend(candidate_rows)
    report = compare_candidates(candidates, gold)
    report.update({
        "schema_version": 1, "stage": "12", "artifact_kind": "stage12_holdout_evaluation", "status": "completed", "formal_release": False,
        "evaluation_entrypoint": "scripts/evaluate_stage12_holdout.py", "holdout_used_for_tuning": False, "result_written_to_development": False,
        "blind_read": False, "isolated_pages_excluded": sum(row.get("review_status") == "isolated" for row in evidence_rows),
        "accepted_evidence_count": len(accepted_evidence), "gold_artifact": "data/stage11/stage11_statement_holdout.jsonl", "evidence_artifact": "data/stage11/stage11_holdout_evidence.jsonl",
    })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    result = evaluate()
    print(json.dumps({"status": result["status"], "gold_statement_count": result["gold_statement_count"], "error_counts": result["error_counts"]}, ensure_ascii=False))

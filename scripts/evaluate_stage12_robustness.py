"""Execute frozen Development-only metamorphic checks for Stage 12."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from turbine_kg.extraction.semantic import HeuristicSemanticExtractor, validate_candidate_semantics

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data/stage12/stage12_robustness_cases.json"
OUT = ROOT / "data/stage12/stage12_robustness_evaluation.json"


def evaluate() -> dict:
    source = json.loads(CASES.read_text(encoding="utf-8"))
    results = []
    for case in source["cases"]:
        text = case["text"]
        evidence = {
            "evidence_id": "robustness-" + case["case_id"],
            "document_logical_id": "robustness-document",
            "revision_id": "robustness-revision",
            "physical_page": 1,
            "source_span_id": "robustness-span-" + case["case_id"],
            "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "source_text": text,
            "review_status": "accepted",
        }
        candidates = HeuristicSemanticExtractor(source_applicability_scope={}).extract(evidence)
        passed = len(candidates) == 1
        failure = None
        if passed:
            candidate = candidates[0]
            try:
                validate_candidate_semantics(candidate)
                passed = candidate["predicate"] == case["expected_predicate"] and candidate["relation_direction"] == case["expected_direction"]
                if case.get("expected_applicability_text"):
                    passed = passed and candidate["applicability_scope"].get("applicability_text") == case["expected_applicability_text"]
                if case.get("expected_condition"):
                    passed = passed and any(item["surface_form"] == case["expected_condition"] for item in candidate["conditions"])
                if case.get("expected_negation"):
                    passed = passed and any(item["surface_form"] == case["expected_negation"] for item in candidate["negation_scope"])
                if case.get("expected_operator"):
                    passed = passed and candidate["quantities"][0]["operator"] == case["expected_operator"]
                if case.get("expected_quantity"):
                    passed = passed and candidate["quantities"][0]["surface_form"] == case["expected_quantity"]
            except (ValueError, KeyError, IndexError) as error:
                passed, failure = False, str(error)
        else:
            failure = f"expected one candidate, got {len(candidates)}"
        results.append({"case_id": case["case_id"], "passed": passed, "failure": failure})
    return {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_development_robustness_evaluation",
        "status": "completed",
        "formal_release": False,
        "holdout_used_for_tuning": False,
        "blind_read": False,
        "cases_artifact": "data/stage12/stage12_robustness_cases.json",
        "case_count": len(results),
        "passed_count": sum(item["passed"] for item in results),
        "failed_count": sum(not item["passed"] for item in results),
        "results": results,
    }


if __name__ == "__main__":
    report = evaluate()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "passed": report["passed_count"], "failed": report["failed_count"]}, ensure_ascii=False))

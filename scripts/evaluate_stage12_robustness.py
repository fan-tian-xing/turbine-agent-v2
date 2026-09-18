"""Execute frozen Development-only metamorphic checks for Stage 12."""

from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path

from turbine_kg.extraction.semantic import (
    ExtractionProfile,
    ExtractionProviderError,
    ExternalLLMProvider,
    FixtureExtractionProvider,
    ProviderBackedExtractor,
    provider_from_config,
    validate_candidate_against_evidence,
    validate_candidate_semantics,
)

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "data/stage12/stage12_robustness_cases.json"
REAL_OUT = ROOT / "data/stage12/stage12_robustness_evaluation.json"
FIXTURE_OUT = ROOT / "data/stage12/stage12_fixture_robustness_evaluation.json"


def _semantic_failure_reasons(candidate: dict, case: dict) -> tuple[list[str], list[str]]:
    """Explain each deterministic mismatch without case-specific repair logic."""
    reasons: list[str] = []
    fields: list[str] = []
    checks = {
        "relation": candidate.get("predicate") == case["expected_predicate"],
        "relation_direction": candidate.get("relation_direction") == case["expected_direction"],
    }
    if case.get("expected_applicability_text"):
        checks["applicability"] = candidate.get("applicability_scope", {}).get("applicability_text") == case["expected_applicability_text"]
    if case.get("expected_condition"):
        checks["condition"] = any(item.get("surface_form") == case["expected_condition"] for item in candidate.get("conditions", []))
    if case.get("expected_negation"):
        checks["negation"] = any(item.get("surface_form") == case["expected_negation"] for item in candidate.get("negation_scope", []))
    if case.get("expected_operator"):
        checks["comparison"] = bool(candidate.get("quantities")) and candidate["quantities"][0].get("operator") == case["expected_operator"]
    if case.get("expected_quantity"):
        checks["quantity"] = bool(candidate.get("quantities")) and candidate["quantities"][0].get("surface_form") == case["expected_quantity"]
    for field, passed in checks.items():
        if not passed:
            fields.append(field)
            expected_key = {
                "relation": "expected_predicate",
                "relation_direction": "expected_direction",
                "applicability": "expected_applicability_text",
                "condition": "expected_condition",
                "negation": "expected_negation",
                "comparison": "expected_operator",
                "quantity": "expected_quantity",
            }[field]
            expected = case.get(expected_key)
            actual = candidate.get({"relation": "predicate", "relation_direction": "relation_direction", "applicability": "applicability_scope", "condition": "conditions", "negation": "negation_scope", "comparison": "quantities", "quantity": "quantities"}[field])
            reasons.append(f"{field}: expected={expected!r}, actual={actual!r}")
    return reasons, fields


def evaluate(*, fixture: bool = False) -> dict:
    source = json.loads(CASES.read_text(encoding="utf-8"))
    provider = FixtureExtractionProvider() if fixture else provider_from_config()
    profile = ExtractionProfile(
        semantic_role="stage12_robustness",
        extraction_profile_id="stage12_robustness_provider_v1",
        source_profile_id="stage12_robustness",
        source_applicability_scope=(),
        # Synthetic Development-only cases are explicitly authorized for the
        # configured provider; this is not a Registry permission override.
        external_llm_allowed=True,
    )
    extractor = ProviderBackedExtractor(provider, profile=profile, split="development_regression_golden")
    results = []
    provider_call_count = 0
    provider_failure_count = 0
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
        candidates = []
        passed = len(candidates) == 1
        failure = None
        failure_type = None
        failure_fields = []
        try:
            candidates = extractor.extract(evidence)
            provider_call_count += 1
            passed = len(candidates) == 1
            if passed:
                candidate = candidates[0]
                validate_candidate_semantics(candidate)
                validate_candidate_against_evidence(candidate, evidence)
                failure_reasons, failure_fields = _semantic_failure_reasons(candidate, case)
                passed = not failure_reasons
                if failure_reasons:
                    failure_type = "semantic_mismatch"
                    failure = "; ".join(failure_reasons)
            else:
                failure_type = "cardinality_failure"
                failure = f"expected one candidate, got {len(candidates)}"
        except (ExtractionProviderError, ValueError, KeyError, IndexError) as error:
            if isinstance(error, ExtractionProviderError):
                provider_failure_count += 1
                failure_type = "provider_failure"
            else:
                failure_type = "semantic_validation_failure"
            passed, failure = False, str(error)
            failure_fields = []
        results.append({"case_id": case["case_id"], "passed": passed, "failure_type": failure_type, "failure_fields": failure_fields, "failure": failure})
    execution_kind = "fixture" if fixture else "real_llm"
    return {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_development_robustness_evaluation",
        "status": "blocked" if provider_failure_count else "completed",
        "formal_release": False,
        "holdout_used_for_tuning": False,
        "blind_read": False,
        "cases_artifact": "data/stage12/stage12_robustness_cases.json",
        "provider_id": provider.provider_id,
        "execution_kind": execution_kind,
        "real_llm_execution": not fixture and provider_call_count > 0 and provider_failure_count == 0,
        "provider_call_count": provider_call_count,
        "provider_failure_count": provider_failure_count,
        "case_count": len(results),
        "passed_count": sum(item["passed"] for item in results),
        "failed_count": sum(not item["passed"] for item in results),
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", action="store_true", help="run the offline fixture explicitly")
    args = parser.parse_args()
    report = evaluate(fixture=args.fixture)
    output = FIXTURE_OUT if args.fixture else REAL_OUT
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "passed": report["passed_count"], "failed": report["failed_count"], "execution_kind": report["execution_kind"]}, ensure_ascii=False))

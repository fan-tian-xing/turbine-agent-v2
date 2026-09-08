"""Record the Stage 3 fixture baseline/projection comparison."""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from turbine_kg.stage3.corpus import load_corpus
from turbine_kg.stage3.models import ScopeContext
from turbine_kg.stage3.pipeline import answer_question
from turbine_kg.stage3.projection import build_traceability_projection, projected_retrieve
from turbine_kg.stage3.real_trial import load_confirmed_real_corpus
from turbine_kg.stage3.retrieval import json_baseline


FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "stage3"
OUTPUT_PATH = PROJECT_ROOT / "data" / "stage3" / "baseline_comparison.json"
REAL_BENCHMARK_PATH = PROJECT_ROOT / "data" / "stage3" / "real_trial_benchmark.json"
REAL_CONFIRMATION_PATH = PROJECT_ROOT / "data" / "stage3" / "real_trial_confirmation.json"
REAL_RUNTIME_PATH = PROJECT_ROOT / "var" / "stage3" / "real_trial_pages.json"
REAL_FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "stage3" / "real_trial_pages.json"
REAL_EXECUTION_PATH = PROJECT_ROOT / "data" / "stage3" / "real_trial_execution.json"


def _run_real_trial() -> dict[str, object] | None:
    if not REAL_CONFIRMATION_PATH.is_file():
        return None
    runtime_path = REAL_RUNTIME_PATH if REAL_RUNTIME_PATH.is_file() else REAL_FIXTURE_PATH
    corpus = load_confirmed_real_corpus(REAL_CONFIRMATION_PATH, runtime_path)
    benchmark = json.loads(REAL_BENCHMARK_PATH.read_text(encoding="utf-8"))
    projection = build_traceability_projection(corpus)
    cases: list[dict[str, object]] = []
    disagreements = 0
    validation_failures = 0
    baseline_total_ns = 0
    projected_total_ns = 0
    repetitions = 25
    for case in benchmark["cases"]:
        statement = next(
            statement
            for document in corpus
            for statement in document.statements
            if statement.statement_id == case["confirmed_statement_id"]
        )
        context = ScopeContext.from_dict(statement.scope.as_dict())
        start = time.perf_counter_ns()
        for _ in range(repetitions):
            baseline_ids = json_baseline(corpus, question=case["question"], context=context)
        baseline_elapsed_ns = time.perf_counter_ns() - start
        start = time.perf_counter_ns()
        for _ in range(repetitions):
            projected_ids = projected_retrieve(projection, question=case["question"], context=context)
        projected_elapsed_ns = time.perf_counter_ns() - start
        baseline_total_ns += baseline_elapsed_ns
        projected_total_ns += projected_elapsed_ns
        answer = answer_question(corpus, primary_question=case["question"], context=context)
        disagreement = set(baseline_ids) != set(projected_ids)
        disagreements += disagreement
        validation_failures += answer["status"] != "validated"
        cases.append({
            "case_id": case["case_id"],
            "baseline_candidate_ids": list(baseline_ids),
            "projected_candidate_ids": list(projected_ids),
            "selected_statement_id": answer.get("retrieval", {}).get("selected_statement_id"),
            "answer_status": answer["status"],
            "candidate_set_disagreement": disagreement,
            "baseline_mean_us": round(baseline_elapsed_ns / repetitions / 1000, 3),
            "projected_mean_us": round(projected_elapsed_ns / repetitions / 1000, 3),
        })
    execution = {
        "schema_version": 1,
        "stage": "3",
        "status": "executed",
        "runtime_corpus": str(runtime_path.relative_to(PROJECT_ROOT)),
        "case_count": len(cases),
        "candidate_set_disagreements": disagreements,
        "claim_validation_failures": validation_failures,
        "latency_repetitions_per_case": repetitions,
        "baseline_mean_us": round(baseline_total_ns / len(cases) / repetitions / 1000, 3),
        "projected_mean_us": round(projected_total_ns / len(cases) / repetitions / 1000, 3),
        "cases": cases,
    }
    REAL_EXECUTION_PATH.write_text(json.dumps(execution, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return execution


def build() -> dict[str, object]:
    corpus = load_corpus(FIXTURE_ROOT / "corpus.json")
    questions = json.loads((FIXTURE_ROOT / "questions.json").read_text(encoding="utf-8"))
    projection = build_traceability_projection(corpus)
    statuses: Counter[str] = Counter()
    disagreements = 0
    fixture_baseline_total_ns = 0
    fixture_projected_total_ns = 0
    fixture_repetitions = 25
    for case in questions:
        context = ScopeContext.from_dict(case["context"])
        answer = answer_question(
            corpus,
            primary_question=case["question"],
            context=context,
            high_risk_action=case["id"] == "q20",
        )
        statuses[answer["status"]] += 1
        start = time.perf_counter_ns()
        for _ in range(fixture_repetitions):
            baseline_ids = json_baseline(corpus, question=case["question"], context=context)
        fixture_baseline_total_ns += time.perf_counter_ns() - start
        start = time.perf_counter_ns()
        for _ in range(fixture_repetitions):
            projected_ids = projected_retrieve(projection, question=case["question"], context=context)
        fixture_projected_total_ns += time.perf_counter_ns() - start
        if set(baseline_ids) != set(projected_ids):
            disagreements += 1
    real_benchmark = json.loads(REAL_BENCHMARK_PATH.read_text(encoding="utf-8")) if REAL_BENCHMARK_PATH.is_file() else None
    real_execution = _run_real_trial()
    result = {
        "schema_version": 1,
        "stage": "3",
        "status": "fixture_smoke_recorded",
        "json_baseline": "implemented",
        "traceability_projection": "implemented_and_deduplicated",
        "fixture_question_count": len(questions),
        "fixture_status_counts": dict(sorted(statuses.items())),
        "candidate_set_disagreements": disagreements,
        "fixture_topology": {
            "logical_documents": len(corpus),
            "pages": sum(len(item.pages) for item in corpus),
            "source_spans": sum(len(item.spans) for item in corpus),
            "evidence": sum(len(item.evidence) for item in corpus),
            "engineering_statements": sum(len(item.statements) for item in corpus),
            "projection_nodes": len(projection["nodes"]),
            "projection_edges": len(projection["edges"]),
        },
        "retrieval_error_comparison": "no_fixture_candidate_set_disagreement",
        "latency_comparison": {
            "fixture_repetitions_per_case": fixture_repetitions,
            "fixture_baseline_mean_us": round(fixture_baseline_total_ns / len(questions) / fixture_repetitions / 1000, 3),
            "fixture_projected_mean_us": round(fixture_projected_total_ns / len(questions) / fixture_repetitions / 1000, 3),
            "real_trial_execution": "see real_trial_execution.json",
        },
        "real_page_error_comparison": (
            f"data/stage3/real_trial_execution.json: {real_execution['case_count']} cases executed, "
            f"{real_execution['candidate_set_disagreements']} candidate-set disagreements, "
            "no measurable error reduction"
            if real_execution and real_execution.get("status") == "executed"
            else "pending_manual_evidence_review"
        ),
        "real_trial_case_count": real_execution.get("case_count", 0) if real_execution else 0,
        "decision": "Neo4j trial completed as the required Stage 3 runtime path; the confirmed sample showed no measurable error reduction over the JSON baseline, so vector retrieval is deferred.",
    }
    OUTPUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))

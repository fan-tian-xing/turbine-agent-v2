import json
from pathlib import Path

from turbine_kg.stage3.models import ScopeContext
from turbine_kg.stage3.pipeline import answer_question
from turbine_kg.stage3.projection import build_traceability_projection, projected_retrieve
from turbine_kg.stage3.retrieval import json_baseline
from turbine_kg.stage3.real_trial import load_confirmed_real_corpus


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIRMATION = PROJECT_ROOT / "data" / "stage3" / "real_trial_confirmation.json"
RUNTIME_CORPUS = PROJECT_ROOT / "var" / "stage3" / "real_trial_pages.json"
if not RUNTIME_CORPUS.is_file():
    RUNTIME_CORPUS = PROJECT_ROOT / "tests" / "fixtures" / "stage3" / "real_trial_pages.json"
BENCHMARK = PROJECT_ROOT / "data" / "stage3" / "real_trial_benchmark.json"


def test_confirmed_real_pages_run_through_retrieval_and_claim_validation():
    corpus = load_confirmed_real_corpus(CONFIRMATION, RUNTIME_CORPUS)
    cases = json.loads(BENCHMARK.read_text(encoding="utf-8"))["cases"]
    for case in cases:
        statement = next(
            statement
            for document in corpus
            for statement in document.statements
            if statement.statement_id == case["confirmed_statement_id"]
        )
        result = answer_question(
            corpus,
            primary_question=case["question"],
            context=ScopeContext.from_dict(statement.scope.as_dict()),
        )
        assert result["status"] == "validated", case["case_id"]
        assert result["retrieval"]["selected_statement_id"] == case["confirmed_statement_id"]
        assert result["claims"][0]["evidence_ids"]
        if case["case_id"] == "real-case-d300n-p73-clearance":
            assert len(result["claims"][0]["quantities"]) == 2
        baseline_ids = json_baseline(corpus, question=case["question"], context=ScopeContext.from_dict(statement.scope.as_dict()))
        projected_ids = projected_retrieve(
            build_traceability_projection(corpus),
            question=case["question"],
            context=ScopeContext.from_dict(statement.scope.as_dict()),
        )
        assert set(baseline_ids) == set(projected_ids)


def test_confirmed_real_pages_have_a_deduplicated_traceability_projection():
    corpus = load_confirmed_real_corpus(CONFIRMATION, RUNTIME_CORPUS)
    projection = build_traceability_projection(corpus)
    node_ids = {node["id"] for node in projection["nodes"]}
    edge_keys = {(edge["from"], edge["to"], edge["type"]) for edge in projection["edges"]}
    assert len(node_ids) == len(projection["nodes"])
    for document in corpus:
        for statement in document.statements:
            assert statement.statement_id in node_ids
            assert any(
                edge[1] == statement.statement_id and edge[2] == "SUPPORTS"
                for edge in edge_keys
            )


def test_confirmed_real_sources_are_bound_to_registry_applicability():
    corpus = load_confirmed_real_corpus(CONFIRMATION, RUNTIME_CORPUS)
    assets = {
        item["asset_id"]: item
        for item in (
            json.loads(line)
            for line in (PROJECT_ROOT / "data" / "registry" / "source_assets.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    for document in corpus:
        assert document.asset.registry_applicability_scope
        assert tuple(assets[document.asset.asset_id]["applicability_scope"]) == document.asset.registry_applicability_scope

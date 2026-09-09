import json
from pathlib import Path

import pytest

from turbine_kg.stage3.applicability import match_scope, statement_scope_within_source
from turbine_kg.stage3.corpus import load_corpus
from turbine_kg.stage3.models import ApplicabilityScope, ScopeContext
from turbine_kg.stage3.projection import build_traceability_projection
from turbine_kg.stage3.real_trial import load_confirmed_real_corpus
from turbine_kg.stage3.trial_cli import build_parser


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "stage3"


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(FIXTURE_ROOT / "corpus.json")


def test_corpus_has_three_roles_and_traceability_chain(corpus):
    assert {item.source_role for item in corpus} == {
        "manufacturer_manual",
        "standard_or_regulation",
        "training_background",
    }
    projection = build_traceability_projection(corpus)
    node_types = {node["type"] for node in projection["nodes"]}
    assert {
        "Asset",
        "LogicalDocument",
        "Revision",
        "Page",
        "SourceSpan",
        "Evidence",
        "EngineeringStatement",
        "Equipment",
        "Process",
        "ApplicabilityScope",
        "Component",
        "Procedure",
        "Step",
        "QuantityValue",
    } <= node_types
    assert any(edge["type"] == "SUPPORTS" for edge in projection["edges"])
    assert {edge["type"] for edge in projection["edges"]} >= {
        "ABOUT_COMPONENT", "DEFINES_PROCEDURE", "HAS_STEP", "HAS_QUANTITY",
    }
    assert any(span.content_kind == "table" for document in corpus for span in document.spans)
    assert any(len(item.source_span_ids) > 1 for document in corpus for item in document.evidence)


def test_scope_matching_covers_positive_negative_and_missing_context(corpus):
    statement = corpus[0].statements[0]
    positive = ScopeContext.from_dict({**statement.scope.as_dict(), "capacity_range": 300})
    assert match_scope(statement.scope, positive).matched
    wrong_model = ScopeContext.from_dict({**positive.as_scope().as_dict(), "model": "N-500"})
    assert not match_scope(statement.scope, wrong_model).matched
    missing_condition = ScopeContext.from_dict({key: value for key, value in positive.as_scope().as_dict().items() if key != "condition"})
    result = match_scope(statement.scope, missing_condition)
    assert "missing_context:condition" in result.reasons
    partial_capacity = ScopeContext.from_dict({**positive.as_scope().as_dict(), "capacity_range": {"min": 300, "max": 360}})
    assert not match_scope(statement.scope, partial_capacity).matched
    assert "mismatch:capacity_range_max" in match_scope(statement.scope, partial_capacity).reasons


def test_statement_cannot_broaden_source_scope():
    source = ApplicabilityScope.from_dict({"model": "N-300", "equipment": "steam_turbine"})
    broader = ApplicabilityScope.from_dict({"equipment": "steam_turbine"})
    valid, errors = statement_scope_within_source(source, broader)
    assert not valid
    assert "statement_scope_broadens_source:model" in errors


def test_confirmed_source_cannot_broaden_its_registry_scope(tmp_path):
    confirmation = json.loads((PROJECT_ROOT / "data" / "stage3" / "real_trial_confirmation.json").read_text(encoding="utf-8"))
    d300n = next(group for group in confirmation["confirmed_groups"] if group["group_id"] == "confirmation-d300n-p73")
    d300n["source_scope"].pop("model")
    path = tmp_path / "broadened-confirmation.json"
    path.write_text(json.dumps(confirmation), encoding="utf-8")
    runtime = PROJECT_ROOT / "var" / "stage3" / "real_trial_pages.json"
    with pytest.raises(ValueError, match="broadens Registry scope"):
        load_confirmed_real_corpus(path, runtime)


def test_fixture_is_explicitly_non_production():
    raw = json.loads((FIXTURE_ROOT / "corpus.json").read_text(encoding="utf-8"))
    assert raw["corpus_kind"] == "public_synthetic_fixture"
    assert all(item["asset"]["source_root_id"] == "fixture" for item in raw["documents"])


def test_initial_batch_is_frozen_and_real_trial_summary_is_consistent():
    manifest = json.loads((PROJECT_ROOT / "data" / "stage3" / "initial_batch_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "frozen"
    assert manifest["formal_release"] is False
    assert manifest["page_sample_count"] == 15
    assert len(manifest["source_documents"]) == 5
    assert all(item.get("asset_id") and item.get("revision_id") and item.get("sha256") for item in manifest["source_documents"])
    assert manifest["registry_snapshot"]["sha256"] == "140fb6412a1a639fdae30ffde9e49475f1b0210ec1e58b7d7696ceaaf525d925"
    summary_path = PROJECT_ROOT / "data" / "stage3" / "real_trial_summary.json"
    if summary_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["page_sample_count"] == manifest["page_sample_count"]
        assert summary["evidence_review"]["status"] == "confirmed_by_user"
        confirmation = json.loads((PROJECT_ROOT / "data" / "stage3" / "real_trial_confirmation.json").read_text(encoding="utf-8"))
        assert confirmation["status"] == "confirmed_by_user"
        assert confirmation["confirmation_group_count"] == 3


def test_stage3_exit_audit_records_closed_strict_closure():
    audit = json.loads((PROJECT_ROOT / "data" / "stage3" / "stage3_exit_audit.json").read_text(encoding="utf-8"))
    assert audit["status"] == "complete"
    assert audit["closure_status"] == "closed"
    assert audit["formal_release"] is False
    required_checks = {
        key for key, item in audit["checks"].items()
        if key not in {"baseline_projection_comparison"}
    }
    assert all(audit["checks"][key]["status"] in {"pass", "observed"} for key in required_checks)
    assert audit["checks"]["neo4j_import_and_chinese_retrieval"]["status"] == "pass"
    assert audit["checks"]["llm_answer_trial"]["status"] == "pass"
    trial = json.loads((PROJECT_ROOT / "data" / "stage3" / "neo4j_trial_execution.json").read_text(encoding="utf-8"))
    assert trial["status"] == "completed"
    assert trial["idempotent_import_verified"] is True
    assert trial["llm"]["prior_live_answer_received"] is True
    assert trial["llm"]["live_replay_after_contract_hardening"] == "passed_recorded_replay_with_safe_rejection_case"
    assert trial["llm"]["strict_replay_status"] == "passed_recorded_replay"
    replay = json.loads((PROJECT_ROOT / "data" / "stage3" / "llm_live_replay_2026-09-08.json").read_text(encoding="utf-8"))
    assert replay["status"] == "passed"
    assert replay["model"] == "gpt-5.6-luna"
    assert replay["llm_ok"] is True
    assert replay["claim_validation"] == "passed"
    assert replay["fallback_used"] is False
    assert trial["database_counts"]["domain_node_type_count"] == 14
    assert trial["database_counts"]["technical_batch_node_type"] == "Stage3Batch"
    metrics = json.loads((PROJECT_ROOT / "data" / "stage3" / "review_metrics.json").read_text(encoding="utf-8"))
    assert metrics["status"] == "review_recorded"
    assert metrics["confirmed_evidence_groups"] == 3
    execution = json.loads((PROJECT_ROOT / "data" / "stage3" / "real_trial_execution.json").read_text(encoding="utf-8"))
    assert execution["status"] == "executed"
    assert execution["case_count"] == 3
    assert execution["claim_validation_failures"] == 0
    assert audit["checks"]["dead_code_orphan_output_review"]["status"] == "pass"
    assert audit["closure_items"] == {
        "benchmark_decision_alignment": "closed_with_current_topology",
        "dead_code_orphan_output_review": "closed",
        "registry_structured_scope_chain": "closed",
        "claim_composed_answer_boundary": "closed",
        "live_llm_replay_evidence": "closed",
    }


def test_stage3_closure_records_are_aligned_and_have_no_orphan_findings():
    benchmark = json.loads((PROJECT_ROOT / "data" / "stage3" / "real_trial_benchmark.json").read_text(encoding="utf-8"))
    comparison = json.loads((PROJECT_ROOT / "data" / "stage3" / "baseline_comparison.json").read_text(encoding="utf-8"))
    audit = json.loads((PROJECT_ROOT / "data" / "stage3" / "stage3_dead_code_orphan_audit.json").read_text(encoding="utf-8"))
    assert "Neo4j研发试点已完成" in benchmark["decision"]
    assert "暂不引入Neo4j" not in benchmark["decision"]
    assert "Neo4j trial completed" in comparison["decision"]
    assert audit["status"] == "pass"
    assert audit["findings"]["dead_code"] == []
    assert audit["findings"]["orphan_outputs"] == []


def test_initial_batch_assets_match_the_registry_snapshot():
    manifest = json.loads((PROJECT_ROOT / "data" / "stage3" / "initial_batch_manifest.json").read_text(encoding="utf-8"))
    assets = {
        item["asset_id"]: item
        for item in (
            json.loads(line)
            for line in (PROJECT_ROOT / "data" / "registry" / "source_assets.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    for selected in manifest["source_documents"]:
        asset = assets[selected["asset_id"]]
        assert {asset[field] for field in ("document_logical_id", "revision_id", "sha256", "relative_path")} == {
            selected[field] for field in ("document_logical_id", "revision_id", "sha256", "relative_path")
        }


def test_cli_does_not_enable_evidence_send_by_default():
    args = build_parser().parse_args(["status"])
    assert args.allow_evidence_send is False


def test_cli_accepts_old_direct_question_form():
    args = build_parser().parse_args(["密封瓦座水平结合面用塞尺检查要求是什么？"])
    assert args.command_or_question.startswith("密封瓦座")


def test_fixture_loader_rejects_unknown_fields_empty_links_and_invalid_enums(tmp_path):
    source = json.loads((FIXTURE_ROOT / "corpus.json").read_text(encoding="utf-8"))
    variants = []
    extra = json.loads(json.dumps(source))
    extra["documents"][0]["unexpected"] = True
    variants.append(extra)
    empty_links = json.loads(json.dumps(source))
    empty_links["documents"][0]["evidence"][0]["source_span_ids"] = []
    variants.append(empty_links)
    invalid_type = json.loads(json.dumps(source))
    invalid_type["documents"][0]["statements"][0]["statement_type"] = "unknown"
    variants.append(invalid_type)
    for index, variant in enumerate(variants):
        path = tmp_path / f"invalid-{index}.json"
        path.write_text(json.dumps(variant), encoding="utf-8")
        with pytest.raises(ValueError):
            load_corpus(path)

"""Pre-freeze Reserve checks. No test sends Reserve Evidence to a provider."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import build_stage12_reserve_gold as gold_builder
from scripts import build_stage12_reserve_evidence as evidence_builder
from scripts import stage12_reserve_pipeline as reserve_pipeline
from scripts.audit_stage12_exit import _reserve_acceptance_gate, _reserve_acceptance_lineage
from scripts.build_stage12_reserve_freeze_manifest import ASSETS, build_manifest, verify_manifest
from turbine_kg.extraction.semantic import HeuristicSemanticExtractor, ProfileRouter
from turbine_kg.extraction.semantic import _resolved_marked_answer_text


def test_gold_builder_requires_reviewed_source_regions(monkeypatch, tmp_path):
    original = json.loads(gold_builder.REVIEWS_PATH.read_text(encoding="utf-8"))
    reviews = copy.deepcopy(original)
    reviews["regions"].pop()
    path = tmp_path / "incomplete_reviews.json"
    path.write_text(json.dumps(reviews, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(gold_builder, "REVIEWS_PATH", path)
    with pytest.raises(ValueError, match="exactly once"):
        gold_builder.build()


def test_gold_builder_refuses_after_reserve_execution(monkeypatch, tmp_path):
    marker = tmp_path / "reserve_execution_marker.json"
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(gold_builder, "EXECUTION_PATHS", (marker,))
    with pytest.raises(PermissionError, match="after Reserve execution"):
        gold_builder.build()


def test_reserve_gold_rejects_distractor_quantity(monkeypatch, tmp_path):
    reviews = json.loads(gold_builder.REVIEWS_PATH.read_text(encoding="utf-8"))
    question = next(region for region in reviews["regions"] if region["statements"][0].get("answer_injection"))
    question["statements"][0]["quantity"]["value"] = 5
    path = tmp_path / "wrong_answer_quantity.json"
    path.write_text(json.dumps(reviews, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(gold_builder, "REVIEWS_PATH", path)
    with pytest.raises(ValueError, match="quantity_source"):
        gold_builder.build()


def _synthetic_evidence() -> dict:
    text = "合成测试设备间隙不得大于4mm。"
    return {
        "split": "synthetic",
        "evidence_id": "synthetic-reserve-runner-evidence",
        "document_logical_id": "synthetic-reserve-runner-document",
        "revision_id": "synthetic-revision",
        "physical_page": 1,
        "logical_page": None,
        "source_span_id": "synthetic-span",
        "source_span_ids": ["synthetic-span"],
        "evidence_version_id": "synthetic-version",
        "source_text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "source_text": text,
        "document_key": "synthetic",
        "review_status": "accepted",
    }


def test_real_reserve_runner_refuses_before_freeze_without_provider_call():
    class NeverCall:
        metadata = {"mode": "real_llm"}

        def extract(self, *_args, **_kwargs):
            raise AssertionError("provider must not be called")

    with pytest.raises(PermissionError):
        reserve_pipeline.execute_reserve(provider=NeverCall(), write_artifacts=True)
    assert not reserve_pipeline.RUN_MARKER.exists()
    assert not reserve_pipeline.RESERVE_CANDIDATE_PATH.exists()


def test_reserve_route_failure_does_not_consume_one_shot(monkeypatch, tmp_path):
    class NeverCall:
        metadata = {"mode": "real_llm"}
        model_config_identifier = "synthetic-model"

        def extract(self, *_args, **_kwargs):
            raise AssertionError("provider must not be called")

    class BrokenRouter:
        def extractor_for(self, *_args, **_kwargs):
            raise ValueError("invalid route")

    provider = NeverCall()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"provider_runtime_fingerprint": reserve_pipeline.provider_runtime_fingerprint(provider)}), encoding="utf-8")
    evidence_path = tmp_path / "evidence.jsonl"
    evidence_path.write_text(json.dumps({"evidence_id": "synthetic"}) + "\n", encoding="utf-8")
    gold_path = tmp_path / "gold.jsonl"
    gold_path.write_text("", encoding="utf-8")
    marker_path = tmp_path / "marker.json"
    monkeypatch.setattr(reserve_pipeline, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(reserve_pipeline, "EVIDENCE_PATH", evidence_path)
    monkeypatch.setattr(reserve_pipeline, "GOLD_PATH", gold_path)
    monkeypatch.setattr(reserve_pipeline, "RUN_MARKER", marker_path)
    monkeypatch.setattr(reserve_pipeline, "build_execution_plan", lambda: {"execution_allowed": True, "evidence_ids": ["synthetic"]})
    with pytest.raises(ValueError, match="invalid route"):
        reserve_pipeline.execute_reserve(provider=provider, router=BrokenRouter(), write_artifacts=True)
    assert not marker_path.exists()


def test_reserve_registry_requires_unique_matching_pages_and_sample_ids():
    registry = json.loads(reserve_pipeline.REGISTRY_PATH.read_text(encoding="utf-8"))
    evidence = reserve_pipeline._jsonl(reserve_pipeline.EVIDENCE_PATH)
    assert reserve_pipeline.validate_reserve_registry(registry, evidence) == []
    wrong_sample = copy.deepcopy(evidence)
    wrong_sample[0]["sample_id"] = "another-sample"
    assert any("evidence_lineage" in issue for issue in reserve_pipeline.validate_reserve_registry(registry, wrong_sample))
    duplicate_page = copy.deepcopy(evidence)
    duplicate_page[-1]["document_logical_id"] = duplicate_page[0]["document_logical_id"]
    duplicate_page[-1]["revision_id"] = duplicate_page[0]["revision_id"]
    duplicate_page[-1]["physical_page"] = duplicate_page[0]["physical_page"]
    assert "reserve_page_identity_set" in reserve_pipeline.validate_reserve_registry(registry, duplicate_page)


def test_reviewed_single_evidence_reserve_question_excludes_distractors():
    evidence = next(row for row in reserve_pipeline._jsonl(reserve_pipeline.EVIDENCE_PATH) if row.get("related_source_context"))
    group = evidence["related_source_context"][0]
    assert group["group_kind"] == "question_options"
    assert group["source_review_status"] == "confirmed"
    support = _resolved_marked_answer_text({"source_question": {"group_id": group["group_id"], "status": "linked"}}, evidence)
    assert support is not None
    assert "3圈" in support
    assert "(B) 5" not in support
    assert "(C) 6" not in support


def test_missing_reviewed_question_answer_is_critical_omission():
    evidence = next(row for row in reserve_pipeline._jsonl(reserve_pipeline.EVIDENCE_PATH) if row.get("related_source_context"))
    gold = next(row for row in reserve_pipeline._jsonl(reserve_pipeline.GOLD_PATH) if any(binding.get("evidence_id") == evidence["evidence_id"] for binding in row.get("evidence_bindings", [])))
    report = reserve_pipeline.compare_candidates([], [gold], evidence_by_id={evidence["evidence_id"]: evidence})
    assert report["safety_metrics"]["critical_omission_count"] == 1


def test_evidence_builder_refuses_rebuild_after_execution_marker(monkeypatch, tmp_path):
    marker = tmp_path / "reserve_execution_marker.json"
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(evidence_builder, "EXECUTION_ARTIFACTS", (marker,))
    with pytest.raises(PermissionError):
        evidence_builder.assert_rebuild_safe()


def test_synthetic_fixture_exercises_runner_evaluator_without_reserve_artifacts():
    evidence = _synthetic_evidence()
    gold = {**HeuristicSemanticExtractor(split="synthetic").extract(evidence)[0], "statement_id": "synthetic-gold"}

    class FixtureProvider:
        provider_id = "synthetic-fixture"
        metadata = {"mode": "fixture", "prompt_version": "synthetic"}

    class Router:
        def extractor_for(self, _evidence, *, split, provider):
            assert split == reserve_pipeline.RESERVE_SPLIT
            assert provider.metadata["mode"] == "fixture"
            return HeuristicSemanticExtractor(split=split, profile_id="synthetic-fixture")

    report = reserve_pipeline.execute_reserve(
        provider=FixtureProvider(), synthetic=True,
        evidence_rows=[evidence], gold_rows=[gold], router=Router(),
    )
    assert report["candidate_count"] >= 1
    assert report["evidence_binding"]["accuracy"] == 1.0
    assert report["synthetic"] is True
    assert not reserve_pipeline.RUN_MARKER.exists()
    assert not reserve_pipeline.RESERVE_CANDIDATE_PATH.exists()


def test_prepared_manifest_hashes_assets_and_is_not_approval():
    manifest = build_manifest()
    checked = verify_manifest(manifest)
    assert checked["valid"] is True
    assert checked["human_approval_present"] is False
    assert "reserve_gold" in manifest["assets"]
    assert "acceptance_gate" in manifest["assets"]
    assert {"profile_routing", "source_asset_registry"} <= set(ASSETS)
    tampered = copy.deepcopy(manifest)
    tampered["assets"]["reserve_gold"]["sha256"] = "0" * 64
    assert verify_manifest(tampered)["valid"] is False
    premature = copy.deepcopy(manifest)
    premature["freeze_status"] = "human_approved"
    premature["approval_token"] = "synthetic-approval"
    assert verify_manifest(premature)["human_approval_present"] is False


def test_acceptance_gate_requires_every_safety_measure_and_real_lineage():
    policy = json.loads((gold_builder.ROOT / "config/stage12_statement_contract.json").read_text(encoding="utf-8"))["evaluation"]["acceptance_quality_gate"]
    safety = {name: threshold for name, threshold in policy["hard_safety"].items()}
    report = {
        "status": "completed",
        "eligible_for_final_acceptance": True,
        "safety_metrics": safety,
        "adjudicated_information_coverage": 1.0,
        "adjudicated_disagreement_summary": {
            "confirmed_critical_model_error_count": 0,
            "confirmed_noncritical_model_error_rate": 0.0,
            "pending_review_count": 0,
        },
    }
    assert _reserve_acceptance_gate(True, report, policy) is True
    report["safety_metrics"].pop("critical_omission_count")
    assert _reserve_acceptance_gate(True, report, policy) is False
    assert _reserve_acceptance_lineage(report) is False

import hashlib
import json
from pathlib import Path

from turbine_kg.extraction.semantic import (
    HeuristicSemanticExtractor,
    _candidate_schema_split,
    _modality,
    _quantity_fields,
    compare_candidates,
    to_stage9_runtime_payload,
    validate_candidate_against_evidence,
    validate_candidate_evidence_binding,
    validate_candidate_payload,
)


ROOT = Path(__file__).resolve().parents[2]
def _synthetic_evidence() -> dict:
    source = "合成测试设备间隙不得大于4mm。"
    return {
        "split": "synthetic",
        "evidence_id": "synthetic-reserve-path-evidence",
        "document_logical_id": "synthetic-reserve-path-document",
        "revision_id": "synthetic-revision",
        "physical_page": 1,
        "logical_page": None,
        "source_span_id": "synthetic-span",
        "source_span_ids": ["synthetic-span"],
        "evidence_version_id": "synthetic-version",
        "source_text_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "source_text": source,
        "document_key": "synthetic",
        "review_status": "accepted",
    }


def test_quantity_parser_supports_omega_variants_without_ocr_q_guessing():
    for source_unit in ("Ω", "Ω"):
        quantities, value, unit = _quantity_fields(f"接地电阻不得大于4{source_unit}")
        assert value == 4
        assert unit == "Ω"
        assert quantities[0]["unit"] == "Ω"
        assert quantities[0]["operator"] == "lte"
    assert _quantity_fields("接地电阻不得大于4Q")[0] == []


def test_quantity_parser_captures_comparator_ratios_and_training_hours():
    assert _quantity_fields("培训不少于500标准学时")[0] == [
        {"surface_form": "500标准学时", "value": 500, "unit": "标准学时", "operator": "gte"}
    ]
    assert _quantity_fields("顶升量不得超过螺杆牙距的3/4")[0] == [
        {"surface_form": "3/4", "value": 0.75, "unit": "1", "operator": "lte"}
    ]
    assert _quantity_fields("安全系数≥6")[0] == [
        {"surface_form": "6", "value": 6, "unit": "1", "operator": "gte"}
    ]
    assert _quantity_fields("角度不小于60°")[0][0]["unit"] == "°"
    assert _quantity_fields("接地电阻不得大于4Q")[0] == []


def test_quantity_parser_preserves_range_comparator_and_long_units():
    quantity = _quantity_fields("齿尖厚度宜为0.10mm～0.20mm")[0]
    assert quantity == [{
        "surface_form": "0.10mm～0.20mm",
        "min": 0.10,
        "max": 0.20,
        "unit": "mm",
        "operator": "range",
    }]
    assert _quantity_fields("润滑油压低于0.05MPa")[0][0]["unit"] == "MPa"
    assert _quantity_fields("中分面间隙不得大于0.10mm")[0][0]["operator"] == "lte"


def test_recommended_modality_is_preserved_by_stage12_semantics():
    assert _modality("齿尖厚度宜为0.10mm～0.20mm") == "recommended"


def test_common_normative_modalities_are_derived_without_document_specific_rules():
    assert _modality("设备可以停运检查") == "permitted"
    assert _modality("设备需停运检查") == "must"
    assert _modality("严禁带压拆卸") == "shall"
    assert _modality("该现象可能导致振动") == "descriptive"
    assert _modality("过大的间隙可导致振动") == "descriptive"
    assert _modality("泄漏可引起油压下降") == "descriptive"
    assert _modality("调整可减少振动") == "descriptive"
    assert _modality("该措施可提高效率") == "descriptive"


def test_reserve_split_maps_to_candidate_schema_and_runs_offline_pipeline():
    assert _candidate_schema_split("acceptance_holdout_reserve") == "acceptance_holdout"
    evidence = _synthetic_evidence()
    candidates = HeuristicSemanticExtractor(
        split="acceptance_holdout_reserve",
        profile_id="reserve_prefreeze_fixture",
    ).extract(evidence)
    assert candidates
    assert {row["split"] for row in candidates} == {"acceptance_holdout"}

    payload = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "engineering_statement_candidates",
        "status": "candidate_only",
        "formal_release": False,
        "producer": "synthetic_reserve_prefreeze_test",
        "inputs": {},
        "extraction_profile": "reserve_prefreeze_fixture",
        "provider_id": "fixture",
        "prompt_version": "offline",
        "provider_metadata": {},
        "candidates": candidates,
        "review_diagnostics": [],
    }
    validate_candidate_payload(payload)
    for row in candidates:
        validate_candidate_evidence_binding(row, evidence)
        validate_candidate_against_evidence(row, evidence)
    to_stage9_runtime_payload(candidates)

    gold = {**candidates[0], "statement_id": "synthetic-gold-1"}
    report = compare_candidates(candidates, [gold], evidence_by_id={evidence["evidence_id"]: evidence})
    assert report["gold_statement_count"] == 1
    assert "evidence_binding" in report

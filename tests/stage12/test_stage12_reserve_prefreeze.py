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
RESERVE_EVIDENCE = ROOT / "data/stage12/stage12_reserve_evidence.jsonl"
RESERVE_GOLD = ROOT / "data/stage12/stage12_reserve_gold_v3_draft.jsonl"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_quantity_parser_supports_omega_variants_without_ocr_q_guessing():
    for source_unit in ("Ω", "Ω"):
        quantities, value, unit = _quantity_fields(f"接地电阻不得大于4{source_unit}")
        assert value == 4
        assert unit == "Ω"
        assert quantities[0]["unit"] == "Ω"
        assert quantities[0]["operator"] == "lte"
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


def test_reserve_split_maps_to_candidate_schema_and_runs_offline_pipeline():
    assert _candidate_schema_split("acceptance_holdout_reserve") == "acceptance_holdout"
    evidence = _jsonl(RESERVE_EVIDENCE)[0]
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
    }
    validate_candidate_payload(payload)
    for row in candidates:
        validate_candidate_evidence_binding(row, evidence)
        validate_candidate_against_evidence(row, evidence)
    to_stage9_runtime_payload(candidates)

    gold = next(row for row in _jsonl(RESERVE_GOLD) if row["sample_id"] == evidence["sample_id"])
    report = compare_candidates(candidates, [gold], evidence_by_id={evidence["evidence_id"]: evidence})
    assert report["gold_statement_count"] == 1
    assert "evidence_binding" in report

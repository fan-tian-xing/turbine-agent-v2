import copy
import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "config/stage12_extraction_response.schema.json").read_text(encoding="utf-8"))
CANDIDATE_ARTIFACT_SCHEMA = json.loads((ROOT / "config/stage12_candidate.schema.json").read_text(encoding="utf-8"))


def _candidate():
    return {
        "statement_text": "紧固件应拧紧，不得松动。",
        "statement_type": "requirement",
        "predicate": "requires",
        "source_unit_ids": ["unit-1", "unit-2"],
        "subject_entities": [{"surface_form": "紧固件", "role": "subject"}],
        "conditions": [],
        "applicability_scope": {"status": "unknown"},
    }


def _ok_response():
    return {
        "schema_version": 1,
        "response_kind": "stage12_candidate_extraction",
        "status": "ok",
        "candidates": [_candidate()],
        "source_unit_coverage": [
            {"source_unit_id": "unit-1", "status": "covered", "candidate_indexes": [1]},
            {"source_unit_id": "unit-2", "status": "covered", "candidate_indexes": [1]},
        ],
    }


def _errors(payload):
    return list(Draft202012Validator(SCHEMA).iter_errors(payload))


def test_response_schema_accepts_one_statement_covering_multiple_source_units():
    assert _errors(_ok_response()) == []


def test_covered_source_unit_requires_candidate_indexes_and_forbids_reason():
    empty = _ok_response()
    empty["source_unit_coverage"][0]["candidate_indexes"] = []
    assert _errors(empty)

    explained = _ok_response()
    explained["source_unit_coverage"][0]["reason"] = "无需排除"
    assert _errors(explained)


def test_excluded_source_unit_requires_reason_and_forbids_candidate_indexes():
    payload = _ok_response()
    payload["source_unit_coverage"][1] = {
        "source_unit_id": "unit-2",
        "status": "excluded",
        "candidate_indexes": [],
    }
    assert _errors(payload)

    payload["source_unit_coverage"][1]["reason"] = "仅为不完整续接片段"
    assert _errors(payload) == []

    payload["source_unit_coverage"][1]["candidate_indexes"] = [1]
    assert _errors(payload)


def test_candidate_source_unit_ids_are_required_nonempty_and_unique():
    missing = _ok_response()
    del missing["candidates"][0]["source_unit_ids"]
    assert _errors(missing)

    empty = _ok_response()
    empty["candidates"][0]["source_unit_ids"] = []
    assert _errors(empty)

    duplicate = _ok_response()
    duplicate["candidates"][0]["source_unit_ids"] = ["unit-1", "unit-1"]
    assert _errors(duplicate)


def test_no_statement_requires_only_excluded_source_units():
    payload = {
        "schema_version": 1,
        "response_kind": "stage12_candidate_extraction",
        "status": "no_statement",
        "candidates": [],
        "no_statement_reason": "来源仅含组织性标题。",
        "source_unit_coverage": [
            {"source_unit_id": "unit-1", "status": "excluded", "candidate_indexes": [], "reason": "组织性标题"}
        ],
    }
    assert _errors(payload) == []

    invalid = copy.deepcopy(payload)
    invalid["source_unit_coverage"][0] = {
        "source_unit_id": "unit-1",
        "status": "covered",
        "candidate_indexes": [1],
    }
    assert _errors(invalid)


def test_provider_and_final_candidate_schemas_share_conditional_contracts():
    provider_candidate = SCHEMA["$defs"]["candidate"]
    final_candidate = CANDIDATE_ARTIFACT_SCHEMA["$defs"]["candidate"]
    assert final_candidate["allOf"] == provider_candidate["allOf"]
    assert (
        final_candidate["properties"]["applicability_scope"]["allOf"]
        == provider_candidate["properties"]["applicability_scope"]["allOf"]
    )

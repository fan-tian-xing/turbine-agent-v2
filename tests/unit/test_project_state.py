import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read_json(relative_path: str) -> dict:
    return json.loads((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))


def test_project_state_is_the_current_state_source():
    state = _read_json("data/project_state.json")

    assert state["artifact_kind"] == "project_state"
    assert state["current_stage"] == 7
    assert state["current_stage_status"] == "blocked_pending_user_review"
    assert state["next_stage"] is None
    assert state["next_stage_status"] == "blocked"
    assert state["formal_release"] is False
    assert state["stages"]["6"]["scope"] == "golden_sample_only"
    assert state["stages"]["7"]["scope"] == "candidate_terminology_and_capability_templates_only"


def test_project_state_references_matching_stage_exit_audits():
    state = _read_json("data/project_state.json")

    for stage in ("2", "3", "4", "5", "6", "7"):
        stage_state = state["stages"][stage]
        audit_path = stage_state["exit_audit"]
        audit = _read_json(audit_path)
        assert audit["stage"] == stage
        assert audit["status"] == stage_state["status"]

    stage6_audit = _read_json(state["stages"]["6"]["exit_audit"])
    assert stage6_audit["next_stage_allowed"] is True
    assert stage6_audit["next_stage"] == "Stage 7 terminology analysis and business capability questions"
    stage7_audit = _read_json(state["stages"]["7"]["exit_audit"])
    assert stage7_audit["next_stage_allowed"] is False
    assert stage7_audit["next_stage"] is None

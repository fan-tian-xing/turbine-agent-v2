import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _read_json(relative_path: str) -> dict:
    return json.loads((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))


def test_project_state_is_the_current_state_source():
    state = _read_json("data/project_state.json")

    assert state["artifact_kind"] == "project_state"
    assert state["formal_release"] is False
    current = state["stages"][str(state["current_stage"])]
    assert state["current_stage_status"] == current["status"]
    if state["next_stage_status"] == "ready":
        assert state["next_stage"] == state["current_stage"] + 1
    else:
        assert state["next_stage"] in (None, state["current_stage"] + 1)

    boundaries = state["boundaries"]
    assert any("阶段 7 候选不得自动 promotion" in item for item in boundaries)
    assert not any("高风险、缩写、OCR 变体及同义/旧称关系均须人工审核后才能进入阶段 8" in item for item in boundaries)


def test_project_state_references_matching_stage_exit_audits():
    state = _read_json("data/project_state.json")

    for stage in ("2", "3", "4", "5", "6", "7"):
        stage_state = state["stages"][stage]
        audit_path = stage_state["exit_audit"]
        audit = _read_json(audit_path)
        assert audit["stage"] == stage
        assert audit["status"] == stage_state["status"]

    current_stage = state["stages"][str(state["current_stage"])]
    if state["current_stage_status"] == "complete":
        current_audit = _read_json(current_stage["exit_audit"])
        assert current_audit["status"] == state["current_stage_status"]
        assert bool(current_audit["next_stage_allowed"]) == (state["next_stage_status"] == "ready")
    else:
        assert state["current_stage_status"] == "in_progress"
        assert current_stage["status"] == "in_progress"
        assert current_stage.get("entry_record")
        entry = _read_json(current_stage["entry_record"])
        assert entry["stage"] == str(state["current_stage"])
        assert entry["status"] == "in_progress"


def test_stage12_project_state_is_a_current_snapshot_not_an_execution_log():
    state = _read_json("data/project_state.json")
    stage12 = state["stages"]["12"]

    execution = stage12["real_llm_execution"]
    current = stage12["current_development_observation"]
    assert execution["current_batch_completed"] is True
    assert execution["evidence_success"] == current["real_llm_success"]
    assert execution["evidence_total"] == current["evidence_total"]
    assert execution["current_valid_cache_count"] == current["real_llm_success"]
    assert set(stage12["provider_connectivity"]) == {"primary", "backup"}

    serialized = json.dumps(stage12, ensure_ascii=False)
    for historical_marker in ("smoke1", "smoke2", "prompt_v", "minimal_chat_completion"):
        assert historical_marker not in serialized

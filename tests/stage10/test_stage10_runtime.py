from __future__ import annotations

import json
from pathlib import Path

import pytest

from turbine_kg.observability.runtime import (
    ExtractionBatch,
    KNOWLEDGE_STATUSES,
    run_with_cache,
    stable_content_fingerprint,
    validate_knowledge_status,
)
from turbine_kg.observability.lifecycle import impacted_by_revision
from turbine_kg.registry.identity import revision_id_for_source_path
from scripts.build_stage7_terminology import _stage7_cache_context


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "config/runtime_run.schema.json"


def _run(tmp_path: Path, *, force: bool = False, output=None, validate_output=None):
    return run_with_cache(
        cache_root=tmp_path / "runs",
        operation="terminology_extraction",
        input_refs=({"kind": "fixture", "id": "input", "sha256": "a" * 64},),
        cache_context=({"implementation": "fixture"},),
        output=output or {"value": "stable"},
        schema_path=SCHEMA,
        force=force,
        validate_output=validate_output,
    )


def test_content_fingerprint_keeps_fact_time_but_ignores_operation_time():
    base = {"value": 1, "started_at": "2026-09-13T00:00:00Z", "effective_time": "2020-01-01"}
    changed_run_time = {**base, "started_at": "2026-09-14T00:00:00Z"}
    changed_fact_time = {**base, "effective_time": "2021-01-01"}
    assert stable_content_fingerprint(base) == stable_content_fingerprint(changed_run_time)
    assert stable_content_fingerprint(base) != stable_content_fingerprint(changed_fact_time)


def test_run_cache_hit_reuses_structured_result_and_force_creates_new_run(tmp_path: Path):
    calls = {"count": 0}

    def build():
        calls["count"] += 1
        return {"value": calls["count"]}

    first, first_output = _run(tmp_path, output=build)
    cached, cached_output = _run(tmp_path, output=build)
    forced, forced_output = _run(tmp_path, force=True, output=build)

    assert calls["count"] == 2
    assert cached.extraction_batch_id == first.extraction_batch_id
    assert cached_output == first_output
    assert forced.extraction_batch_id != first.extraction_batch_id
    assert forced_output == {"value": 2}
    assert (tmp_path / "runs" / "batches" / first.extraction_batch_id / "batch.json").is_file()


def test_cache_corruption_is_rejected(tmp_path: Path):
    first, _ = _run(tmp_path)
    output_path = tmp_path / "runs" / "batches" / first.extraction_batch_id / "output.json"
    output_path.write_text(json.dumps({"value": "tampered"}), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint"):
        _run(tmp_path)


def test_failed_output_is_not_added_to_success_cache(tmp_path: Path):
    def reject(_value):
        raise ValueError("invalid structured output")

    with pytest.raises(ValueError, match="invalid structured"):
        _run(tmp_path, validate_output=reject)
    index = tmp_path / "runs" / "cache_index.json"
    assert not index.exists()


def test_failed_force_rerun_does_not_replace_successful_result(tmp_path: Path):
    first, _ = _run(tmp_path)

    def reject(_value):
        raise ValueError("mock model rejected output")

    with pytest.raises(ValueError, match="mock model"):
        _run(tmp_path, force=True, validate_output=reject)
    index = json.loads((tmp_path / "runs" / "cache_index.json").read_text(encoding="utf-8"))
    cached = next(iter(index.values()))
    assert cached["batch_record"].endswith(f"{first.extraction_batch_id}/batch.json")


def test_cache_hit_rechecks_input_gate_and_input_change_misses(tmp_path: Path):
    gate_calls = {"count": 0}
    builds = {"count": 0}

    def gate():
        gate_calls["count"] += 1

    def build():
        builds["count"] += 1
        return {"value": builds["count"]}

    kwargs = dict(
        cache_root=tmp_path / "runs",
        operation="terminology_extraction",
        cache_context=({"implementation": "fixture"},),
        output=build,
        schema_path=SCHEMA,
        validate_input=gate,
    )
    first, _ = run_with_cache(input_refs=({"kind": "fixture", "id": "input-a", "sha256": "a" * 64},), **kwargs)
    cached, _ = run_with_cache(input_refs=({"kind": "fixture", "id": "input-a", "sha256": "a" * 64},), **kwargs)
    changed, _ = run_with_cache(input_refs=({"kind": "fixture", "id": "input-b", "sha256": "c" * 64},), **kwargs)
    assert cached.extraction_batch_id == first.extraction_batch_id
    assert changed.extraction_batch_id != first.extraction_batch_id
    assert gate_calls["count"] == 3
    assert builds["count"] == 2


def test_stage7_contract_change_misses_cache_and_irrelevant_llm_settings_do_not_enter_context(tmp_path: Path):
    refs = lambda contract_sha: (
        {"kind": "input_manifest", "id": "manifest", "sha256": "a" * 64},
        {"kind": "contract", "id": "terminology", "path": "config/terminology_contract.json", "sha256": contract_sha},
    )
    kwargs = dict(
        cache_root=tmp_path / "runs",
        operation="terminology_extraction",
        cache_context=({"implementation": "stage7-fixture"},),
        output={"value": "stable"},
        schema_path=SCHEMA,
    )
    first, _ = run_with_cache(input_refs=refs("b" * 64), **kwargs)
    cached, _ = run_with_cache(input_refs=refs("b" * 64), **kwargs)
    changed, _ = run_with_cache(input_refs=refs("c" * 64), **kwargs)
    assert cached.extraction_batch_id == first.extraction_batch_id
    assert changed.extraction_batch_id != first.extraction_batch_id

    context = _stage7_cache_context(ROOT)
    serialized = json.dumps(context, ensure_ascii=False)
    assert "terminology/models.py" in serialized
    assert "documents/ids.py" in serialized
    assert "llm_model" not in serialized
    assert "llm_allow_evidence_send" not in serialized


def test_knowledge_lifecycle_statuses_and_revision_scoping():
    assert KNOWLEDGE_STATUSES == {"active", "superseded", "invalid"}
    assert validate_knowledge_status("superseded") == "superseded"
    with pytest.raises(ValueError):
        validate_knowledge_status("replaced")
    with pytest.raises(ValueError):
        validate_knowledge_status("published")
    impacted = impacted_by_revision(
        "rev-a",
        [
            {"evidence_id": "e-a", "revision_id": "rev-a", "status": "active"},
            {"evidence_id": "e-b", "revision_id": "rev-b", "status": "active"},
        ],
        [
            {"statement_id": "s-a", "evidence_ids": ["e-a"], "status": "active"},
            {"statement_id": "s-shared", "evidence_ids": ["e-a", "e-b"], "status": "active"},
            {"statement_id": "s-b", "evidence_ids": ["e-b"], "status": "active"},
        ],
    )
    assert impacted == {"evidence_ids": ["e-a"], "statement_ids": ["s-a", "s-shared"]}


def test_multi_revision_asset_assignment_is_explicit_and_checked(tmp_path: Path):
    mapping = tmp_path / "asset_revision_identity.tsv"
    document = "doc-" + "a" * 20
    rev1 = "rev-" + "1" * 20
    rev2 = "rev-" + "2" * 20
    mapping.write_text(
        "relative_path\tdocument_logical_id\trevision_id\n"
        f"old.pdf\t{document}\t{rev1}\n"
        f"new.pdf\t{document}\t{rev2}\n",
        encoding="utf-8",
    )
    assert revision_id_for_source_path(document, "old.pdf", mapping) == rev1
    assert revision_id_for_source_path(document, "new.pdf", mapping) == rev2
    with pytest.raises(KeyError):
        revision_id_for_source_path(document, "unassigned.pdf", mapping)
    with pytest.raises(ValueError):
        revision_id_for_source_path("doc-" + "b" * 20, "old.pdf", mapping)


def test_extraction_batch_round_trips_through_schema(tmp_path: Path):
    record, _ = _run(tmp_path)
    payload = record.as_dict()
    assert payload["schema_version"] == 1
    assert payload["output_ref"]["kind"] == "structured_result"
    assert isinstance(record, ExtractionBatch)
    assert set(payload) == {"schema_version", "extraction_batch_id", "operation", "status", "input_refs", "output_ref", "output_fingerprint"}

from __future__ import annotations

import json
from pathlib import Path

import pytest

from turbine_kg.observability.runtime import (
    RunRecord,
    producer_ref,
    prov_o_mapping,
    run_with_cache,
    stable_content_fingerprint,
)
from turbine_kg.registry.identity import revision_id_for_source_path


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "config/runtime_run.schema.json"


def _producer(tmp_path: Path) -> dict:
    source = tmp_path / "producer.py"
    source.write_text("producer = 1\n", encoding="utf-8")
    return producer_ref(tmp_path, (source,), label="fixture-producer")


def _run(tmp_path: Path, *, force: bool = False, output=None, validate_output=None):
    return run_with_cache(
        cache_root=tmp_path / "runs",
        operation="terminology_extraction",
        input_refs=({"id": "input", "sha256": "a" * 64},),
        config_refs=({"id": "config", "sha256": "b" * 64},),
        producer=_producer(tmp_path),
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
    assert cached.run_id == first.run_id
    assert cached_output == first_output
    assert forced.run_id != first.run_id
    assert forced_output == {"value": 2}
    assert (tmp_path / "runs" / "runs" / first.run_id / "run.json").is_file()


def test_cache_corruption_is_rejected(tmp_path: Path):
    first, _ = _run(tmp_path)
    output_path = tmp_path / "runs" / "runs" / first.run_id / "output.json"
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
    assert cached["run_record"].endswith(f"{first.run_id}/run.json")


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
        config_refs=({"id": "config", "sha256": "b" * 64},),
        producer=_producer(tmp_path),
        output=build,
        schema_path=SCHEMA,
        validate_input=gate,
    )
    first, _ = run_with_cache(input_refs=({"id": "input-a", "sha256": "a" * 64},), **kwargs)
    cached, _ = run_with_cache(input_refs=({"id": "input-a", "sha256": "a" * 64},), **kwargs)
    changed, _ = run_with_cache(input_refs=({"id": "input-b", "sha256": "c" * 64},), **kwargs)
    assert cached.run_id == first.run_id
    assert changed.run_id != first.run_id
    assert gate_calls["count"] == 3
    assert builds["count"] == 2


def test_minimal_prov_view_contains_required_relations(tmp_path: Path):
    record, _ = _run(tmp_path)
    view = prov_o_mapping(record)
    relation_types = {item["type"] for item in view["relations"]}
    assert {"used", "wasGeneratedBy", "wasDerivedFrom"} <= relation_types
    assert view["agent"]["associated_with"] == view["activity"]["id"]


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


def test_run_record_round_trips_through_schema(tmp_path: Path):
    record, _ = _run(tmp_path)
    payload = record.as_dict()
    assert payload["schema_version"] == 1
    assert payload["output_ref"]["kind"] == "structured_result"
    assert isinstance(record, RunRecord)

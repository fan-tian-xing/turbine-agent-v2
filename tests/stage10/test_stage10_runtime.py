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
from turbine_kg.observability.lifecycle import ensure_knowledge_status, impacted_by_revision
from turbine_kg.registry.identity import revision_id_for_source_path
from scripts import build_stage7_terminology as stage7
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
            {"evidence_id": "e-a", "revision_id": "rev-a", "knowledge_status": "active"},
            {"evidence_id": "e-b", "revision_id": "rev-b", "knowledge_status": "active"},
        ],
        [
            {"statement_id": "s-a", "evidence_ids": ["e-a"], "knowledge_status": "active"},
            {"statement_id": "s-shared", "evidence_ids": ["e-a", "e-b"], "knowledge_status": "active"},
            {"statement_id": "s-b", "evidence_ids": ["e-b"], "knowledge_status": "active"},
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


@pytest.mark.parametrize("historical_status", ["superseded", "invalid"])
def test_lifecycle_uses_knowledge_status_and_excludes_historical_dependents(historical_status):
    evidence = [
        {"evidence_id": "current", "revision_id": "rev-a", "knowledge_status": "active", "status": "completed"},
        {"evidence_id": "history", "revision_id": "rev-a", "knowledge_status": historical_status,
         "publication_stage": "published", "status": "active"},
    ]
    statements = [
        {"statement_id": "current", "evidence_ids": ["current"], "knowledge_status": "active", "status": "candidate_only"},
        {"statement_id": "history", "evidence_ids": ["current"], "knowledge_status": historical_status,
         "publication_stage": "published"},
        {"statement_id": "history-source", "evidence_ids": ["history"], "knowledge_status": "active"},
    ]
    assert impacted_by_revision("rev-a", evidence, statements) == {
        "evidence_ids": ["current"], "statement_ids": ["current"],
    }
    assert ensure_knowledge_status(evidence[1]) == historical_status


@pytest.mark.parametrize("record", [
    {"status": "active"}, {"publication_stage": "published"},
    {"knowledge_status": "completed"}, {"knowledge_status": None},
])
def test_lifecycle_missing_or_invalid_knowledge_status_fails_closed(record):
    with pytest.raises(ValueError, match="knowledge"):
        ensure_knowledge_status(record)
    with pytest.raises(ValueError, match="knowledge"):
        impacted_by_revision("rev-a", [{**record, "evidence_id": "e", "revision_id": "rev-a"}], [])
    with pytest.raises(ValueError, match="knowledge"):
        impacted_by_revision("rev-a", [], [{**record, "statement_id": "s", "evidence_ids": []}])


@pytest.mark.parametrize("change", ["operation", "inputs"])
def test_cache_index_cannot_rebind_another_valid_batch(tmp_path: Path, change):
    first, _ = _run(tmp_path)
    run_with_cache(
        cache_root=tmp_path / "runs",
        operation="evidence_build" if change == "operation" else "terminology_extraction",
        input_refs=({"kind": "fixture", "id": "other" if change == "inputs" else "input", "sha256": "a" * 64},),
        cache_context=({"implementation": "fixture"},),
        output={"value": "stable"}, schema_path=SCHEMA,
    )
    path = tmp_path / "runs/cache_index.json"
    index = json.loads(path.read_text(encoding="utf-8"))
    first_key = next(key for key, entry in index.items() if first.extraction_batch_id in entry["batch_record"])
    index[first_key] = next(entry for key, entry in index.items() if key != first_key)
    path.write_text(json.dumps(index), encoding="utf-8")
    with pytest.raises(ValueError, match="inputs or operation"):
        _run(tmp_path)


def test_cache_index_output_must_belong_to_its_batch(tmp_path: Path):
    first, _ = _run(tmp_path)
    second, _ = _run(tmp_path, force=True)
    path = tmp_path / "runs/cache_index.json"
    index = json.loads(path.read_text(encoding="utf-8"))
    next(iter(index.values()))["output"] = f"batches/{first.extraction_batch_id}/output.json"
    path.write_text(json.dumps(index), encoding="utf-8")
    assert first.extraction_batch_id != second.extraction_batch_id
    with pytest.raises(ValueError, match="output binding"):
        _run(tmp_path)


def _stage7_fixture(tmp_path, monkeypatch):
    from turbine_kg.settings import Settings
    import hashlib

    (tmp_path / "config").mkdir()
    (tmp_path / "data/registry").mkdir(parents=True)
    (tmp_path / "data/stage7").mkdir()
    (tmp_path / "data/stage6").mkdir()
    source = tmp_path / "sources"
    source.mkdir()
    (source / "fixture.pdf").write_bytes(b"original source")
    asset = {
        "asset_id": "asset-fixture", "relative_path": "fixture.pdf",
        "sha256": hashlib.sha256(b"original source").hexdigest(),
        "document_logical_id": "doc-fixture", "revision_id": "rev-fixture",
        "asset_kind": "original", "admission_status": "admitted",
        "text_adapter_status": "native_text_available",
    }
    manifest = {"pages": [{
        "page_status": "text_accepted", "page_id": "page-fixture", "physical_page": 1,
        "processing_asset_id": asset["asset_id"], "authority_asset_id": asset["asset_id"],
        "processing_relative_path": asset["relative_path"], "authority_relative_path": asset["relative_path"],
        "document_logical_id": asset["document_logical_id"], "revision_id": asset["revision_id"],
    }]}
    (tmp_path / "data/stage7/terminology_input_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "data/stage6/stage6_evidence_bundle.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "config/terminology_contract.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config/runtime_run.schema.json").write_bytes(SCHEMA.read_bytes())
    monkeypatch.setattr(stage7, "ROOT", tmp_path)
    monkeypatch.setattr(stage7, "STAGE7", tmp_path / "data/stage7")
    monkeypatch.setattr(stage7, "_stage7_cache_context", lambda root: ({"implementation": "fixture"},))
    monkeypatch.setattr(stage7, "load_terminology_contract", lambda path: {})
    monkeypatch.setattr(stage7, "analyze_terminology", lambda *args, **kwargs: [])
    calls = []
    monkeypatch.setattr(stage7, "_accepted_page_texts", lambda *args: calls.append("build") or {})
    settings = Settings(source_root=source, ocr_derived_root=tmp_path / "ocr")
    _write_stage7_registry(tmp_path, [asset])
    return manifest, asset, settings, calls


def _write_stage7_registry(root, assets):
    (root / "data/registry/source_assets.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in assets), encoding="utf-8",
    )
    (root / "config/source_allowlist.tsv").write_text(
        "sha256\tsize_bytes\tpath\n" + "".join(
            f"{row['sha256']}\t1\t{row['relative_path']}\n" for row in assets
        ), encoding="utf-8",
    )


def test_stage7_related_registry_sha_change_misses_but_unrelated_asset_does_not(tmp_path, monkeypatch, capsys):
    import hashlib
    manifest, asset, settings, calls = _stage7_fixture(tmp_path, monkeypatch)
    stage7._runtime_run(manifest, settings, force=False)
    first = json.loads(capsys.readouterr().out)
    unrelated = {**asset, "asset_id": "unrelated", "relative_path": "other.pdf", "sha256": "b" * 64}
    _write_stage7_registry(tmp_path, [asset, unrelated])
    stage7._runtime_run(manifest, settings, force=False)
    assert json.loads(capsys.readouterr().out)["extraction_batch_id"] == first["extraction_batch_id"]
    (settings.source_root / "fixture.pdf").write_bytes(b"changed source")
    asset["sha256"] = hashlib.sha256(b"changed source").hexdigest()
    _write_stage7_registry(tmp_path, [asset, unrelated])
    stage7._runtime_run(manifest, settings, force=False)
    assert json.loads(capsys.readouterr().out)["extraction_batch_id"] != first["extraction_batch_id"]
    assert calls == ["build", "build"]


@pytest.mark.parametrize("field", ["processing_relative_path", "authority_relative_path"])
def test_stage7_manifest_registry_path_mismatch_is_a_hard_failure(tmp_path, monkeypatch, field):
    manifest, asset, settings, calls = _stage7_fixture(tmp_path, monkeypatch)
    before = stage7._stage7_registry_ref(manifest)
    _write_stage7_registry(tmp_path, [{**asset, "relative_path": "renamed.pdf"}])
    assert stage7._stage7_registry_ref(manifest) != before
    # Update only the other path: the field under test remains stale.
    other = "authority_relative_path" if field == "processing_relative_path" else "processing_relative_path"
    manifest["pages"][0][other] = "renamed.pdf"
    with pytest.raises(ValueError, match=field + " differs"):
        stage7._runtime_run(manifest, settings, force=False)
    assert not calls


@pytest.mark.parametrize("field,value,error", [
    ("admission_status", "quarantined", "not currently admitted"),
    ("text_adapter_status", "text_review_required", "not currently ready"),
])
def test_stage7_cached_result_cannot_bypass_current_admission(tmp_path, monkeypatch, field, value, error):
    manifest, asset, settings, calls = _stage7_fixture(tmp_path, monkeypatch)
    stage7._runtime_run(manifest, settings, force=False)
    before = stage7._stage7_registry_ref(manifest)
    _write_stage7_registry(tmp_path, [{**asset, field: value}])
    assert stage7._stage7_registry_ref(manifest) == before
    with pytest.raises(ValueError, match=error):
        stage7._runtime_run(manifest, settings, force=False)
    assert calls == ["build"]


@pytest.mark.parametrize("field,value", [
    ("inputs", {}), ("contract_sha256", "b" * 64), ("status", "complete"),
    ("formal_release", True),
])
def test_stage7_output_package_must_match_current_inputs_and_candidate_boundary(field, value):
    inputs = {"registry_scope": {"sha256": "a" * 64}}
    output = {
        "schema_version": 1, "stage": "7", "artifact_kind": "terminology_candidates",
        "status": "candidate_only", "formal_release": False,
        "producer": "turbine_kg.terminology.analyzer", "inputs": inputs,
        "contract_sha256": "a" * 64, "candidates": [],
    }
    output[field] = value
    with pytest.raises(ValueError, match="input binding"):
        stage7._validate_runtime_output(output, inputs, "a" * 64, set())

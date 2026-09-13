"""Minimal, local run records and validated result cache for Stage 10.

This module records provenance around an existing producer.  It deliberately
does not define engineering semantics, a Release package, or a second source
of truth for Evidence and Statements.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from jsonschema import Draft202012Validator


SYSTEM_TIME_FIELDS = frozenset({"started_at", "finished_at", "imported_at", "reviewed_at", "run_at", "recorded_at"})
SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_content_fingerprint(value: Any) -> str:
    """Hash semantic content while ignoring only operational timestamps."""
    def scrub(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: scrub(val) for key, val in item.items() if key not in SYSTEM_TIME_FIELDS}
        if isinstance(item, list):
            return [scrub(val) for val in item]
        return item

    return sha256_value(scrub(value))


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    operation: str
    status: str
    input_refs: tuple[dict[str, Any], ...]
    config_refs: tuple[dict[str, Any], ...]
    producer_ref: dict[str, Any]
    output_ref: dict[str, Any] | None
    output_fingerprint: str | None
    cache_key: str
    started_at: str
    finished_at: str | None = None
    failure_code: str | None = None
    model_attempts: tuple[dict[str, Any], ...] = ()
    review_refs: tuple[dict[str, Any], ...] = ()
    legacy_refs: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "operation": self.operation,
            "status": self.status,
            "input_refs": list(self.input_refs),
            "config_refs": list(self.config_refs),
            "producer_ref": self.producer_ref,
            "output_ref": self.output_ref,
            "output_fingerprint": self.output_fingerprint,
            "cache_key": self.cache_key,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "failure_code": self.failure_code,
            "model_attempts": list(self.model_attempts),
            "review_refs": list(self.review_refs),
            "legacy_refs": list(self.legacy_refs),
        }


def producer_ref(root: Path, files: Iterable[Path], *, label: str) -> dict[str, Any]:
    """Return readable producer/version references without copying source code."""
    root = root.resolve()
    file_refs = []
    for path in files:
        resolved = path.resolve()
        file_refs.append({
            "path": str(resolved.relative_to(root)).replace("\\", "/"),
            "sha256": _file_sha256(resolved),
        })
    return {"label": label, "git_head": _git_head(root), "files": file_refs}


def _validate_shape(record: dict[str, Any], schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(record), key=lambda error: list(error.path))
    if errors:
        raise ValueError("invalid Stage 10 run record: " + "; ".join(error.message for error in errors))


def prov_o_mapping(record: RunRecord) -> dict[str, Any]:
    """Build the small PROV-O-shaped view consumed by the exit audit."""
    activity = f"urn:turbine-v2:activity:{record.run_id}"
    entities = [
        {"id": ref.get("id", ref.get("path", "")), "kind": "Entity", "used_by": activity}
        for ref in record.input_refs
    ]
    relations = [
        {"type": "used", "entity": ref.get("id", ref.get("path", "")), "activity": activity}
        for ref in record.input_refs
    ]
    if record.output_ref:
        output_id = record.output_ref.get("path", record.output_ref.get("id", ""))
        entities.append({"id": output_id, "kind": "Entity", "generated_by": activity})
        relations.append({"type": "wasGeneratedBy", "entity": output_id, "activity": activity})
        for ref in record.input_refs:
            relations.append({
                "type": "wasDerivedFrom",
                "generated_entity": output_id,
                "used_entity": ref.get("id", ref.get("path", "")),
            })
    return {
        "entity": entities,
        "activity": {"id": activity, "operation": record.operation, "started_at": record.started_at, "finished_at": record.finished_at},
        "agent": {"id": record.producer_ref.get("label", "unknown"), "associated_with": activity},
        "relations": relations,
    }


def run_with_cache(
    *,
    cache_root: Path,
    operation: str,
    input_refs: Iterable[dict[str, Any]],
    config_refs: Iterable[dict[str, Any]],
    producer: dict[str, Any],
    output: dict[str, Any] | Callable[[], dict[str, Any]],
    schema_path: Path,
    force: bool = False,
    validate_input: Callable[[], None] | None = None,
    validate_output: Callable[[dict[str, Any]], None] | None = None,
    model_attempts: Iterable[dict[str, Any]] = (),
    review_refs: Iterable[dict[str, Any]] = (),
    legacy_refs: Iterable[dict[str, Any]] = (),
) -> tuple[RunRecord, dict[str, Any]]:
    """Run or reuse one structured result, with validation on every access."""
    cache_root = cache_root.resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    inputs = tuple(input_refs)
    configs = tuple(config_refs)
    attempts = tuple(model_attempts)
    reviews = tuple(review_refs)
    legacy = tuple(legacy_refs)
    cache_key = sha256_value({"operation": operation, "inputs": inputs, "configs": configs, "producer": producer})
    index_path = cache_root / "cache_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}

    if validate_input is not None:
        validate_input()
    if not force and cache_key in index:
        cached_run_path = cache_root / index[cache_key]["run_record"]
        cached_output_path = cache_root / index[cache_key]["output"]
        cached_record = json.loads(cached_run_path.read_text(encoding="utf-8"))
        cached_output = json.loads(cached_output_path.read_text(encoding="utf-8"))
        _validate_shape(cached_record, schema_path)
        if cached_record.get("status") != "succeeded" or cached_record.get("cache_key") != cache_key:
            raise ValueError("cached run is not a successful match")
        if cached_record.get("output_fingerprint") != stable_content_fingerprint(cached_output):
            raise ValueError("cached structured result fingerprint does not match its run record")
        if validate_output is not None:
            validate_output(cached_output)
        return RunRecord(**{key: tuple(value) if key in {"input_refs", "config_refs", "model_attempts", "review_refs", "legacy_refs"} else value for key, value in cached_record.items() if key != "schema_version"}), cached_output

    run_id = f"run-{uuid.uuid4().hex[:20]}"
    output = output() if callable(output) else output
    started = utc_now()
    output_fingerprint = stable_content_fingerprint(output)
    if validate_output is not None:
        validate_output(output)
    run_record = RunRecord(
        run_id=run_id, operation=operation, status="succeeded",
        input_refs=inputs, config_refs=configs, producer_ref=producer,
        output_ref={"path": f"runs/{run_id}/output.json", "kind": "structured_result"},
        output_fingerprint=output_fingerprint, cache_key=cache_key, started_at=started,
        finished_at=utc_now(), model_attempts=attempts, review_refs=reviews, legacy_refs=legacy,
    )
    run_dir = cache_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    output_path = run_dir / "output.json"
    record_path = run_dir / "run.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record_dict = run_record.as_dict()
    _validate_shape(record_dict, schema_path)
    record_path.write_text(json.dumps(record_dict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index[cache_key] = {"run_record": f"runs/{run_id}/run.json", "output": f"runs/{run_id}/output.json"}
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return run_record, output

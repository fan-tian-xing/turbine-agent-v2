"""Small cache adapter for extraction batches.

Knowledge provenance lives on Document, Revision, Page/SourceSpan, Evidence
and Statement. This module only keeps a lightweight batch marker and a local
structured-result cache; implementation details used to calculate cache keys
never become formal knowledge fields.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from jsonschema import Draft202012Validator


SCHEMA_VERSION = 1
KNOWLEDGE_STATUSES = frozenset({"active", "superseded", "invalid"})


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_content_fingerprint(value: Any) -> str:
    """Hash content while ignoring only explicitly operational timestamps."""
    operational = {"started_at", "finished_at", "imported_at", "reviewed_at", "run_at", "recorded_at"}

    def scrub(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: scrub(value) for key, value in item.items() if key not in operational}
        if isinstance(item, list):
            return [scrub(value) for value in item]
        return item

    return sha256_value(scrub(value))


def validate_knowledge_status(status: str) -> str:
    if status not in KNOWLEDGE_STATUSES:
        raise ValueError(f"unsupported knowledge lifecycle status: {status}")
    return status


@dataclass(frozen=True, slots=True)
class ExtractionBatch:
    """The only extraction metadata that formal knowledge needs to retain."""

    extraction_batch_id: str
    operation: str
    input_refs: tuple[dict[str, Any], ...]
    output_ref: dict[str, Any]
    output_fingerprint: str
    status: str = "completed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "extraction_batch_id": self.extraction_batch_id,
            "operation": self.operation,
            "status": self.status,
            "input_refs": list(self.input_refs),
            "output_ref": self.output_ref,
            "output_fingerprint": self.output_fingerprint,
        }


def _validate_shape(record: dict[str, Any], schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(record), key=lambda error: list(error.path))
    if errors:
        raise ValueError("invalid extraction batch record: " + "; ".join(error.message for error in errors))


def run_with_cache(
    *,
    cache_root: Path,
    operation: str,
    input_refs: Iterable[dict[str, Any]],
    output: dict[str, Any] | Callable[[], dict[str, Any]],
    schema_path: Path,
    cache_context: Iterable[dict[str, Any]] = (),
    force: bool = False,
    validate_input: Callable[[], None] | None = None,
    validate_output: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[ExtractionBatch, dict[str, Any]]:
    """Run or reuse one structured result without exposing cache metadata."""
    cache_root = cache_root.resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    inputs = tuple(input_refs)
    context = tuple(cache_context)
    cache_key = sha256_value({"operation": operation, "inputs": inputs, "context": context})
    index_path = cache_root / "cache_index.json"
    raw_index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    # Ignore pre-simplification entries. They remain on disk for reference but
    # cannot satisfy the lightweight batch contract and must not become a
    # formal dependency of the new cache.
    index = {
        key: value for key, value in raw_index.items()
        if isinstance(value, dict) and "batch_record" in value and "output" in value
    }
    if validate_input is not None:
        validate_input()
    if not force and cache_key in index:
        entry = index[cache_key]
        record_path = cache_root / entry["batch_record"]
        output_path = cache_root / entry["output"]
        record_payload = json.loads(record_path.read_text(encoding="utf-8"))
        cached_output = json.loads(output_path.read_text(encoding="utf-8"))
        _validate_shape(record_payload, schema_path)
        if record_payload.get("status") != "completed":
            raise ValueError("cached extraction batch is not completed")
        if record_payload.get("output_fingerprint") != stable_content_fingerprint(cached_output):
            raise ValueError("cached structured result fingerprint does not match its batch record")
        if validate_output is not None:
            validate_output(cached_output)
        batch = ExtractionBatch(**{key: tuple(value) if key == "input_refs" else value for key, value in record_payload.items() if key != "schema_version"})
        return batch, cached_output

    result = output() if callable(output) else output
    if validate_output is not None:
        validate_output(result)
    batch_id = f"batch-{uuid.uuid4().hex[:20]}"
    batch = ExtractionBatch(
        extraction_batch_id=batch_id,
        operation=operation,
        status="completed",
        input_refs=inputs,
        output_ref={"path": f"batches/{batch_id}/output.json", "kind": "structured_result"},
        output_fingerprint=stable_content_fingerprint(result),
    )
    batch_dir = cache_root / "batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=False)
    output_path = batch_dir / "output.json"
    record_path = batch_dir / "batch.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record_payload = batch.as_dict()
    _validate_shape(record_payload, schema_path)
    record_path.write_text(json.dumps(record_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    index[cache_key] = {"batch_record": f"batches/{batch_id}/batch.json", "output": f"batches/{batch_id}/output.json"}
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return batch, result

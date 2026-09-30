"""Write the single current Stage 12 real-LLM failure summary."""

from __future__ import annotations

import json
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


SUMMARY_PATH = Path(__file__).resolve().parents[1] / "data/stage12/stage12_real_llm_failure_summary.json"
MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data/stage12/stage12_input_manifest.json"


def decorate_event(evidence: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Keep diagnostic categories in memory without copying Evidence or model text."""
    return {
        "evidence_id": evidence.get("evidence_id"),
        "attempt": event.get("attempt"),
        "outcome": event.get("outcome"),
        "failure_type": event.get("failure_type"),
        "field": event.get("field"),
        "validator_reason": str(event.get("validator_reason") or "")[:300] if event.get("outcome") == "failure" else None,
        "exception_type": event.get("exception_type"),
        "provider_alias": event.get("provider_alias"),
        "root_cause": event.get("root_cause"),
        "status_code": event.get("status_code"),
        "elapsed_seconds": event.get("elapsed_seconds"),
        "transport_attempts": event.get("transport_attempts"),
        "fallback_triggered": event.get("fallback_triggered", False),
    }


def write_failure_summary(
    events: Iterable[dict[str, Any]],
    *,
    run_kind: str,
    evidence_ids: list[str],
    cache_maintenance: dict[str, Any] | None = None,
    status: str = "completed",
    progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = list(events)
    failures = [event for event in events if event.get("outcome") == "failure"]
    successes = [event for event in events if event.get("outcome") == "success"]
    by_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_evidence[str(event.get("evidence_id"))].append(event)
    retry_recovered = sum(
        any(item.get("outcome") == "failure" for item in items)
        and any(item.get("outcome") == "success" for item in items)
        for items in by_evidence.values()
    )
    unresolved_failures = {
        evidence_id: [
            {key: value for key, value in item.items() if key in {"attempt", "failure_type", "field", "validator_reason", "root_cause", "status_code", "provider_alias"} and value is not None}
            for item in items if item.get("outcome") == "failure"
        ]
        for evidence_id, items in by_evidence.items()
        if not any(item.get("outcome") == "success" for item in items)
        and any(item.get("outcome") == "failure" for item in items)
    }
    counts = {
        "transport_failure": sum(item.get("failure_type") == "transport_failure" for item in failures),
        "schema_failure": sum(item.get("failure_type") == "schema_failure" for item in failures),
        "semantic_validation_failure": sum(item.get("failure_type") == "semantic_validation_failure" for item in failures),
        "success": len(successes),
        "attempts": len(events),
        "model_call_count": sum(int(event.get("transport_attempts") or 1) for event in events),
        "elapsed_seconds": round(sum(float(event.get("elapsed_seconds") or 0) for event in events), 3),
        "retry_recovered": retry_recovered,
        "fallback_triggered": sum(any(item.get("fallback_triggered") for item in items) for items in by_evidence.values()),
        "fallback_success": sum(any(item.get("outcome") == "success" and item.get("fallback_triggered") for item in items) for items in by_evidence.values()),
        "provider_counts": {
            str(provider): sum(item.get("provider_alias") == provider for item in events)
            for provider in sorted({item.get("provider_alias") for item in events if item.get("provider_alias")})
        },
        "root_cause_counts": {
            str(root_cause): sum(item.get("root_cause") == root_cause for item in failures)
            for root_cause in sorted({item.get("root_cause") for item in failures if item.get("root_cause")})
        },
        "failure_field_counts": {
            str(field): sum(item.get("field") == field for item in failures)
            for field in sorted({item.get("field") for item in failures if item.get("field")})
        },
    }
    summary = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_real_llm_failure_summary",
        "status": status,
        "run_kind": run_kind,
        "source_split": "development_regression_golden",
        "formal_release": False,
        "holdout_used_for_tuning": False,
        "blind_read": False,
        "evidence_ids": evidence_ids,
        "input_manifest_sha256": hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest() if MANIFEST_PATH.is_file() else None,
        "counts": counts,
        "unresolved_failures": unresolved_failures,
        "cache_maintenance": {
            key: value for key, value in (cache_maintenance or {}).items()
            if isinstance(value, int) and not isinstance(value, bool)
        },
        "progress": {
            key: value for key, value in (progress or {}).items()
            if isinstance(value, (int, bool)) or key == "current_artifact_status" and isinstance(value, str)
        },
        "raw_model_response_persisted": False,
        "sensitive_transport_data_persisted": False,
        "producer": "scripts/stage12_failure_summary.py",
    }
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary

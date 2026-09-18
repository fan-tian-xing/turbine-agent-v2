"""Write the single current Stage 12 real-LLM failure summary."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


SUMMARY_PATH = Path(__file__).resolve().parents[1] / "data/stage12/stage12_real_llm_failure_summary.json"


def _clip(value: Any, limit: int = 1600) -> Any:
    if isinstance(value, str):
        return value[:limit]
    return value


def decorate_event(evidence: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Attach only the canonical Evidence text needed for diagnosis."""
    result = {
        "evidence_id": evidence.get("evidence_id"),
        "attempt": event.get("attempt"),
        "outcome": event.get("outcome"),
        "failure_type": event.get("failure_type"),
        "field": event.get("field"),
        "validator_reason": _clip(event.get("validator_reason")),
        "evidence_value_or_text": _clip(evidence.get("effective_text") or evidence.get("source_text") or ""),
        "model_value_or_text": event.get("model_value_or_text"),
    }
    return result


def write_failure_summary(
    events: Iterable[dict[str, Any]],
    *,
    run_kind: str,
    evidence_ids: list[str],
    cache_maintenance: dict[str, Any] | None = None,
    status: str = "completed",
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
    counts = {
        "transport_failure": sum(item.get("failure_type") == "transport_failure" for item in failures),
        "schema_failure": sum(item.get("failure_type") == "schema_failure" for item in failures),
        "semantic_validation_failure": sum(item.get("failure_type") == "semantic_validation_failure" for item in failures),
        "success": len(successes),
        "attempts": len(events),
        "retry_recovered": retry_recovered,
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
        "counts": counts,
        "failures": failures,
        "attempts": events,
        "cache_maintenance": cache_maintenance or {},
        "raw_model_response_persisted": False,
        "sensitive_transport_data_persisted": False,
        "producer": "scripts/stage12_failure_summary.py",
    }
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary

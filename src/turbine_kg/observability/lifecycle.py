"""Small helpers for revision-scoped knowledge maintenance.

These helpers operate on already validated Evidence and Statement records. They
do not own either record type or delete shared entities from a projection.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .runtime import validate_knowledge_status


def impacted_by_revision(
    revision_id: str,
    evidence_records: Iterable[Mapping[str, Any]],
    statement_records: Iterable[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Return active Evidence and Statements derived from one Revision.

    Shared Entity IDs are deliberately not returned: a projection consumer can
    retain them while removing only the affected source-dependent relations.
    Superseded and invalid knowledge remains historical, outside this rebuild
    scope. Publication and artifact statuses do not grant knowledge validity.
    """
    evidence = [
        row for row in evidence_records
        if ensure_knowledge_status(row) == "active"
        and row.get("revision_id") == revision_id
    ]
    evidence_ids = {str(row["evidence_id"]) for row in evidence if row.get("evidence_id")}
    statement_ids = sorted(
        str(row["statement_id"])
        for row in statement_records
        if ensure_knowledge_status(row) == "active"
        and row.get("statement_id")
        and any(str(evidence_id) in evidence_ids for evidence_id in row.get("evidence_ids", ()))
    )
    return {"evidence_ids": sorted(evidence_ids), "statement_ids": statement_ids}


def ensure_knowledge_status(record: Mapping[str, Any]) -> str:
    """Require the knowledge lifecycle field; never infer it from status."""
    if "knowledge_status" not in record:
        raise ValueError("knowledge_status is required for lifecycle analysis")
    return validate_knowledge_status(record["knowledge_status"])

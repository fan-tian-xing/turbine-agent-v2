"""Small helpers for revision-scoped knowledge maintenance.

These helpers operate on already validated Evidence and Statement records. They
do not own either record type or delete shared entities from a projection.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .runtime import KNOWLEDGE_STATUSES, validate_knowledge_status


def impacted_by_revision(
    revision_id: str,
    evidence_records: Iterable[Mapping[str, Any]],
    statement_records: Iterable[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Return only Evidence and Statements derived from one Revision.

    Shared Entity IDs are deliberately not returned: a projection consumer can
    retain them while removing only the affected source-dependent relations.
    """
    evidence = [
        row for row in evidence_records
        if row.get("revision_id") == revision_id
        and row.get("status", "active") in KNOWLEDGE_STATUSES
    ]
    evidence_ids = {str(row["evidence_id"]) for row in evidence if row.get("evidence_id")}
    statement_ids = sorted(
        str(row["statement_id"])
        for row in statement_records
        if row.get("statement_id")
        and any(str(evidence_id) in evidence_ids for evidence_id in row.get("evidence_ids", ()))
        and row.get("status", "active") in KNOWLEDGE_STATUSES
    )
    return {"evidence_ids": sorted(evidence_ids), "statement_ids": statement_ids}


def ensure_knowledge_status(record: Mapping[str, Any]) -> str:
    """Validate the optional lifecycle status used by future formal records."""
    return validate_knowledge_status(str(record.get("status", "active")))

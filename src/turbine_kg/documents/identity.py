"""Controlled identity and Revision catalog compatibility layer."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RevisionRecord:
    document_logical_id: str
    revision_id: str
    revision_label: str
    revision_basis: str = "legacy_registry_baseline"
    revision_status: str = "current"
    supersedes_revision_id: str | None = None
    identity_basis: str = "controlled_mapping"
    content_identity_status: str = "not_assessed"


def load_revision_catalog(path: Path) -> tuple[RevisionRecord, ...]:
    """Read both the Stage 3 three-column map and the Stage 4 catalog shape."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader((line for line in handle if not line.startswith("#")), delimiter="\t")]
    required = {"document_logical_id", "revision_id", "revision_label"}
    if not rows or not required <= set(rows[0]):
        raise ValueError("revision catalog is missing required identity columns")
    records = []
    seen_ids: set[str] = set()
    docs: dict[str, set[str]] = {}
    for row in rows:
        revision_id = row["revision_id"].strip()
        document_id = row["document_logical_id"].strip()
        if not document_id or not revision_id or revision_id in seen_ids:
            raise ValueError("revision catalog contains an empty or duplicate identity")
        seen_ids.add(revision_id)
        docs.setdefault(document_id, set()).add(revision_id)
        records.append(RevisionRecord(
            document_logical_id=document_id,
            revision_id=revision_id,
            revision_label=row["revision_label"].strip(),
            revision_basis=row.get("revision_basis") or "legacy_registry_baseline",
            revision_status=row.get("revision_status") or "current",
            supersedes_revision_id=row.get("supersedes_revision_id") or None,
            identity_basis=row.get("identity_basis") or "controlled_mapping",
            content_identity_status=row.get("content_identity_status") or "not_assessed",
        ))
    by_id = {record.revision_id: record for record in records}
    for record in records:
        if record.supersedes_revision_id:
            old = by_id.get(record.supersedes_revision_id)
            if old is None or old.document_logical_id != record.document_logical_id:
                raise ValueError("superseded Revision must belong to the same logical document")
    return tuple(records)

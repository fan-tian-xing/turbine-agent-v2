"""Stable source and revision identity helpers."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from .schema import DOCUMENT_ID_PATTERN


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DOCUMENT_IDENTITY_PATH = PROJECT_ROOT / "config" / "document_identity.tsv"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def asset_id_for_path(relative_path: str) -> str:
    """Return a stable registration ID for one allowlisted asset path."""
    return f"asset-{digest(relative_path)[:20]}"


def load_document_identity_map(path: Path = DEFAULT_DOCUMENT_IDENTITY_PATH) -> dict[str, str]:
    """Load reviewed path-to-logical-document assignments."""
    if not path.is_file():
        raise FileNotFoundError(f"controlled document identity map is missing: {path}")
    data_lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    reader = csv.DictReader(data_lines, delimiter="\t")
    expected_fields = ["relative_path", "document_logical_id"]
    if reader.fieldnames != expected_fields:
        raise ValueError(f"identity map fields must be {expected_fields}, got {reader.fieldnames}")
    assignments: dict[str, str] = {}
    for row in reader:
        relative_path = row["relative_path"]
        document_id = row["document_logical_id"]
        if not relative_path or relative_path in assignments:
            raise ValueError(f"document identity map has duplicate or empty path: {relative_path!r}")
        if not DOCUMENT_ID_PATTERN.fullmatch(document_id):
            raise ValueError(f"invalid document logical ID for {relative_path}: {document_id}")
        assignments[relative_path] = document_id
    return assignments

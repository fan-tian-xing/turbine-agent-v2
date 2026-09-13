"""Stable source and revision identity helpers."""

from __future__ import annotations

import csv
import hashlib
from functools import lru_cache
from pathlib import Path

from .schema import DOCUMENT_ID_PATTERN


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DOCUMENT_IDENTITY_PATH = PROJECT_ROOT / "config" / "document_identity.tsv"
DEFAULT_REVISION_IDENTITY_PATH = PROJECT_ROOT / "config" / "revision_identity.tsv"
DEFAULT_ASSET_REVISION_PATH = PROJECT_ROOT / "config" / "asset_revision_identity.tsv"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def asset_id_for_path(relative_path: str) -> str:
    """Return a stable registration ID for one allowlisted asset path."""
    return f"asset-{digest(relative_path)[:20]}"


@lru_cache(maxsize=1)
def load_revision_identity_map(path: Path = DEFAULT_REVISION_IDENTITY_PATH) -> dict[str, tuple[str, str]]:
    """Load revision identities assigned to logical documents, not file paths."""
    data_lines = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    reader = csv.DictReader(data_lines, delimiter="\t")
    expected_fields = ["document_logical_id", "revision_id", "revision_label"]
    if reader.fieldnames != expected_fields:
        raise ValueError(f"revision identity fields are invalid: {reader.fieldnames}")
    result: dict[str, tuple[str, str]] = {}
    for row in reader:
        document_id = row["document_logical_id"]
        if document_id in result or not document_id or not row["revision_id"] or not row["revision_label"]:
            raise ValueError(f"revision identity map has duplicate or empty values: {row}")
        result[document_id] = (row["revision_id"], row["revision_label"])
    return result


def revision_for_document(document_logical_id: str) -> tuple[str, str]:
    """Return the controlled revision ID and label for a logical document."""
    try:
        return load_revision_identity_map()[document_logical_id]
    except KeyError as error:
        raise KeyError(f"no controlled revision identity for {document_logical_id}") from error


def revision_id_for_source_path(
    document_logical_id: str,
    source_relative_path: str,
    asset_revision_path: Path = DEFAULT_ASSET_REVISION_PATH,
) -> str:
    """Resolve a path's controlled Revision, with a multi-Revision override.

    The legacy document-level map remains the fallback for the current corpus.
    When the optional controlled asset map is present, every listed path must
    carry an explicit Revision so two revisions of one logical document cannot
    be silently collapsed.
    """
    assignments = load_asset_revision_map(asset_revision_path)
    if assignments:
        if source_relative_path not in assignments:
            # Preserve the historical compatibility wrapper for callers that
            # use a synthetic/renamed path; registry construction itself
            # checks that the controlled map covers every allowlisted path.
            if asset_revision_path == DEFAULT_ASSET_REVISION_PATH:
                return revision_for_document(document_logical_id)[0]
            raise KeyError(f"no controlled Revision assignment for {source_relative_path}")
        assigned_document, revision_id = assignments[source_relative_path]
        if assigned_document != document_logical_id:
            raise ValueError(f"asset Revision assignment disagrees with logical document for {source_relative_path}")
        return revision_id
    return revision_for_document(document_logical_id)[0]


def load_asset_revision_map(path: Path = DEFAULT_ASSET_REVISION_PATH) -> dict[str, tuple[str, str]]:
    """Load optional path-to-document/Revision assignments.

    The file is intentionally optional while the v2 corpus has one Revision
    per logical document.  Its presence is an explicit signal that all paths
    must be assigned, which makes multi-Revision ambiguity fail closed.
    """
    if not path.is_file():
        return {}
    data_lines = [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    reader = csv.DictReader(data_lines, delimiter="\t")
    expected_fields = ["relative_path", "document_logical_id", "revision_id"]
    if reader.fieldnames != expected_fields:
        raise ValueError(f"asset Revision identity fields are invalid: {reader.fieldnames}")
    assignments: dict[str, tuple[str, str]] = {}
    for row in reader:
        relative_path = row["relative_path"]
        document_id = row["document_logical_id"]
        revision_id = row["revision_id"]
        if (
            not relative_path or relative_path in assignments
            or not DOCUMENT_ID_PATTERN.fullmatch(document_id)
            or not revision_id
        ):
            raise ValueError(f"asset Revision identity map has invalid or duplicate row: {row}")
        assignments[relative_path] = (document_id, revision_id)
    return assignments


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


def load_derived_asset_links(path: Path) -> dict[str, str]:
    """Load reviewed OCR-derivative to source-asset links."""
    data_lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    reader = csv.DictReader(data_lines, delimiter="\t")
    if reader.fieldnames != ["derived_relative_path", "source_relative_path"]:
        raise ValueError(f"derived asset link fields are invalid: {reader.fieldnames}")
    links = {row["derived_relative_path"]: row["source_relative_path"] for row in reader}
    if len(links) != len(data_lines) - 1 or any(not derived or not source for derived, source in links.items()):
        raise ValueError("derived asset links must be unique and non-empty")
    return links

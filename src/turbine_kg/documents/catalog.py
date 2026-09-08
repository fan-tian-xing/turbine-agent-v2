"""Validated identity catalog for Registry assets and Document IR inputs.

The Registry remains the authority for asset identity.  This module only
creates a typed, read-only view that the Stage 4 parser can consume without
inventing a second numbering scheme or treating a file path as an identity.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .identity import RevisionRecord, load_revision_catalog


_ID_PATTERNS = {
    "asset": re.compile(r"^asset-[0-9a-f]{20}$"),
    "document": re.compile(r"^doc-[0-9a-f]{20}$"),
    "revision": re.compile(r"^rev-[0-9a-f]{20}$"),
}


@dataclass(frozen=True, slots=True)
class AssetIdentity:
    """A Registry asset with an optional controlled derivation relation."""

    asset_id: str
    document_logical_id: str
    revision_id: str
    asset_kind: str
    relative_path: str
    sha256: str
    source_root_id: str
    derived_from_asset_id: str | None = None
    derivation_type: str | None = None


@dataclass(frozen=True, slots=True)
class IdentityCatalog:
    """Read-only identity index used at the Document IR boundary."""

    assets: tuple[AssetIdentity, ...]
    revisions: tuple[RevisionRecord, ...]
    path_aliases: dict[str, str]

    def asset_for_path(self, relative_path: str) -> AssetIdentity:
        try:
            asset_id = self.path_aliases[relative_path]
        except KeyError as exc:
            raise KeyError(f"unregistered asset path: {relative_path}") from exc
        return self.asset_for_id(asset_id)

    def asset_for_id(self, asset_id: str) -> AssetIdentity:
        for asset in self.assets:
            if asset.asset_id == asset_id:
                return asset
        raise KeyError(f"unregistered asset ID: {asset_id}")

    def revision_for_id(self, revision_id: str) -> RevisionRecord:
        for revision in self.revisions:
            if revision.revision_id == revision_id:
                return revision
        raise KeyError(f"unregistered Revision ID: {revision_id}")


def load_identity_catalog(
    assets_path: Path,
    revision_catalog_path: Path,
    derived_links_path: Path | None = None,
) -> IdentityCatalog:
    """Load and validate the current Registry identity material.

    ``relative_path`` is an address, never an identity.  A path lookup always
    returns the Registry ``asset_id``; derived OCR files additionally retain
    their exact source asset and are required to share its logical document
    and Revision.
    """

    revisions = load_revision_catalog(revision_catalog_path)
    revision_by_id = {record.revision_id: record for record in revisions}
    assets: list[AssetIdentity] = []
    by_id: dict[str, AssetIdentity] = {}
    by_path: dict[str, str] = {}
    with assets_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid Registry JSON at line {line_number}") from exc
            required = {
                "asset_id",
                "document_logical_id",
                "revision_id",
                "asset_kind",
                "relative_path",
                "sha256",
                "source_root_id",
            }
            if not required <= row.keys():
                missing = ", ".join(sorted(required - row.keys()))
                raise ValueError(f"Registry asset line {line_number} is missing: {missing}")
            asset = AssetIdentity(
                asset_id=str(row["asset_id"]).strip(),
                document_logical_id=str(row["document_logical_id"]).strip(),
                revision_id=str(row["revision_id"]).strip(),
                asset_kind=str(row["asset_kind"]).strip(),
                relative_path=str(row["relative_path"]).strip(),
                sha256=str(row["sha256"]).strip(),
                source_root_id=str(row["source_root_id"]).strip(),
            )
            _validate_asset_shape(asset)
            if asset.asset_id in by_id or asset.relative_path in by_path:
                raise ValueError("Registry contains a duplicate asset ID or path")
            revision = revision_by_id.get(asset.revision_id)
            if revision is None or revision.document_logical_id != asset.document_logical_id:
                raise ValueError("asset Revision is not controlled for its logical document")
            by_id[asset.asset_id] = asset
            by_path[asset.relative_path] = asset.asset_id
            assets.append(asset)

    if not assets:
        raise ValueError("Registry asset catalog is empty")
    if derived_links_path is not None:
        derived_relations = _load_derived_relations(derived_links_path)
        for derived_path, source_path in derived_relations:
            derived = _asset_by_path(by_path, by_id, derived_path)
            source = _asset_by_path(by_path, by_id, source_path)
            if derived.asset_kind != "derived_ocr":
                raise ValueError("derived asset link must point to an OCR-derived asset")
            if (derived.document_logical_id, derived.revision_id) != (source.document_logical_id, source.revision_id):
                raise ValueError("derived asset and source asset must share document and Revision")
            if derived.derived_from_asset_id is not None:
                raise ValueError("derived relation is declared more than once")
            values = {
                field: getattr(derived, field)
                for field in AssetIdentity.__dataclass_fields__
            }
            values.update(
                derived_from_asset_id=source.asset_id,
                derivation_type="ocr_derivative",
            )
            replacement = AssetIdentity(**values)
            by_id[derived.asset_id] = replacement
            assets[assets.index(derived)] = replacement

    return IdentityCatalog(tuple(assets), revisions, dict(by_path))


def _validate_asset_shape(asset: AssetIdentity) -> None:
    for value, pattern, label in (
        (asset.asset_id, _ID_PATTERNS["asset"], "asset_id"),
        (asset.document_logical_id, _ID_PATTERNS["document"], "document_logical_id"),
        (asset.revision_id, _ID_PATTERNS["revision"], "revision_id"),
    ):
        if not pattern.fullmatch(value):
            raise ValueError(f"invalid {label}: {value!r}")
    if not asset.relative_path or not asset.sha256 or not asset.source_root_id:
        raise ValueError("asset path, sha256 and source root are required")


def _asset_by_path(by_path: dict[str, str], by_id: dict[str, AssetIdentity], path: str) -> AssetIdentity:
    try:
        return by_id[by_path[path]]
    except KeyError as exc:
        raise ValueError(f"derived relation references an unregistered path: {path}") from exc


def _load_derived_relations(path: Path) -> tuple[tuple[str, str], ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [
            row
            for row in csv.DictReader(
                (line for line in handle if not line.startswith("#")), delimiter="\t"
            )
        ]
    required = {"derived_relative_path", "source_relative_path"}
    if not rows or not required <= set(rows[0]):
        raise ValueError("derived asset relation file is missing required columns")
    relations = []
    seen: set[str] = set()
    for row in rows:
        derived = row["derived_relative_path"].strip()
        source = row["source_relative_path"].strip()
        if not derived or not source or derived in seen:
            raise ValueError("derived asset relation contains an empty or duplicate path")
        seen.add(derived)
        relations.append((derived, source))
    return tuple(relations)

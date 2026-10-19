"""Stable keys and pure diff helpers for one-version graph updates.

The graph stores the current knowledge only.  A source update is therefore
matched by a stable logical key, while a content fingerprint decides whether
the matched record needs to be written.  The key deliberately excludes the
current extraction run identifier.
"""
from __future__ import annotations

from copy import deepcopy

from turbine_kg.runtime import fingerprint, json_text


def _locator_key(locator: dict | None) -> tuple:
    locator = locator or {}
    return (
        locator.get("physical_page"),
        locator.get("logical_page"),
        tuple(locator.get("char_range") or ()),
        tuple(locator.get("bbox") or ()),
    )


def evidence_logical_key(row: dict) -> str:
    """Return a source-location key that survives re-extraction."""
    members = []
    for member in row.get("members") or []:
        members.append((
            member.get("source_id"),
            member.get("role"),
            _locator_key(member.get("locator")),
        ))
    context = row.get("context") or {}
    return "evidence:" + fingerprint((
        row.get("document_id"), row.get("kind"), tuple(sorted(members)),
        context.get("table_id"), context.get("picture_id"),
    ))


def statement_logical_key(row: dict, evidence_keys: dict[str, str] | None = None) -> str:
    """Identify an assertion, not just the source shared by several assertions.

    A source member can contain multiple independent requirements. Without the
    assertion discriminator an incremental import silently collapses these.
    Explicit edits keep their node id; changed extraction wording is reconciled
    as a replacement within its source component, never guessed to be the same
    assertion merely because its source matches.
    """
    evidence_keys = evidence_keys or {}
    supports = []
    for support in row.get("supports") or []:
        evidence_id = support.get("evidence_id")
        supports.append((
            evidence_keys.get(evidence_id, evidence_id),
            tuple(sorted(support.get("member_keys") or ())),
        ))
    anchors = tuple(sorted(json_text(anchor) for anchor in row.get("source_anchors") or ()))
    return "statement:" + fingerprint((
        row.get("document_id"), row.get("type"), tuple(sorted(supports)), anchors,
        " ".join((row.get("text") or "").split()),
    ))


def node_logical_key(label: str, row: dict, evidence_keys: dict[str, str] | None = None) -> str:
    """Select the stable key for a managed graph node."""
    # Recompute assertion keys to repair the former source-only keys, which
    # collided whenever several facts shared a paragraph.
    if label == "Statement":
        return statement_logical_key(row, evidence_keys)
    explicit = row.get("logical_key")
    if isinstance(explicit, str) and explicit:
        return explicit
    if label == "Evidence":
        return evidence_logical_key(row)
    if row.get("document_id"):
        return f"{label.lower()}:{fingerprint((row.get('document_id'), row.get('physical_page'), row.get('id')))}"
    raise ValueError(f"{label} 缺少稳定 logical_key")


def content_fingerprint(row: dict) -> str:
    """Fingerprint mutable content without the storage identity fields."""
    ignored = {"id", "logical_key", "content_hash", "updated_at",
               "search_tokens", "search_projection_version"}
    return fingerprint({key: value for key, value in row.items() if key not in ignored})


def build_delta(existing: list[dict], incoming: list[dict], *, label: str,
                evidence_keys: dict[str, str] | None = None) -> dict:
    """Compare one managed node label without touching a database."""
    def keyed(rows):
        result = {}
        for row in rows:
            key = node_logical_key(label, row, evidence_keys)
            if key in result:
                raise ValueError(f"{label} 出现重复稳定标识：{key}")
            result[key] = {**deepcopy(row), "logical_key": key}
        return result

    before, after = keyed(existing), keyed(incoming)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(key for key in set(before) & set(after)
                     if content_fingerprint(before[key]) != content_fingerprint(after[key]))
    unchanged = sorted(set(before) & set(after) - set(changed))
    return {
        "label": label,
        "added": [after[key] for key in added],
        "changed": [after[key] for key in changed],
        "removed": [before[key] for key in removed],
        "unchanged": [before[key] for key in unchanged],
    }

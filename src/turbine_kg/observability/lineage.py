"""Small, reusable file-lineage primitives for stage artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_ref(root: Path, relative_path: str) -> dict[str, str]:
    path = root / relative_path
    if not path.is_file():
        raise FileNotFoundError(relative_path)
    return {"path": relative_path, "sha256": sha256_file(path)}


def verify_input_hashes(root: Path, references: Mapping[str, Any]) -> list[dict[str, str]]:
    """Return blocking mismatches for persisted path/hash references."""
    failures: list[dict[str, str]] = []
    for name, reference in references.items():
        if not isinstance(reference, Mapping) or not reference.get("path") or not reference.get("sha256"):
            failures.append({"name": name, "reason": "invalid_lineage_reference"})
            continue
        path = root / str(reference["path"])
        if not path.is_file():
            failures.append({"name": name, "path": str(reference["path"]), "reason": "missing_artifact"})
            continue
        current = sha256_file(path)
        if current != reference["sha256"]:
            failures.append({"name": name, "path": str(reference["path"]), "expected": str(reference["sha256"]), "current": current, "reason": "stale_artifact"})
    return failures

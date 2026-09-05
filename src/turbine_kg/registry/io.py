"""Small, explicit persistence helpers for Registry inputs and outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def jsonl_write(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def load_manual_findings(path: Path) -> dict[str, dict[str, Any]]:
    """Load reviewed findings without copying source text into the Registry."""
    if not path.exists():
        return {}
    findings: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        relative_path = record.get("relative_path")
        if not isinstance(relative_path, str) or relative_path in findings:
            raise ValueError("manual findings must have one unique relative_path per record")
        findings[relative_path] = record
    return findings

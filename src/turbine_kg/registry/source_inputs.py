"""Allowlist parsing and source-file integrity helpers."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_allowlist(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    metadata: dict[str, str] = {}
    data_lines: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            key, separator, value = line[1:].strip().partition("=")
            if separator:
                metadata[key.strip()] = value.strip()
            continue
        data_lines.append(raw_line)
    reader = csv.DictReader(data_lines, delimiter="\t")
    expected_fields = ["sha256", "size_bytes", "path"]
    if reader.fieldnames != expected_fields:
        raise ValueError(f"allowlist fields must be {expected_fields}, got {reader.fieldnames}")
    return metadata, list(reader)

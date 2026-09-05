"""Allowlist parsing and source-file integrity helpers."""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path, PurePosixPath


DERIVED_OCR_PREFIX = "OCR/"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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


def asset_root_id(relative_path: str) -> str:
    return "ocr_derived" if relative_path.startswith(DERIVED_OCR_PREFIX) else "source"


def resolve_allowlisted_path(relative_path: str, source_root: Path, ocr_derived_root: Path) -> Path:
    parts = PurePosixPath(relative_path)
    if parts.is_absolute() or ".." in parts.parts:
        raise ValueError(f"allowlist path escapes its configured root: {relative_path}")
    if asset_root_id(relative_path) == "ocr_derived":
        remainder = relative_path.removeprefix(DERIVED_OCR_PREFIX)
        if not remainder:
            raise ValueError("derived OCR path must include a file name")
        root, parts = ocr_derived_root.resolve(), PurePosixPath(remainder)
    else:
        root = source_root.resolve()
    candidate = (root / Path(*parts.parts)).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"allowlist path escapes its configured root: {relative_path}")
    return candidate


def validate_source_allowlist(
    *, source_root: Path, ocr_derived_root: Path, allowlist_path: Path
) -> list[str]:
    """Validate only declared source assets, resolved through their configured roots."""
    resolved_source_root = source_root.resolve()
    resolved_ocr_root = ocr_derived_root.resolve()
    if resolved_ocr_root == resolved_source_root or resolved_ocr_root.is_relative_to(resolved_source_root):
        return ["OCR_DERIVED_ROOT must be outside SOURCE_ROOT"]
    metadata, entries = load_allowlist(allowlist_path)
    errors: list[str] = []
    try:
        expected_count = int(metadata["expected_count"])
    except (KeyError, ValueError):
        errors.append("metadata expected_count must be an integer")
        expected_count = -1
    if len(entries) != expected_count:
        errors.append(f"allowlist count mismatch: expected {expected_count}, got {len(entries)}")

    seen_paths: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        relative_path = entry["path"]
        label = f"entry {index} ({relative_path})"
        if relative_path in seen_paths:
            errors.append(f"{label}: duplicate path")
        seen_paths.add(relative_path)
        if not relative_path.lower().endswith(".pdf"):
            errors.append(f"{label}: only PDF files are allowed")
        if not SHA256_PATTERN.fullmatch(entry["sha256"]):
            errors.append(f"{label}: invalid SHA-256")
            continue
        try:
            expected_size = int(entry["size_bytes"])
        except ValueError:
            errors.append(f"{label}: size_bytes must be an integer")
            continue
        try:
            candidate = resolve_allowlisted_path(relative_path, source_root, ocr_derived_root)
        except ValueError as error:
            errors.append(f"{label}: {error}")
            continue
        if not candidate.is_file():
            errors.append(f"{label}: file does not exist")
            continue
        if candidate.stat().st_size != expected_size:
            errors.append(f"{label}: size mismatch, expected {expected_size}, got {candidate.stat().st_size}")
            continue
        if sha256_file(candidate) != entry["sha256"]:
            errors.append(f"{label}: SHA-256 mismatch")
    return errors

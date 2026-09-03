from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import sys
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = PROJECT_ROOT / "config" / "source_allowlist.tsv"
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
        raise ValueError(
            f"allowlist fields must be {expected_fields}, got {reader.fieldnames}"
        )
    return metadata, list(reader)


def validate_source_allowlist(source_root: Path) -> list[str]:
    metadata, entries = load_allowlist(ALLOWLIST_PATH)
    errors: list[str] = []

    try:
        expected_count = int(metadata["expected_count"])
    except (KeyError, ValueError):
        errors.append("metadata expected_count must be an integer")
        expected_count = -1

    if len(entries) != expected_count:
        errors.append(
            f"allowlist count mismatch: expected {expected_count}, got {len(entries)}"
        )

    resolved_root = source_root.resolve()
    if not resolved_root.is_dir():
        return errors + [f"SOURCE_ROOT does not exist: {resolved_root}"]

    seen_paths: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        relative_text = entry["path"]
        relative_path = PurePosixPath(relative_text)
        label = f"entry {index} ({relative_text})"

        if relative_path.is_absolute() or ".." in relative_path.parts:
            errors.append(f"{label}: path must stay inside SOURCE_ROOT")
            continue
        if relative_path.suffix.lower() != ".pdf":
            errors.append(f"{label}: only PDF files are allowed")
        if relative_text in seen_paths:
            errors.append(f"{label}: duplicate path")
        seen_paths.add(relative_text)

        expected_hash = entry["sha256"]
        if not SHA256_PATTERN.fullmatch(expected_hash):
            errors.append(f"{label}: invalid SHA-256")
            continue

        try:
            expected_size = int(entry["size_bytes"])
        except ValueError:
            errors.append(f"{label}: size_bytes must be an integer")
            continue

        candidate = (resolved_root / Path(*relative_path.parts)).resolve()
        if not candidate.is_relative_to(resolved_root):
            errors.append(f"{label}: resolved path escapes SOURCE_ROOT")
            continue
        if not candidate.is_file():
            errors.append(f"{label}: file does not exist")
            continue
        if candidate.stat().st_size != expected_size:
            errors.append(
                f"{label}: size mismatch, expected {expected_size}, "
                f"got {candidate.stat().st_size}"
            )
            continue

        actual_hash = sha256_file(candidate)
        if actual_hash != expected_hash:
            errors.append(
                f"{label}: SHA-256 mismatch, expected {expected_hash}, got {actual_hash}"
            )

    return errors


def main() -> int:
    default_source_root = Path(
        os.environ.get("SOURCE_ROOT", PROJECT_ROOT.parent / "Original materials")
    )
    parser = argparse.ArgumentParser(
        description="Verify every explicitly allowed source PDF without scanning the workspace."
    )
    parser.add_argument("--source-root", type=Path, default=default_source_root)
    args = parser.parse_args()

    errors = validate_source_allowlist(args.source_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    _, entries = load_allowlist(ALLOWLIST_PATH)
    print(f"source allowlist verified: {len(entries)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

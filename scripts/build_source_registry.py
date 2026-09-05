"""CLI entry point for the metadata-only Source Registry builder."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from turbine_kg.registry.builder import DEFAULT_SOURCE_ROOT, build_registry


def main() -> int:
    source_root = Path(os.environ.get("SOURCE_ROOT", DEFAULT_SOURCE_ROOT))
    try:
        summary = build_registry(source_root)
    except Exception as error:  # pragma: no cover - command-line diagnostics
        print(f"ERROR: {error}")
        return 1
    print(
        "source registry built: "
        f"{summary['physical_assets']} physical assets, "
        f"{summary['logical_documents']} logical documents, "
        f"{summary['duplicate_groups']} duplicate groups, "
        f"{summary['duplicate_relations']} duplicate relations, "
        f"{summary['review_items']} review items"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

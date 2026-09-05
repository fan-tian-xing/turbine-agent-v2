from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from turbine_kg.registry.source_inputs import load_allowlist, validate_source_allowlist
from turbine_kg.settings import Settings


ALLOWLIST_PATH = PROJECT_ROOT / "config" / "source_allowlist.tsv"
def main() -> int:
    settings = Settings.from_environment()
    parser = argparse.ArgumentParser(
        description="Verify every explicitly allowed source PDF without scanning the workspace."
    )
    parser.add_argument("--source-root", type=Path, default=settings.source_root)
    parser.add_argument("--ocr-derived-root", type=Path, default=settings.ocr_derived_root)
    args = parser.parse_args()

    errors = validate_source_allowlist(
        source_root=args.source_root,
        ocr_derived_root=args.ocr_derived_root,
        allowlist_path=ALLOWLIST_PATH,
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    _, entries = load_allowlist(ALLOWLIST_PATH)
    print(f"source allowlist verified: {len(entries)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

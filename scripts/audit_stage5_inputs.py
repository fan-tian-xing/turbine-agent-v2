"""Audit Stage 5 input completeness and freeze the selected sample boundary."""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path

import pymupdf

from turbine_kg.settings import PROJECT_ROOT, Settings


SAMPLE_MANIFEST = PROJECT_ROOT / "data" / "stage5" / "stage5_sample_manifest.json"
REGISTRY_ASSETS = PROJECT_ROOT / "data" / "registry" / "source_assets.jsonl"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_assets() -> dict[str, dict]:
    return {
        row["asset_id"]: row
        for line in REGISTRY_ASSETS.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for row in (json.loads(line),)
    }


def _resolve(asset: dict, settings: Settings) -> Path:
    if asset["source_root_id"] == "source":
        return settings.source_root / asset["relative_path"]
    if asset["source_root_id"] == "ocr_derived" and asset["relative_path"].startswith("OCR/"):
        return settings.ocr_derived_root / asset["relative_path"].removeprefix("OCR/")
    raise ValueError(f"unsupported asset root/path: {asset['asset_id']}")


def _page_summary(path: Path) -> dict:
    document = pymupdf.open(path)
    rotations: dict[str, int] = {}
    sizes: dict[str, int] = {}
    for page in document:
        rotations[str(page.rotation)] = rotations.get(str(page.rotation), 0) + 1
        size = f"{page.rect.width:.2f}x{page.rect.height:.2f}"
        sizes[size] = sizes.get(size, 0) + 1
    return {
        "page_count": len(document),
        "rotation_counts": rotations,
        "page_size_counts": sizes,
    }


def audit() -> dict:
    settings = Settings.from_environment()
    sample = json.loads(SAMPLE_MANIFEST.read_text(encoding="utf-8"))
    assets = _load_assets()
    documents = []
    errors: list[str] = []
    seen_pages: set[tuple[str, int]] = set()

    for item in sample["documents"]:
        processing = assets[item["processing_asset_id"]]
        original = assets[item["original_asset_id"]]
        processing_path = _resolve(processing, settings)
        original_path = _resolve(original, settings)
        if not processing_path.is_file():
            errors.append(f"missing processing PDF: {processing_path}")
        if not original_path.is_file():
            errors.append(f"missing original PDF: {original_path}")
        processing_summary = _page_summary(processing_path) if processing_path.is_file() else {"page_count": 0}
        original_summary = _page_summary(original_path) if original_path.is_file() else {"page_count": 0}
        expected = int(item["page_count"])
        if processing_summary["page_count"] != expected:
            errors.append(f"{item['document_key']} processing page count differs: {processing_summary['page_count']} != {expected}")
        if original_summary["page_count"] != expected:
            errors.append(f"{item['document_key']} original page count differs: {original_summary['page_count']} != {expected}")
        sample_pages = [int(page["pdf_page"]) for page in item["sample_pages"]]
        if len(sample_pages) != len(set(sample_pages)):
            errors.append(f"{item['document_key']} sample pages contain duplicates")
        for page in sample_pages:
            if not 1 <= page <= expected:
                errors.append(f"{item['document_key']} sample page out of range: {page}")
            seen_pages.add((item["document_key"], page))
        documents.append({
            "document_key": item["document_key"],
            "document_logical_id": item["document_logical_id"],
            "processing_asset_id": processing["asset_id"],
            "original_asset_id": original["asset_id"],
            "processing_path": processing["relative_path"],
            "original_path": original["relative_path"],
            "expected_page_count": expected,
            "processing": {
                **processing_summary,
                "sha256": _sha256(processing_path) if processing_path.is_file() else None,
                "registry_sha256": processing.get("sha256"),
                "sha256_matches_registry": processing_path.is_file() and _sha256(processing_path) == processing.get("sha256"),
            },
            "original": {
                **original_summary,
                "sha256": _sha256(original_path) if original_path.is_file() else None,
                "registry_sha256": original.get("sha256"),
                "sha256_matches_registry": original_path.is_file() and _sha256(original_path) == original.get("sha256"),
            },
            "sample_page_count": len(sample_pages),
        })

    expected_sample_count = int(sample["sample_page_count"])
    if len(seen_pages) != expected_sample_count:
        errors.append(f"sample page count differs: {len(seen_pages)} != {expected_sample_count}")
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_input_audit",
        "audited_at": date.today().isoformat(),
        "scope": sample["scope"],
        "sample_manifest": str(SAMPLE_MANIFEST.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "sample_page_count": len(seen_pages),
        "expected_sample_page_count": expected_sample_count,
        "documents": documents,
        "errors": errors,
        "status": "pass" if not errors else "fail",
        "conclusion": (
            "All five selected original PDFs and their processing assets are present, page-complete, hash-consistent, and within the frozen Stage 5 sample boundary."
            if not errors
            else "Stage 5 input audit has blocking discrepancies; do not start OCR benchmarking."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "stage5" / f"stage5_input_audit_{date.today().isoformat()}.json",
    )
    args = parser.parse_args()
    result = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

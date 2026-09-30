"""Validate the 36 original-page repaired source records before Evidence use."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pymupdf

from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
STAGE6 = ROOT / "data/stage6"
REPAIRED = (
    "repaired_source_standards.json",
    "repaired_source_d300n_haf103.json",
    "repaired_source_auxiliary.json",
)
REVIEWS = (
    "ocr_review_standards.json",
    "ocr_review_d300n_haf103.json",
    "ocr_review_auxiliary.json",
)


def _load(name: str) -> dict:
    return json.loads((STAGE6 / name).read_text(encoding="utf-8"))


def _bbox(value: object, *, width: float, height: float) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    try:
        x0, y0, x1, y1 = map(float, value)
    except (TypeError, ValueError):
        return False
    return 0 <= x0 < x1 <= width + 0.5 and 0 <= y0 < y1 <= height + 0.5


def validate_repaired_pages() -> dict:
    golden = _load("stage6_evidence_golden_sample.json")
    expected = {
        (row["document_key"], int(row["physical_page"])): row
        for row in golden["records"]
    }
    reviews = [row for name in REVIEWS for row in _load(name)["pages"]]
    review_by_page = {(row["document_key"], int(row["physical_page"])): row for row in reviews}
    repaired = [row for name in REPAIRED for row in _load(name)["pages"]]
    keys = [(row["document_key"], int(row["physical_page"])) for row in repaired]
    errors: list[str] = []
    if len(reviews) != 36 or set(review_by_page) != set(expected):
        errors.append("ocr_review_page_scope")
    if len(keys) != 36 or len(set(keys)) != 36 or set(keys) != set(expected):
        errors.append("repaired_page_scope")

    assets = {
        row["asset_id"]: row
        for line in (ROOT / "data/registry/source_assets.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
        for row in (json.loads(line),)
    }
    settings = Settings.from_environment()
    source_paths = {
        key: settings.source_root / assets[sample["original_asset_id"]]["relative_path"]
        for (key, _), sample in expected.items()
    }
    source_sha = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in source_paths.items()}
    source_pdf = {key: pymupdf.open(path) for key, path in source_paths.items()}
    region_counts: Counter[str] = Counter()
    try:
        for row in repaired:
            key = (row["document_key"], int(row["physical_page"]))
            if key not in expected:
                continue
            document_key, page_number = key
            review = review_by_page.get(key)
            if not review or review["ocr_text_status"] not in {"verified", "corrected"} or review["nontext_status"] not in {"none", "region_recorded"}:
                errors.append(f"review_not_resolved:{key}")
            if row.get("original_pdf_sha256") != source_sha[document_key]:
                errors.append(f"source_hash:{key}")
            pdf = source_pdf[document_key]
            if not 1 <= page_number <= len(pdf):
                errors.append(f"source_page_missing:{key}")
                continue
            rect = pdf[page_number - 1].rect
            for field in ("text_runs", "table_regions", "image_regions", "excluded_regions"):
                regions = row.get(field)
                if not isinstance(regions, list):
                    errors.append(f"missing_region_list:{key}:{field}")
                    continue
                region_counts[field] += len(regions)
                for index, region in enumerate(regions):
                    if not isinstance(region, dict) or not _bbox(region.get("bbox_pdf_pt"), width=rect.width, height=rect.height):
                        errors.append(f"invalid_bbox:{key}:{field}:{index}")
                        continue
                    if field == "text_runs" and not str(region.get("text", "")).strip():
                        errors.append(f"empty_text:{key}:{index}")
                    if field == "excluded_regions" and not region.get("reason"):
                        errors.append(f"unreasoned_exclusion:{key}:{index}")
            if not any(row.get(field) for field in ("text_runs", "table_regions", "image_regions", "excluded_regions")):
                eligibility = expected[key]["evidence_eligibility"]
                if eligibility not in {"navigation_only", "boundary_only"}:
                    errors.append(f"unrepresented_page:{key}")
    finally:
        for pdf in source_pdf.values():
            pdf.close()
    return {
        "status": "complete" if not errors else "blocked",
        "page_count": len(keys),
        "unique_page_count": len(set(keys)),
        "region_counts": dict(region_counts),
        "errors": errors,
    }


def main() -> None:
    result = validate_repaired_pages()
    print(json.dumps(result, ensure_ascii=False))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

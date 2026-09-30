"""Audit the current 775-page Stage 5 delivery.

This gate verifies file identity, page order, visible-page preservation, searchable
text and a review record bound to the current bytes. It cannot infer OCR accuracy
from machine checks; the line and table review requires an explicit visual review record.
"""

from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path

import pymupdf

from turbine_kg.settings import PROJECT_ROOT, Settings


MANIFEST = PROJECT_ROOT / "data" / "stage5" / "stage5_sample_manifest.json"
REGISTRY = PROJECT_ROOT / "data" / "registry" / "source_assets.jsonl"
REVIEW = PROJECT_ROOT / "data" / "registry" / "ocr_validation_report.json"
EXIT_AUDIT = PROJECT_ROOT / "data" / "stage5" / "stage5_exit_audit.json"
EXPECTED_DOCUMENTS = 5
EXPECTED_PAGES = 775
EXPECTED_DERIVED = 5
EXPECTED_SCANNED_PAGES = 602
EXPECTED_NATIVE_PAGES = 173
UNRESOLVED_FIELDS = (
    "unresolved_text_count",
    "unresolved_table_cell_count",
    "unresolved_page_mapping_count",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _asset_path(asset: dict, settings: Settings) -> Path:
    root = asset.get("source_root_id")
    relative = str(asset.get("relative_path", ""))
    if root == "source":
        return settings.source_root / relative
    if root == "ocr_derived" and relative.startswith("OCR/"):
        return settings.ocr_derived_root / relative[4:]
    raise ValueError(f"unknown asset root/path: {root!r} {relative!r}")


def _reviewed_pages(ranges: object, page_count: int) -> bool:
    if not isinstance(ranges, list):
        return False
    covered: set[int] = set()
    for item in ranges:
        if not isinstance(item, list) or len(item) != 2:
            return False
        first, last = item
        if type(first) is not int or type(last) is not int:
            return False
        if first < 1 or last > page_count or first > last:
            return False
        current = set(range(first, last + 1))
        if covered & current:
            return False
        covered.update(current)
    return covered == set(range(1, page_count + 1))


def _page_numbers(value: object, page_count: int) -> set[int] | None:
    if not isinstance(value, list) or any(
        not isinstance(number, int) or number < 1 or number > page_count
        for number in value
    ):
        return None
    if len(value) != len(set(value)):
        return None
    return set(value)


def _current_user_acceptance(
    value: object, original_sha: str, processing_sha: str, page_count: int
) -> bool:
    """A user's explicit outcome acceptance is distinct from an agent's review method."""
    return (
        isinstance(value, dict)
        and value.get("accepted") is True
        and value.get("acceptance_kind") == "user_confirmation_of_current_ocr_delivery"
        and isinstance(value.get("confirmation_text"), str)
        and bool(value["confirmation_text"].strip())
        and value.get("original_sha256") == original_sha
        and value.get("processing_sha256") == processing_sha
        and _reviewed_pages(value.get("accepted_page_ranges"), page_count)
        and value.get("does_not_assert_agent_line_by_line_or_cell_review") is True
    )


def _same_visible_page(original: pymupdf.Page, processed: pymupdf.Page) -> bool:
    # Compare pixels, not PDF objects: hidden OCR text may change the PDF bytes.
    if original.rect != processed.rect:
        return False
    left = original.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
    right = processed.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
    return (
        left.width == right.width
        and left.height == right.height
        and left.samples == right.samples
    )


def audit(
    manifest_path: Path = MANIFEST,
    registry_path: Path = REGISTRY,
    review_path: Path = REVIEW,
    settings: Settings | None = None,
) -> dict:
    settings = settings or Settings.from_environment()
    issues: list[str] = []
    documents: list[dict] = []
    result = {
        "schema_version": 2,
        "stage": "5",
        "artifact_kind": "stage5_exit_audit",
        "audited_at": date.today().isoformat(),
        "status": "blocked",
        "next_stage_allowed": False,
        "scope": {
            "expected_documents": EXPECTED_DOCUMENTS,
            "expected_existing_physical_pages": EXPECTED_PAGES,
            "expected_derived_pdfs": EXPECTED_DERIVED,
            "expected_scanned_pages": EXPECTED_SCANNED_PAGES,
            "expected_native_pages": EXPECTED_NATIVE_PAGES,
        },
        "documents": documents,
        "blocking_items": issues,
        "automatic_checks_do_not_prove_ocr_accuracy": True,
    }
    for label, path in (
        ("manifest", manifest_path),
        ("Registry", registry_path),
        ("full-page review", review_path),
    ):
        if not path.is_file():
            issues.append(f"{label}: missing {path}")
    if issues:
        return result
    try:
        manifest = _load_json(manifest_path)
        registry_rows = [
            json.loads(line)
            for line in registry_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        report = _load_json(review_path)
    except (OSError, ValueError, TypeError) as exc:
        issues.append(f"input file cannot be read: {exc}")
        return result

    items = manifest.get("documents", [])
    full_scope = manifest.get("full_page_processing", {})
    if len(items) != EXPECTED_DOCUMENTS:
        issues.append(f"manifest document count: {len(items)} != {EXPECTED_DOCUMENTS}")
    if len({item.get("document_key") for item in items}) != len(items):
        issues.append("manifest document keys are duplicated")
    page_total = sum(int(item.get("page_count", 0)) for item in items)
    if page_total != EXPECTED_PAGES:
        issues.append(f"manifest physical page total: {page_total} != {EXPECTED_PAGES}")
    for field, expected in (
        ("total_existing_physical_pages", EXPECTED_PAGES),
        ("scanned_page_count", EXPECTED_SCANNED_PAGES),
        ("native_text_page_count", EXPECTED_NATIVE_PAGES),
    ):
        if full_scope.get(field) != expected:
            issues.append(f"manifest {field}: {full_scope.get(field)!r} != {expected}")
    derived_count = sum(
        str(item.get("processing_relative_path", "")).startswith("OCR/")
        for item in items
    )
    if derived_count != EXPECTED_DERIVED:
        issues.append(f"derived PDF count: {derived_count} != {EXPECTED_DERIVED}")
    scanned_keys = full_scope.get("scanned_documents")
    native_keys = full_scope.get("native_text_documents")
    if not isinstance(scanned_keys, list) or not isinstance(native_keys, list):
        issues.append("manifest scanned/native document classification is missing")
    else:
        scoped_keys = {item.get("document_key") for item in items}
        if (
            set(scanned_keys) & set(native_keys)
            or set(scanned_keys) | set(native_keys) != scoped_keys
            or len(scanned_keys) + len(native_keys) != len(items)
        ):
            issues.append("manifest scanned/native document classification is inconsistent")
        page_counts = {item.get("document_key"): int(item.get("page_count", 0)) for item in items}
        if sum(page_counts.get(key, 0) for key in scanned_keys) != EXPECTED_SCANNED_PAGES:
            issues.append("manifest scanned document page sum is incorrect")
        if sum(page_counts.get(key, 0) for key in native_keys) != EXPECTED_NATIVE_PAGES:
            issues.append("manifest native document page sum is incorrect")

    assets = {row.get("asset_id"): row for row in registry_rows}
    reviews = report.get("full_corpus_reviews")
    if not isinstance(reviews, list):
        reviews = []
        issues.append("full_corpus_reviews missing; historical sample reviews cannot close the gate")
    review_map = {row.get("document_key"): row for row in reviews if isinstance(row, dict)}
    if len(review_map) != len(reviews):
        issues.append("full_corpus_reviews contains duplicate or invalid document keys")
    if set(review_map) != {item.get("document_key") for item in items}:
        issues.append("full_corpus_reviews document set differs from manifest")

    for item in items:
        key = str(item.get("document_key", ""))
        expected_pages = int(item.get("page_count", 0))
        source_asset = assets.get(item.get("original_asset_id"))
        processed_asset = assets.get(item.get("processing_asset_id"))
        entry = {
            "document_key": key,
            "expected_pages": expected_pages,
            "original_sha256": None,
            "processing_sha256": None,
            "pixel_mismatch_pages": [],
            "unsearchable_pages": [],
            "issues": [],
        }
        documents.append(entry)
        local = entry["issues"]
        if source_asset is None or processed_asset is None:
            local.append("source or processed asset missing from Registry")
            issues.append(f"{key}: missing Registry asset")
            continue
        try:
            source_path = _asset_path(source_asset, settings)
            processed_path = _asset_path(processed_asset, settings)
        except ValueError as exc:
            local.append(str(exc))
            issues.append(f"{key}: invalid Registry asset path")
            continue
        if item.get("original_relative_path", item.get("processing_relative_path")) != source_asset.get("relative_path"):
            local.append("manifest original path differs from Registry")
        if item.get("processing_relative_path") != processed_asset.get("relative_path"):
            local.append("manifest processed path differs from Registry")
        if not source_path.is_file():
            local.append(f"original PDF missing: {source_path}")
        if not processed_path.is_file():
            local.append(f"processed PDF missing: {processed_path}")
        if local:
            issues.extend(f"{key}: {message}" for message in local)
            continue

        original_sha = _sha256(source_path)
        processing_sha = _sha256(processed_path)
        entry["original_sha256"] = original_sha
        entry["processing_sha256"] = processing_sha
        if original_sha != source_asset.get("sha256"):
            local.append("original SHA-256 differs from current Registry")
        if processing_sha != processed_asset.get("sha256"):
            local.append("processed SHA-256 differs from current Registry")
        review = review_map.get(key)
        if review is None:
            local.append("current full-page manual review missing")
            review = {}
        else:
            if review.get("original_sha256") != original_sha:
                local.append("manual review original SHA-256 is stale")
            if review.get("processing_sha256") != processing_sha:
                local.append("manual review processed SHA-256 is stale")
            if not _reviewed_pages(review.get("reviewed_page_ranges"), expected_pages):
                local.append("manual line/table review does not cover every physical page")
            acceptance = review.get("user_acceptance")
            user_accepted = _current_user_acceptance(
                acceptance, original_sha, processing_sha, expected_pages
            )
            if acceptance is not None and not user_accepted:
                local.append("user acceptance is not bound to this exact complete PDF")
            entry["review_basis"] = (
                "explicit_current_pdf_user_acceptance"
                if user_accepted else "agent_line_table_and_order_review"
            )
            if user_accepted:
                entry["user_acceptance"] = {
                    "confirmation_text": acceptance["confirmation_text"],
                    "accepted_at": acceptance.get("accepted_at"),
                    "accepted_page_ranges": acceptance["accepted_page_ranges"],
                    "processing_sha256": processing_sha,
                    "agent_line_by_line_reviewed": review.get("line_by_line_reviewed"),
                    "agent_table_cells_reviewed": review.get("table_cells_reviewed"),
                    "agent_reading_order_reviewed": review.get("reading_order_reviewed"),
                }
            if not user_accepted:
                if review.get("line_by_line_reviewed") is not True:
                    local.append("line-by-line review not confirmed")
                if review.get("table_cells_reviewed") is not True:
                    local.append("table-cell review not confirmed")
                if review.get("reading_order_reviewed") is not True:
                    local.append("reading-order review not confirmed")
                for field in UNRESOLVED_FIELDS:
                    if review.get(field) != 0:
                        local.append(f"{field} is not zero")
        blank = _page_numbers(review.get("blank_pages"), expected_pages)
        unreadable = _page_numbers(review.get("source_unreadable_pages"), expected_pages)
        if blank is None or unreadable is None:
            local.append("blank/source-unreadable page lists missing or invalid")
            blank, unreadable = set(), set()
        if blank & unreadable:
            local.append("same page marked both blank and source-unreadable")
        try:
            with pymupdf.open(source_path) as original, pymupdf.open(processed_path) as processed:
                if len(original) != expected_pages:
                    local.append(f"original page count {len(original)} != {expected_pages}")
                if len(processed) != expected_pages:
                    local.append(f"processed page count {len(processed)} != {expected_pages}")
                for index in range(min(len(original), len(processed), expected_pages)):
                    number = index + 1
                    if source_path != processed_path and not _same_visible_page(
                        original[index], processed[index]
                    ):
                        entry["pixel_mismatch_pages"].append(number)
                    if not processed[index].get_text().strip() and number not in blank | unreadable:
                        entry["unsearchable_pages"].append(number)
        except (OSError, ValueError, RuntimeError) as exc:
            local.append(f"PDF read/render failure: {exc}")
        if entry["pixel_mismatch_pages"]:
            local.append(f"visible page mismatch: {entry['pixel_mismatch_pages']}")
        if entry["unsearchable_pages"]:
            local.append(f"no extractable text: {entry['unsearchable_pages']}")
        issues.extend(f"{key}: {message}" for message in local)

    result["status"] = "complete" if not issues else "blocked"
    result["next_stage_allowed"] = not issues
    return result


def main() -> int:
    result = audit()
    EXIT_AUDIT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": result["status"], "output": str(EXIT_AUDIT)}, ensure_ascii=False))
    return 0 if result["next_stage_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

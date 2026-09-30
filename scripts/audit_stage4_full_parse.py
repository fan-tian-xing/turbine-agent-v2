"""Run a full-page Stage 4 Document IR audit for the current five registered OCRs.

This checks structural processing only.  It does not grade OCR accuracy or
recover table cells; those remain Stage 5 responsibilities.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import date
import hashlib
import json
from pathlib import Path
import sys

import pymupdf

from turbine_kg.documents.catalog import IdentityCatalog, load_identity_catalog
from turbine_kg.documents.models import record_value
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.documents.profiles import LayoutProfile, load_layout_profile
from turbine_kg.registry.source_inputs import sha256_file
from turbine_kg.settings import PROJECT_ROOT, Settings


REGISTRY_ASSETS = PROJECT_ROOT / "data" / "registry" / "source_assets.jsonl"
REVISION_CATALOG = PROJECT_ROOT / "config" / "revision_identity.tsv"
DERIVED_LINKS = PROJECT_ROOT / "config" / "derived_asset_links.tsv"
MANIFEST = PROJECT_ROOT / "data" / "stage3" / "initial_batch_manifest.json"
CURRENT_MANIFEST = PROJECT_ROOT / "data" / "stage5" / "stage5_sample_manifest.json"
FULL_REVIEWS = PROJECT_ROOT / "data" / "registry" / "ocr_validation_report.json"
LAYOUT_PROFILES = PROJECT_ROOT / "config" / "layout_profiles.json"
DOCUMENT_IR_CONTRACT = PROJECT_ROOT / "config" / "document_ir_contract.json"
EXCEPTION_REVIEW = PROJECT_ROOT / "data" / "stage4" / "stage4_full_parse_exception_review.json"
MAX_DERIVED_PAGE_LAYOUT_DISTANCE = 0.30
LAYOUT_PROFILE_ID = "adaptive_pdf_v1"


class DocumentAuditError(RuntimeError):
    def __init__(self, message: str, *, page_failures: list[dict] | None = None):
        super().__init__(message)
        self.page_failures = page_failures or []


def _load_jsonl(path: Path) -> dict[str, dict]:
    return {
        row["asset_id"]: row
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for row in (json.loads(line),)
    }


def _resolve_path(relative_path: str, source_root_id: str, settings: Settings) -> Path:
    if source_root_id == "source":
        return settings.source_root / relative_path
    if source_root_id == "ocr_derived" and relative_path.startswith("OCR/"):
        return settings.ocr_derived_root / relative_path.removeprefix("OCR/")
    raise ValueError(f"cannot resolve registered asset: {relative_path!r}")


def _sha256(path: Path) -> str:
    return sha256_file(path)


def _assert_manifest_identity(item: dict, processing_record: dict) -> None:
    for field in ("asset_id", "document_logical_id", "revision_id", "sha256", "relative_path"):
        if item.get(field) != processing_record.get(field):
            raise ValueError(f"frozen manifest {field} differs from Registry for {item.get('asset_id')}")


def _current_manifest_items(manifest: dict, frozen: dict, full_reviews: dict, records: dict[str, dict], catalog: IdentityCatalog) -> list[dict]:
    """Select current processing bytes without changing the admitted batch."""
    if (manifest.get("artifact_kind") != "stage5_sample_manifest"
            or manifest.get("status") != "complete" or frozen.get("status") != "frozen"
            or frozen.get("manifest_kind") != "research_trial_sampling"):
        raise ValueError("current Stage 5 manifest and historical frozen batch are required")
    historical = {row["document_logical_id"]: row for row in frozen["source_documents"]}
    documents = manifest["documents"]
    if (len(historical) != 5 or len(frozen["source_documents"]) != 5 or len(documents) != 5
            or {row["document_logical_id"] for row in documents} != set(historical)
            or len({row["document_key"] for row in documents}) != 5
            or len({row["processing_asset_id"] for row in documents}) != 5):
        raise ValueError("current input scope must preserve the five frozen logical documents")
    reviews = full_reviews.get("full_corpus_reviews", [])
    by_key = {row["document_key"]: row for row in reviews}
    if len(by_key) != len(reviews) or set(by_key) != {row["document_key"] for row in documents}:
        raise ValueError("current OCR review records must cover the batch exactly once")
    selected = []
    for document in documents:
        old = historical[document["document_logical_id"]]
        historical_asset = catalog.asset_for_id(old["asset_id"])
        original_id = historical_asset.derived_from_asset_id or historical_asset.asset_id
        processing = catalog.asset_for_id(document["processing_asset_id"])
        original = catalog.asset_for_id(document["original_asset_id"])
        if (original.asset_id != original_id or original.asset_kind != "original"
                or processing.asset_kind != "derived_ocr" or processing.derived_from_asset_id != original.asset_id
                or historical_asset.document_logical_id != old["document_logical_id"]
                or historical_asset.revision_id != old["revision_id"]
                or any(asset.document_logical_id != old["document_logical_id"] or asset.revision_id != old["revision_id"] for asset in (processing, original))
                or document["processing_relative_path"] != processing.relative_path
                or document["original_relative_path"] != original.relative_path):
            raise ValueError("current processing selection changes frozen document/revision or original identity")
        original_record, processing_record = records[original.asset_id], records[processing.asset_id]
        page_count = document["page_count"]
        if (type(page_count) is not int or page_count < 1
                or page_count != original_record["page_count"] or page_count != processing_record["page_count"]):
            raise ValueError("current manifest page counts differ from original/processing Registry")
        review = by_key[document["document_key"]]
        # Stage 4 verifies structure on the selected current bytes. Stage 5 owns
        # transcription accuracy, table-cell review, and review-range completion.
        if (review.get("original_sha256") != original_record["sha256"]
                or review.get("processing_sha256") != processing_record["sha256"]):
            raise ValueError("OCR review record is not bound to current Registry PDF bytes")
        selected.append({
            **{field: processing_record[field] for field in ("asset_id", "document_logical_id", "revision_id", "sha256", "relative_path")},
            "document_key": document["document_key"],
        })
    return selected


def _layout_profile_fingerprint(profile: LayoutProfile) -> str:
    """Fingerprint the exact profile values passed to the parser."""
    payload = asdict(profile)
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _parse_registered_pdf(
    path: Path,
    relative_path: str,
    catalog: IdentityCatalog,
    *,
    title: str,
    profile: LayoutProfile,
    config_fingerprint: str,
    page_indices: tuple[int, ...] | None = None,
):
    """Parse a registered asset with the profile selected from Stage 4 config.

    The public Registry bridge receives the selected profile explicitly.  Its
    historical interface has no config-fingerprint parameter, so the audit
    attaches the fingerprint of those exact profile values to the returned
    parsing-run metadata at this boundary.
    """
    ir = parse_registered_pdf(
        path,
        relative_path,
        catalog,
        title=title,
        profile=profile,
        page_indices=page_indices,
    )
    return replace(
        ir,
        parsing_run=replace(ir.parsing_run, config_fingerprint=config_fingerprint),
    )


def _page_layout_bits(page) -> list[bool]:
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(0.05, 0.05),
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )
    values = list(pixmap.samples)
    mean = sum(values) / len(values)
    return [value > mean for value in values]


def _derived_page_alignment(processing_path: Path, source_path: Path, expected_pages: int) -> dict:
    """Confirm same-index original/OCR page mapping using dimensions and page layout."""
    distances: list[float] = []
    rotation_normalized_page_count = 0
    with pymupdf.open(processing_path) as processing_pdf, pymupdf.open(source_path) as source_pdf:
        if len(processing_pdf) != expected_pages or len(source_pdf) != expected_pages:
            raise ValueError("original/OCR page count changed during page alignment check")
        for index in range(expected_pages):
            processing_page = processing_pdf[index]
            source_page = source_pdf[index]
            if processing_page.rect != source_page.rect:
                raise ValueError(f"original/OCR page geometry mismatch at PDF page {index + 1}")
            if processing_page.rotation != source_page.rotation:
                rotation_normalized_page_count += 1
            processing_bits = _page_layout_bits(processing_page)
            source_bits = _page_layout_bits(source_page)
            if len(processing_bits) != len(source_bits):
                raise ValueError(f"original/OCR page raster geometry mismatch at PDF page {index + 1}")
            distance = sum(left != right for left, right in zip(processing_bits, source_bits)) / len(processing_bits)
            if distance > MAX_DERIVED_PAGE_LAYOUT_DISTANCE:
                raise ValueError(
                    f"original/OCR page layout mismatch at PDF page {index + 1}: {distance:.3f}"
                )
            distances.append(distance)
    return {
        "method": "same_pdf_index + page_geometry + low_resolution_binary_layout",
        "page_count": expected_pages,
        "maximum_layout_distance": round(max(distances, default=0.0), 6),
        "mean_layout_distance": round(sum(distances) / len(distances), 6) if distances else 0.0,
        "maximum_allowed_layout_distance": MAX_DERIVED_PAGE_LAYOUT_DISTANCE,
        "rotation_normalized_page_count": rotation_normalized_page_count,
        "status": "pass",
    }


def _stable_output_fingerprint(ir) -> str:
    """Hash semantic Document IR output without random ParsingRun/BlockVersion IDs."""
    blocks = {block.block_version_id: (block.page_id, block.block_ordinal) for block in ir.blocks}
    payload = {
        "pages": [
            {
                "pdf_page_index": page.pdf_page_index,
                "display_page_number": page.display_page_number,
                "printed_page_label": page.printed_page_label,
                "width_pt": page.width_pt,
                "height_pt": page.height_pt,
                "rotation_deg": page.rotation_deg,
                "page_mode": page.page_mode,
                "text_layer_status": page.text_layer_status,
                "asset_page_refs": [record_value(reference) for reference in page.asset_page_refs],
            }
            for page in ir.pages
        ],
        "blocks": [
            {
                "page_id": block.page_id,
                "block_ordinal": block.block_ordinal,
                "block_type": block.block_type,
                "text": block.text,
                "bbox": record_value(block.bbox),
                "reading_order": block.reading_order,
                "text_origin": block.text_origin,
            }
            for block in ir.blocks
        ],
        "source_spans": [
            {
                "page_id": span.page_id,
                "block_positions": [blocks[block_id] for block_id in span.block_version_ids],
                "quote": span.quote,
                "content_kind": span.content_kind,
                "char_start": span.char_start,
                "char_end": span.char_end,
                "bbox": record_value(span.bbox),
                "text_origin": span.text_origin,
            }
            for span in ir.source_spans
        ],
        "figures": [blocks[figure.block_version_id] for figure in ir.figures],
        "tables": [record_value(table) for table in ir.tables],
        "table_cells": [record_value(cell) for cell in ir.table_cells],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _page_output_counts(ir) -> tuple[int, int, list[int]]:
    block_pages = Counter(block.page_id for block in ir.blocks)
    span_pages = Counter(span.page_id for span in ir.source_spans)
    figure_pages = Counter(
        next(block.page_id for block in ir.blocks if block.block_version_id == figure.block_version_id)
        for figure in ir.figures
    )
    empty_pages = [
        page.display_page_number
        for page in ir.pages
        if not (block_pages[page.page_id] or span_pages[page.page_id] or figure_pages[page.page_id])
    ]
    return sum(block_pages.values()), sum(span_pages.values()), empty_pages


def _page_mode_numbers(ir) -> dict[str, list[int]]:
    """Keep each non-standard routing decision auditable by PDF page number."""
    modes: dict[str, list[int]] = {}
    for page in ir.pages:
        modes.setdefault(page.page_mode, []).append(page.display_page_number)
    return dict(sorted(modes.items()))


def _parse_with_page_failure_isolation(
    path: Path,
    relative_path: str,
    catalog: IdentityCatalog,
    *,
    title: str,
    expected_pages: int,
    profile: LayoutProfile,
    config_fingerprint: str,
):
    """Report a concrete page list when full-document parsing cannot complete."""
    try:
        return _parse_registered_pdf(
            path,
            relative_path,
            catalog,
            title=title,
            profile=profile,
            config_fingerprint=config_fingerprint,
        ), []
    except Exception as full_error:
        page_failures: list[dict] = []
        for page_index in range(expected_pages):
            try:
                _parse_registered_pdf(
                    path,
                    relative_path,
                    catalog,
                    title=title,
                    profile=profile,
                    config_fingerprint=config_fingerprint,
                    page_indices=(page_index,),
                )
            except Exception as page_error:
                page_failures.append({
                    "pdf_page_number": page_index + 1,
                    "error": f"{type(page_error).__name__}: {page_error}",
                })
        raise DocumentAuditError(
            f"full-document parsing failed: {type(full_error).__name__}: {full_error}",
            page_failures=page_failures,
        ) from full_error


def _audit_document(
    item: dict,
    *,
    catalog: IdentityCatalog,
    records: dict[str, dict],
    settings: Settings,
    profile: LayoutProfile,
    config_fingerprint: str,
) -> dict:
    processing_asset = catalog.asset_for_id(item["asset_id"])
    source_asset = (
        catalog.asset_for_id(processing_asset.derived_from_asset_id)
        if processing_asset.derived_from_asset_id
        else processing_asset
    )
    processing_record = records[processing_asset.asset_id]
    source_record = records[source_asset.asset_id]
    _assert_manifest_identity(item, processing_record)
    expected_pages = int(source_record["page_count"])
    processing_pages = int(processing_record["page_count"])
    path = _resolve_path(processing_asset.relative_path, processing_asset.source_root_id, settings)
    source_path = _resolve_path(source_asset.relative_path, source_asset.source_root_id, settings)
    if not path.is_file():
        raise FileNotFoundError(path)
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    processing_observed_sha256 = _sha256(path)
    source_observed_sha256 = _sha256(source_path)
    if processing_observed_sha256 != processing_record["sha256"]:
        raise ValueError(f"registered processing asset SHA-256 mismatch: {processing_asset.asset_id}")
    if source_observed_sha256 != source_record["sha256"]:
        raise ValueError(f"registered original asset SHA-256 mismatch: {source_asset.asset_id}")
    if processing_pages != expected_pages:
        raise ValueError(
            f"processing/original page count mismatch for {processing_asset.asset_id}: "
            f"{processing_pages} != {expected_pages}"
        )

    ir, page_failures = _parse_with_page_failure_isolation(
        path,
        processing_asset.relative_path,
        catalog,
        title=item["document_logical_id"],
        expected_pages=expected_pages,
        profile=profile,
        config_fingerprint=config_fingerprint,
    )
    if page_failures:
        raise DocumentAuditError("page-level parsing failures detected", page_failures=page_failures)
    if len(ir.pages) != expected_pages:
        raise ValueError(
            f"Document IR page count mismatch for {processing_asset.asset_id}: "
            f"{len(ir.pages)} != {expected_pages}"
        )
    if any(page.pdf_page_index != offset for offset, page in enumerate(ir.pages)):
        raise ValueError(f"Document IR page order mismatch for {processing_asset.asset_id}")
    if any(page.display_page_number != page.pdf_page_index + 1 for page in ir.pages):
        raise ValueError(f"Document IR display page numbering mismatch for {processing_asset.asset_id}")

    block_count, span_count, empty_pages = _page_output_counts(ir)
    source_asset_in_ir = any(asset.asset_id == source_asset.asset_id for asset in ir.assets)
    if processing_asset.derived_from_asset_id and not source_asset_in_ir:
        raise ValueError(f"derived processing asset lacks original source in IR: {processing_asset.asset_id}")
    original_page_refs_confirmed = all(
        any(
            reference.asset_id == source_asset.asset_id and reference.page_index == page.pdf_page_index
            for reference in page.asset_page_refs
        )
        for page in ir.pages
    )
    if processing_asset.derived_from_asset_id and not original_page_refs_confirmed:
        raise ValueError(f"derived processing pages lack same-index original references: {processing_asset.asset_id}")
    page_alignment = (
        _derived_page_alignment(path, source_path, expected_pages)
        if processing_asset.derived_from_asset_id
        else {"method": "registered original asset parsed directly", "page_count": expected_pages, "status": "pass"}
    )

    return {
        "status": "pass",
        "document_logical_id": item["document_logical_id"],
        "revision_id": processing_asset.revision_id,
        "processing_asset": {
            "asset_id": processing_asset.asset_id,
            "asset_kind": processing_asset.asset_kind,
            "relative_path": processing_asset.relative_path,
            "page_count": processing_pages,
        },
        "source_original_asset": {
            "asset_id": source_asset.asset_id,
            "relative_path": source_asset.relative_path,
            "page_count": expected_pages,
        },
        "integrity": {
            "processing_asset": {
                "registry_sha256": processing_record["sha256"],
                "observed_sha256": processing_observed_sha256,
            },
            "source_original_asset": {
                "registry_sha256": source_record["sha256"],
                "observed_sha256": source_observed_sha256,
            },
        },
        "source_asset_in_ir": source_asset_in_ir,
        "original_page_refs_confirmed": original_page_refs_confirmed,
        "source_to_processing_page_alignment": page_alignment,
        "output": {
            "page_count": len(ir.pages),
            "block_count": block_count,
            "source_span_count": span_count,
            "figure_count": len(ir.figures),
            "table_count": len(ir.tables),
            "table_cell_count": len(ir.table_cells),
            "page_mode_counts": dict(sorted(Counter(page.page_mode for page in ir.pages).items())),
            "page_mode_page_numbers": _page_mode_numbers(ir),
            "empty_output_pages": empty_pages,
            "parsing_run": asdict(ir.parsing_run),
            "stable_output_fingerprint": _stable_output_fingerprint(ir),
        },
    }


def run_audit(*, historical_frozen: bool = False) -> dict:
    settings = Settings.from_environment()
    profile = load_layout_profile(LAYOUT_PROFILES, LAYOUT_PROFILE_ID)
    config_fingerprint = _layout_profile_fingerprint(profile)
    catalog = load_identity_catalog(REGISTRY_ASSETS, REVISION_CATALOG, DERIVED_LINKS)
    records = _load_jsonl(REGISTRY_ASSETS)
    frozen = json.loads(MANIFEST.read_text(encoding="utf-8"))
    input_manifest = MANIFEST if historical_frozen else CURRENT_MANIFEST
    manifest = json.loads(input_manifest.read_text(encoding="utf-8"))
    if historical_frozen:
        if manifest.get("manifest_kind") != "research_trial_sampling" or manifest.get("status") != "frozen":
            raise ValueError("unexpected historical input manifest kind/status")
        items = manifest["source_documents"]
        snapshot = manifest.get("registry_snapshot", {})
    else:
        full_reviews = json.loads(FULL_REVIEWS.read_text(encoding="utf-8"))
        items = _current_manifest_items(manifest, frozen, full_reviews, records, catalog)
        snapshot = manifest.get("source_registry_snapshot", {})
        if snapshot.get("source_assets_sha256") != _sha256(REGISTRY_ASSETS):
            raise ValueError("current Stage 5 manifest Registry asset fingerprint is stale")
        documents_path = PROJECT_ROOT / "data/registry/source_documents.jsonl"
        if snapshot.get("source_documents_sha256") != _sha256(documents_path):
            raise ValueError("current Stage 5 manifest Registry document fingerprint is stale")
    snapshot_path = PROJECT_ROOT / snapshot.get("path", "")
    if not snapshot_path.is_file() or _sha256(snapshot_path) != snapshot.get("sha256"):
        raise ValueError("input manifest Registry snapshot does not match its declared SHA-256")
    manifest_assets = [item.get("asset_id") for item in items]
    if len(manifest_assets) != len(set(manifest_assets)) or any(not asset_id for asset_id in manifest_assets):
        raise ValueError("frozen manifest contains a missing or duplicate processing asset")

    rows: list[dict] = []
    failures: list[dict] = []
    for item in items:
        try:
            rows.append(
                _audit_document(
                    item,
                    catalog=catalog,
                    records=records,
                    settings=settings,
                    profile=profile,
                    config_fingerprint=config_fingerprint,
                )
            )
        except DocumentAuditError as exc:
            failures.append({
                "asset_id": item["asset_id"],
                "error": f"{type(exc).__name__}: {exc}",
                "page_failures": exc.page_failures,
            })
        except Exception as exc:  # record the document-level failure for the audit artifact
            failures.append({"asset_id": item["asset_id"], "error": f"{type(exc).__name__}: {exc}", "page_failures": []})

    if not EXCEPTION_REVIEW.is_file():
        raise ValueError("full-parse exception review is required before this audit can be finalized")
    exception_review = json.loads(EXCEPTION_REVIEW.read_text(encoding="utf-8"))
    expected_exceptions = {
        (
            row["source_original_asset"]["asset_id"],
            page_number,
        )
        for row in rows
        for page_mode in ("review_required", "scan_only")
        for page_number in row["output"]["page_mode_page_numbers"].get(page_mode, [])
    }
    reviewed_items = [item for item in exception_review.get("items", []) if item.get("disposition") == "approved_intentional_blank_page"]
    reviewed_exceptions = {
        (item.get("source_original_asset_id"), item.get("pdf_page_number"))
        for item in exception_review.get("items", [])
        if item.get("disposition") == "approved_intentional_blank_page"
    }
    if (exception_review.get("status") != "pass" or len(reviewed_items) != len(reviewed_exceptions)
            or any(item.get("original_sha256") != records.get(item.get("source_original_asset_id"), {}).get("sha256") for item in reviewed_items)):
        raise ValueError("blank-page approvals must bind unchanged original asset bytes")
    if not failures and expected_exceptions != reviewed_exceptions:
        raise ValueError("full-parse exception review does not exactly match routed exception pages")

    output_rows = [row["output"] for row in rows]
    total_modes: Counter[str] = Counter()
    for row in output_rows:
        total_modes.update(row["page_mode_counts"])
    input_paths = {
        "input_manifest": input_manifest, "historical_batch_scope": MANIFEST,
        "registry_assets": REGISTRY_ASSETS, "revision_catalog": REVISION_CATALOG,
        "derived_links": DERIVED_LINKS, "layout_profiles": LAYOUT_PROFILES,
        "document_ir_contract": DOCUMENT_IR_CONTRACT, "exception_review": EXCEPTION_REVIEW,
        "audit_script": Path(__file__).resolve(),
        "pdf_parser": PROJECT_ROOT / "src/turbine_kg/documents/pdf.py",
        "page_profiles": PROJECT_ROOT / "src/turbine_kg/documents/profiles.py",
        "ir_parser": PROJECT_ROOT / "src/turbine_kg/documents/parser.py",
    }
    if not historical_frozen:
        input_paths["full_corpus_reviews"] = FULL_REVIEWS
    inputs = {key: {"path": path.relative_to(PROJECT_ROOT).as_posix(), "sha256": _sha256(path)} for key, path in input_paths.items()}
    input_fingerprint = hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "stage": "4",
        "artifact_kind": "full_document_ir_parse_audit",
        "audited_at": date.today().isoformat(),
        "input_manifest": input_manifest.relative_to(PROJECT_ROOT).as_posix(),
        "input_mode": "strict_historical_frozen" if historical_frozen else "current_registered_ocr",
        "input_fingerprint": input_fingerprint,
        "inputs": inputs,
        "scope": "all pages of the unchanged five-document batch using explicitly selected processing assets",
        "formal_release": False,
        "processing_strategy": {
            "processing_source": "parse each current registered OCR and retain original same-index asset references" if not historical_frozen else "strict historical Stage 3 processing selection",
        },
        "reproducibility_basis": {
            "audit_script_sha256": _sha256(Path(__file__).resolve()),
            "registry_snapshot": snapshot,
            "layout_profile_sha256": _sha256(LAYOUT_PROFILES),
            "layout_profile_id": profile.profile_id,
            "layout_profile_config_fingerprint": config_fingerprint,
            "document_ir_contract_sha256": _sha256(DOCUMENT_IR_CONTRACT),
            "python_version": sys.version.split()[0],
            "pymupdf_version": getattr(pymupdf, "VersionBind", "unknown"),
            "stable_output_fingerprint": "Excludes random ParsingRun and BlockVersion IDs; equal input/config/runtime should reproduce this value.",
        },
        "exception_review": {
            "path": str(EXCEPTION_REVIEW.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "sha256": _sha256(EXCEPTION_REVIEW),
            "expected_exception_page_count": len(expected_exceptions),
            "status": "pass" if not failures and expected_exceptions == reviewed_exceptions else "not_reconciled_due_to_parse_failure",
            "approved_original_pages": sorted([list(identity) for identity in reviewed_exceptions]),
        },
        "expected": {
            "document_count": len(items),
            "physical_page_count": sum(
                int(records[
                    catalog.asset_for_id(item["asset_id"]).derived_from_asset_id
                    or item["asset_id"]
                ]["page_count"])
                for item in items
            ),
        },
        "actual": {
            "document_count": len(rows),
            "physical_page_count": sum(row["output"]["page_count"] for row in rows),
            "block_count": sum(row["output"]["block_count"] for row in rows),
            "source_span_count": sum(row["output"]["source_span_count"] for row in rows),
            "figure_count": sum(row["output"]["figure_count"] for row in rows),
            "table_count": sum(row["output"]["table_count"] for row in rows),
            "table_cell_count": sum(row["output"]["table_cell_count"] for row in rows),
            "page_mode_counts": dict(sorted(total_modes.items())),
            "empty_output_page_count": sum(len(row["output"]["empty_output_pages"]) for row in rows),
            "failed_document_count": len(failures),
            "failed_page_count": sum(len(row["page_failures"]) for row in failures),
        },
        "documents": rows,
        "failures": failures,
        "status": "pass" if not failures and len(rows) == len(items) else "fail",
        "boundaries": [
            "This is a full-page structural Document IR audit, not an OCR quality benchmark or Stage 5 review-completion gate.",
            "Table row/column recovery remains Stage 5 work.",
            "Formal release remains false.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )
    parser.add_argument("--historical-frozen", action="store_true", help="strict replay of the historical manifest; requires an explicit separate --output")
    args = parser.parse_args()
    if args.historical_frozen and args.output is None:
        parser.error("--historical-frozen requires an explicit --output")
    output = args.output or PROJECT_ROOT / "data/stage4/stage4_full_parse_audit.json"
    result = run_audit(historical_frozen=args.historical_frozen)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output), "actual": result["actual"]}, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Bind Stage 7 to the current, complete Stage 5 PDF baseline."""

from __future__ import annotations

import json
from pathlib import Path

from stage5_fingerprint import ocr_fingerprint, sha256_file
from stage6_page_review_binding import current_stage5_bindings, input_bindings_match


BASELINE_RELATIVE_PATH = "data/stage5/stage5_baseline_benchmark.json"


def _nonnegative_integer(value: object) -> bool:
    return type(value) is int and value >= 0


def validate_current_baseline(
    baseline: dict, sample: dict, assets: dict[str, dict],
    current_fingerprint: str, current_components: dict,
) -> tuple[dict[tuple[str, int], dict], dict]:
    if (baseline.get("artifact_kind") != "stage5_baseline_benchmark"
            or baseline.get("input_fingerprint") != current_fingerprint
            or baseline.get("fingerprint_components") != current_components):
        raise ValueError("Stage 5 baseline fingerprint does not match current inputs")
    if baseline.get("errors") != []:
        raise ValueError("Stage 5 baseline has unresolved processing errors")
    documents = sample["documents"]
    expected_documents = {row["document_key"]: row for row in documents}
    if len(expected_documents) != len(documents):
        raise ValueError("Stage 5 sample has duplicate document keys")
    selected_assets = {
        row["asset_id"]: row for row in current_components["selected_assets"]
    }
    expected_assets = {
        document[field] for document in documents
        for field in ("original_asset_id", "processing_asset_id")
    }
    if set(selected_assets) != expected_assets:
        raise ValueError("current fingerprint asset scope differs from the Stage 5 sample")
    for document in documents:
        original = assets[document["original_asset_id"]]
        processing = assets[document["processing_asset_id"]]
        for asset, field in ((original, "original_relative_path"), (processing, "processing_relative_path")):
            selected = selected_assets[asset["asset_id"]]
            if (selected["sha256"] != asset["sha256"]
                    or selected["relative_path"] != asset["relative_path"]
                    or document[field] != asset["relative_path"]
                    or document["document_logical_id"] != asset["document_logical_id"]):
                raise ValueError("Stage 5 sample/Registry identity differs from actual current PDF bytes")
        if original["revision_id"] != processing["revision_id"]:
            raise ValueError("Stage 5 original/processing revision identity differs")
    records = baseline.get("page_records", [])
    pages: dict[tuple[str, int], dict] = {}
    for row in records:
        number = row.get("physical_page")
        if type(number) is not int or number < 1:
            raise ValueError("Stage 5 baseline has an invalid physical page")
        key = (row["document_key"], number)
        if key in pages:
            raise ValueError("Stage 5 baseline has duplicate physical pages")
        pages[key] = row
    expected_pages = {
        (document["document_key"], number)
        for document in documents for number in range(1, document["page_count"] + 1)
    }
    if set(pages) != expected_pages:
        raise ValueError("Stage 5 baseline page coverage differs from the current sample")
    blank_count = empty_nonblank_count = 0
    for (key, _), row in pages.items():
        document = expected_documents[key]
        if any(row.get(field) != document[field] for field in (
            "document_logical_id", "processing_asset_id", "original_asset_id",
        )):
            raise ValueError("Stage 5 baseline page identity differs from the current sample")
        metrics = row.get("processing_metrics", {})
        if (not _nonnegative_integer(metrics.get("text_chars"))
                or type(metrics.get("low_text_flag")) is not bool
                or row.get("page_mode") not in {"native_text", "mixed", "scan_only", "review_required"}):
            raise ValueError("Stage 5 baseline page metrics or mode are invalid")
        if type(row.get("reviewed_blank")) is not bool:
            raise ValueError("Stage 5 baseline lacks explicit reviewed blank accounting")
        if row["reviewed_blank"] and row.get("full_review_binding_status") != "bound_to_current_pdf":
            raise ValueError("Stage 5 blank page is not bound to the current reviewed PDF")
        blank_count += row["reviewed_blank"]
        empty_nonblank_count += metrics["text_chars"] == 0 and not row["reviewed_blank"]
    baseline_documents = baseline.get("documents", [])
    by_document = {row["document_key"]: row for row in baseline_documents}
    if len(by_document) != len(baseline_documents) or set(by_document) != set(expected_documents):
        raise ValueError("Stage 5 baseline document coverage differs from the current sample")
    failures = 0
    for key, document in expected_documents.items():
        row = by_document[key]
        failed = row.get("failed_pages")
        processed_count = sum(page_key[0] == key for page_key in pages)
        if (not isinstance(failed, list)
                or row.get("failed_page_count") != len(failed)
                or row.get("processed_page_count") != processed_count
                or row.get("page_count") != document["page_count"]
                or row.get("processing_sha256") != assets[document["processing_asset_id"]]["sha256"]
                or row.get("original_sha256") != assets[document["original_asset_id"]]["sha256"]):
            raise ValueError("Stage 5 baseline document counts or PDF fingerprints do not reconcile")
        failures += len(failed)
    actual = baseline.get("actual", {})
    if (failures or actual.get("failed_page_count") != failures
            or actual.get("page_count") != len(pages)
            or actual.get("document_count") != len(documents)):
        raise ValueError("Stage 5 baseline failed pages or coverage totals do not reconcile")
    return pages, {
        "expected_page_count": len(expected_pages),
        "page_record_count": len(pages),
        "reviewed_blank_page_count": blank_count,
        "empty_nonblank_page_count": empty_nonblank_count,
        "failed_page_count": failures,
    }


def load_current_baseline(root: Path, sample: dict, assets: dict[str, dict]) -> tuple[Path, dict, dict, dict]:
    path = root / BASELINE_RELATIVE_PATH
    baseline = json.loads(path.read_text(encoding="utf-8"))
    fingerprint, components = ocr_fingerprint()
    pages, coverage = validate_current_baseline(baseline, sample, assets, fingerprint, components)
    return path, baseline, pages, coverage


def validate_stage6_source_binding(root: Path, fingerprint: str) -> None:
    """Reject accepted Evidence produced before the current reviewed PDFs."""
    expected = current_stage5_bindings(root, fingerprint)
    golden_path = root / "data/stage6/stage6_evidence_golden_sample.json"
    build = json.loads((root / "data/stage6/stage6_evidence_build_audit.json").read_text(encoding="utf-8"))
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    table = json.loads((root / "data/stage6/stage6_table_evidence_audit.json").read_text(encoding="utf-8"))
    if (build.get("status") != "complete"
            or not input_bindings_match((golden, build, table), expected)
            or table.get("status") != "complete"
            or build.get("inputs", {}).get("stage6_evidence_golden_sample_sha256") != sha256_file(golden_path)):
        raise ValueError("Stage 6 Evidence is not bound to current reviewed Stage 5 inputs")
    for stage in ("stage5", "stage6"):
        gate = json.loads((root / f"data/{stage}/{stage}_exit_audit.json").read_text(encoding="utf-8"))
        if gate.get("status") != "complete" or gate.get("next_stage_allowed") is not True:
            raise ValueError(f"current {stage} exit gate does not allow Stage 7 consumption")

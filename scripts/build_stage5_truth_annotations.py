"""Build Stage 5 truth annotations from original PDFs and independent review.

The original PDF is the authority.  Native PDF text may be scored after visual
confirmation.  Scanned pages require an independently supplied manual
transcription; processing OCR output is never accepted as its own truth.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re

import pymupdf

from turbine_kg.settings import PROJECT_ROOT, Settings
from audit_stage5_exit import _current_user_acceptance
from stage5_fingerprint import ocr_fingerprint


STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"
MANIFEST = STAGE5_ROOT / "stage5_sample_manifest.json"
ASSETS = PROJECT_ROOT / "data" / "registry" / "source_assets.jsonl"
TABLE_REVIEW = STAGE5_ROOT / "stage5_table_truth_review.json"
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[,.]\d+)?(?:\s*[-~至]\s*\d+(?:[,.]\d+)?)?")
UNIT_PATTERN = re.compile(
    r"(?:mm|cm|m|MPa|kPa|Pa|℃|°C|V|kV|A|Hz|MW|kW|rpm|%|毫米|厘米|米|兆帕|千帕)",
    re.IGNORECASE,
)
NEGATION_TERMS = ("不得", "不应", "禁止", "严禁", "不准", "无须", "除非", "未")
SCOPED_DISPOSITIONS = {
    "structured_text_candidate",
    "metadata_only",
    "region_scoped_only",
    "navigation_only",
    "boundary_exception",
}


def _load_jsonl(path: Path) -> dict[str, dict]:
    return {
        row["asset_id"]: row
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for row in (json.loads(line),)
    }


def _latest(pattern: str) -> Path:
    candidates = sorted(STAGE5_ROOT.glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"no Stage 5 artifact matches {pattern}")
    return candidates[-1]


def _resolve(asset: dict, settings: Settings) -> Path:
    if asset["source_root_id"] == "source":
        return settings.source_root / asset["relative_path"]
    if asset["source_root_id"] == "ocr_derived" and asset["relative_path"].startswith("OCR/"):
        return settings.ocr_derived_root / asset["relative_path"].removeprefix("OCR/")
    raise ValueError(f"unsupported asset root/path: {asset['asset_id']}")


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _lines(text: str) -> list[str]:
    return [_normalize(line) for line in text.splitlines() if _normalize(line)]


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tokens(text: str) -> dict[str, list[str] | dict[str, int]]:
    normalized = _normalize(text)
    return {
        "numbers": NUMBER_PATTERN.findall(normalized),
        "units": UNIT_PATTERN.findall(normalized),
        "negations": dict(Counter(term for term in NEGATION_TERMS if term in normalized)),
    }


def _validate_table_review(report: dict, manifest: dict, assets: dict, fingerprint: str,
                           components: dict, input_hashes: dict) -> dict[tuple[str, int], dict]:
    if (report.get("source_input_fingerprint") != fingerprint
            or report.get("source_fingerprint_components") != components
            or any(report.get("inputs", {}).get(key) != value for key, value in input_hashes.items())):
        raise ValueError("table truth is not bound to current manifest/Registry/full review/PDFs")
    documents = {row["document_key"]: row for row in manifest["documents"]}
    selected = {row["asset_id"]: row for row in components["selected_assets"]}
    bindings = {row["document_key"]: row for row in report.get("documents", [])}
    if len(bindings) != len(report.get("documents", [])) or set(bindings) != set(documents):
        raise ValueError("table truth current document bindings are incomplete or duplicated")
    for key, document in documents.items():
        for role in ("original", "processing"):
            asset = assets[document[role + "_asset_id"]]
            if (bindings[key].get(role + "_asset_id") != asset["asset_id"]
                    or bindings[key].get(role + "_sha256") != asset["sha256"]
                    or selected[asset["asset_id"]]["sha256"] != asset["sha256"]):
                raise ValueError("table truth PDF binding differs from current Registry/actual bytes")
    expected = {(row["document_key"], int(page["physical_page"]))
                for row in manifest["documents"] for page in row["sample_pages"]
                if set(page["categories"]) & {"table", "continuation_table", "table_candidate", "complex_layout"}}
    rows = report.get("records", [])
    actual = {(row["document_key"], row["physical_page"]): row for row in rows}
    if len(actual) != len(rows) or set(actual) != expected:
        raise ValueError("table truth layout-page coverage differs from the current sample")
    for (key, number), row in actual.items():
        if type(number) is not int or not 1 <= number <= documents[key]["page_count"]:
            raise ValueError("table truth physical page is invalid")
        for role in ("original", "processing"):
            asset = assets[documents[key][role + "_asset_id"]]
            if (row.get(role + "_asset_id") != asset["asset_id"]
                    or row.get(role + "_sha256") != asset["sha256"]):
                raise ValueError("table truth page identity differs from its current PDF")
    return actual


def _table_truth(path: Path, *, manifest: dict, assets: dict, fingerprint: str,
                 components: dict, input_hashes: dict) -> dict[tuple[str, int], dict]:
    report = json.loads(path.read_text(encoding="utf-8"))
    records = _validate_table_review(report, manifest, assets, fingerprint, components, input_hashes)
    settings = Settings.from_environment()
    pdfs = {}
    try:
        for row in records.values():
            for role in ("original", "processing"):
                asset_id = row[role + "_asset_id"]
                if asset_id not in pdfs:
                    pdfs[asset_id] = pymupdf.open(_resolve(assets[asset_id], settings))
                page = pdfs[asset_id][row["physical_page"] - 1]
                if row.get(role + "_page_geometry") != _page_geometry(page):
                    raise ValueError("table truth page geometry/rotation differs from current PDF")
    finally:
        for pdf in pdfs.values():
            pdf.close()
    return records


def _page_geometry(page) -> dict:
    return {"rotation": page.rotation, "mediabox": list(page.mediabox),
            "cropbox": list(page.cropbox), "rect": list(page.rect)}


def _reviewed_transcription(visual: dict, original: dict, processing: dict,
                            full_review: dict, report_sha256: str, gate: dict) -> str | None:
    """Accept only an explicit transcription from completed visual correction.

    This records a transcription already checked against the source image. It
    does not score a generated OCR candidate against itself or grant table
    Evidence approval.
    """
    binding = visual.get("reviewed_transcription_binding")
    if binding is None:
        return None
    agent_reviewed = all(full_review.get(flag) is True for flag in (
        "line_by_line_reviewed", "table_cells_reviewed", "reading_order_reviewed"
    ))
    document_gate = next(
        (row for row in gate.get("documents", [])
         if row.get("document_key") == visual.get("document_key")),
        None,
    )
    user_accepted = bool(
        document_gate
        and document_gate.get("review_basis") == "explicit_current_pdf_user_acceptance"
        and _current_user_acceptance(
            full_review.get("user_acceptance"), original["sha256"],
            processing["sha256"], document_gate["expected_pages"]
        )
    )
    if (gate.get("status") != "complete" or gate.get("next_stage_allowed") is not True
            or binding.get("original_sha256") != original["sha256"]
            or binding.get("processing_sha256") != processing["sha256"]
            or binding.get("full_review_report_sha256") != report_sha256
            or full_review.get("original_sha256") != original["sha256"]
            or full_review.get("processing_sha256") != processing["sha256"]
            or not (agent_reviewed or user_accepted)
            or not any(first <= int(visual["physical_page"]) <= last
                       for first, last in full_review.get("reviewed_page_ranges", []))
            or visual.get("reviewer_kind") != "codex_visual_source_review"):
        raise ValueError("visual transcription is not bound to the current reviewed PDF")
    text = visual.get("independent_reference_text")
    if not isinstance(text, str):
        raise ValueError("explicit visually checked transcription is missing")
    return text


def build() -> dict:
    settings = Settings.from_environment()
    source_input_fingerprint, source_fingerprint_components = ocr_fingerprint()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assets = _load_jsonl(ASSETS)
    review = json.loads(_latest("stage5_golden_sample_review_*.json").read_text(encoding="utf-8"))
    review_by_page = {
        (row["document_key"], int(row["physical_page"])): row for row in review["records"]
    }
    report_path = PROJECT_ROOT / "data/registry/ocr_validation_report.json"
    full_reviews = {row["document_key"]: row for row in
                    json.loads(report_path.read_text(encoding="utf-8")).get("full_corpus_reviews", [])}
    report_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
    gate = json.loads((STAGE5_ROOT / "stage5_exit_audit.json").read_text(encoding="utf-8"))
    input_hashes = {
        "stage5_sample_manifest_sha256": hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
        "source_assets_sha256": hashlib.sha256(ASSETS.read_bytes()).hexdigest(),
        "full_corpus_reviews_sha256": report_sha256,
    }
    table_review = _table_truth(TABLE_REVIEW, manifest=manifest, assets=assets,
                                fingerprint=source_input_fingerprint,
                                components=source_fingerprint_components, input_hashes=input_hashes)
    records: list[dict] = []
    errors: list[str] = []

    for document in manifest["documents"]:
        original = assets[document["original_asset_id"]]
        processing = assets[document["processing_asset_id"]]
        original_doc = pymupdf.open(_resolve(original, settings))
        try:
            for sample_page in document["sample_pages"]:
                page_number = int(sample_page["physical_page"])
                key = (document["document_key"], page_number)
                visual = review_by_page.get(key)
                if visual is None:
                    errors.append(f"missing visual review record: {key}")
                    continue
                original_page = original_doc[page_number - 1]
                original_raw_text = original_page.get_text("text")
                original_text = _normalize(original_raw_text)
                disposition = visual["gate_disposition"]
                is_quarantined = disposition == "quarantine_structured_ocr"
                checked_transcription = _reviewed_transcription(
                    visual, original, processing, full_reviews.get(document["document_key"], {}),
                    report_sha256, gate)
                if checked_transcription is not None:
                    reference_raw_text = checked_transcription
                    reference_text = _normalize(reference_raw_text)
                    reference_kind = "original_pdf_independent_visual_transcription"
                elif original_text:
                    reference_text = original_text
                    reference_raw_text = original_raw_text
                    reference_kind = "original_pdf_native_text_visual_confirmed"
                elif (
                    disposition in SCOPED_DISPOSITIONS
                    and not is_quarantined
                    and visual.get("independent_reference_text")
                ):
                    reference_text = _normalize(visual["independent_reference_text"])
                    reference_raw_text = visual["independent_reference_text"]
                    reference_kind = "original_pdf_independent_manual_transcription"
                else:
                    reference_text = ""
                    reference_raw_text = ""
                    reference_kind = "original_pdf_visual_only_unscored"
                table = table_review.get(key)
                records.append(
                    {
                        "document_key": document["document_key"],
                        "physical_page": page_number,
                        "logical_page": sample_page.get("logical_page_label"),
                        "categories": sample_page["categories"],
                        "original_asset_id": document["original_asset_id"],
                        "processing_asset_id": document["processing_asset_id"],
                        "original_sha256": original["sha256"],
                        "processing_sha256": processing["sha256"],
                        "truth_source": "Original materials original PDF",
                        "visual_review_status": visual["review_status"],
                        "gate_disposition": disposition,
                        "structured_text_scoring_allowed": bool(reference_text) and not is_quarantined,
                        "reference_kind": reference_kind,
                        "reference_text": reference_text,
                        "reference_lines": _lines(reference_raw_text),
                        "reference_text_sha256": _sha256(reference_text),
                        "reference_tokens": _tokens(reference_text),
                        "table_truth": (
                            {
                                **table,
                                "cell_text_accuracy_status": "quarantined_not_scored",
                            }
                            if table and table["table_truth_status"] != "not_applicable"
                            else None
                        ),
                        "complex_layout_truth": table if table and table["table_truth_status"] == "not_applicable" else None,
                    }
                )
        finally:
            original_doc.close()

    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_original_pdf_truth_annotations",
        "created_at": date.today().isoformat(),
        "authority": "Original materials original PDF; page image is authoritative for scans",
        "source_input_fingerprint": source_input_fingerprint,
        "source_fingerprint_components": source_fingerprint_components,
        "inputs": {
            **input_hashes,
            "stage5_table_truth_review": "data/stage5/stage5_table_truth_review.json",
            "stage5_table_truth_review_sha256": hashlib.sha256(TABLE_REVIEW.read_bytes()).hexdigest(),
        },
        "manifest": str(MANIFEST.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "sample_page_count": len(records),
        "records": records,
        "coverage": {
            "visual_reviewed_pages": len(records),
            "structured_text_scoring_pages": sum(row["structured_text_scoring_allowed"] for row in records),
            "visual_only_or_quarantined_pages": sum(not row["structured_text_scoring_allowed"] for row in records),
            "table_pages": sum(row["table_truth"] is not None for row in records),
        },
        "boundaries": [
            "Original PDF pages are the truth source; registered OCR is never accepted without visual review.",
            "Scanned pages are scored only when an independent manual transcription is supplied; processing OCR is never used as truth.",
            "Unresolved tables, formulas, figures and reading order remain quarantined and are not scored as structured truth.",
        ],
        "errors": errors,
        "status": "complete" if len(records) == manifest["sample_page_count"] and not errors else "incomplete",
    }


def main() -> int:
    output = STAGE5_ROOT / f"stage5_truth_annotations_{date.today().isoformat()}.json"
    result = build()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output), "pages": result["sample_page_count"]}, ensure_ascii=False))
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())

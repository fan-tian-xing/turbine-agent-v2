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
from stage5_fingerprint import ocr_fingerprint


STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"
MANIFEST = STAGE5_ROOT / "stage5_sample_manifest.json"
ASSETS = PROJECT_ROOT / "data" / "registry" / "source_assets.jsonl"
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


def _table_truth(path: Path) -> dict[tuple[str, int], dict]:
    report = json.loads(path.read_text(encoding="utf-8"))
    return {(row["document_key"], int(row["physical_page"])): row for row in report["records"]}


def build() -> dict:
    settings = Settings.from_environment()
    source_input_fingerprint, source_fingerprint_components = ocr_fingerprint()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assets = _load_jsonl(ASSETS)
    review = json.loads(_latest("stage5_golden_sample_review_*.json").read_text(encoding="utf-8"))
    review_by_page = {
        (row["document_key"], int(row["physical_page"])): row for row in review["records"]
    }
    table_review = _table_truth(_latest("stage5_table_truth_review_*.json"))
    records: list[dict] = []
    errors: list[str] = []

    for document in manifest["documents"]:
        original = assets[document["original_asset_id"]]
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
                if original_text:
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
                                "table_truth_status": table["table_truth_status"],
                                "visible_content_match": table["visible_content_match"],
                                "leaf_column_count": table.get("leaf_column_count"),
                                "cell_text_accuracy_status": "quarantined_not_scored",
                            }
                            if table and table["table_truth_status"] != "not_applicable"
                            else None
                        ),
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

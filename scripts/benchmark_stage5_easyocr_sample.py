"""Run the complete EasyOCR backup-engine comparison on the frozen Stage 5 sample."""

from __future__ import annotations

import argparse
from datetime import date
import difflib
import hashlib
import json
from pathlib import Path

import easyocr
import fitz
import numpy as np

from generate_ocr_pdf import grouped_text
from turbine_kg.settings import PROJECT_ROOT, Settings


SAMPLE_MANIFEST = PROJECT_ROOT / "data" / "stage5" / "stage5_sample_manifest.json"
REGISTRY_ASSETS = PROJECT_ROOT / "data" / "registry" / "source_assets.jsonl"


def _load_jsonl(path: Path) -> dict[str, dict]:
    return {
        row["asset_id"]: row
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for row in (json.loads(line),)
    }


def _resolve(asset: dict, settings: Settings) -> Path:
    if asset["source_root_id"] == "source":
        return settings.source_root / asset["relative_path"]
    if asset["source_root_id"] == "ocr_derived" and asset["relative_path"].startswith("OCR/"):
        return settings.ocr_derived_root / asset["relative_path"].removeprefix("OCR/")
    raise ValueError(f"unsupported asset root/path: {asset['asset_id']}")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _render_ocr(reader: easyocr.Reader, page) -> str:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(170 / 72.0, 170 / 72.0), alpha=False)
    image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
    result = reader.readtext(image, detail=1, paragraph=False)
    return grouped_text(result or [])


def benchmark() -> dict:
    settings = Settings.from_environment()
    sample = json.loads(SAMPLE_MANIFEST.read_text(encoding="utf-8"))
    assets = _load_jsonl(REGISTRY_ASSETS)
    reader = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
    records = []
    errors = []
    for item in sample["documents"]:
        original = assets[item["original_asset_id"]]
        processing = assets[item["processing_asset_id"]]
        original_doc = fitz.open(_resolve(original, settings))
        processing_doc = fitz.open(_resolve(processing, settings))
        for sample_page in item["sample_pages"]:
            page_number = int(sample_page.get("physical_page", sample_page["pdf_page"]))
            try:
                original_text = _normalize(original_doc[page_number - 1].get_text("text"))
                existing_text = _normalize(processing_doc[page_number - 1].get_text("text"))
                fresh_text = _normalize(_render_ocr(reader, original_doc[page_number - 1]))
                baseline_text = original_text if original_text else existing_text
                records.append({
                    "document_key": item["document_key"],
                    "pdf_page": page_number,
                    "physical_page": page_number,
                    "categories": sample_page["categories"],
                    "source_has_native_text": bool(original_text),
                    "fresh_easyocr": {
                        "char_count": len(fresh_text),
                        "text_sha256": _sha256_text(fresh_text),
                    },
                    "registered_processing_text": {
                        "char_count": len(existing_text),
                        "text_sha256": _sha256_text(existing_text),
                    },
                    "reference_text": {
                        "char_count": len(baseline_text),
                        "reference_kind": "native_pdf_text" if original_text else "registered_ocr_derivative_text",
                    },
                    "fresh_vs_registered_similarity": round(difflib.SequenceMatcher(None, fresh_text, existing_text).ratio(), 6),
                    "fresh_vs_reference_similarity": round(difflib.SequenceMatcher(None, fresh_text, baseline_text).ratio(), 6),
                    "status": "comparison_only_no_accuracy_claim",
                })
            except Exception as exc:  # pragma: no cover - defensive audit boundary
                errors.append({
                    "document_key": item["document_key"],
                    "pdf_page": page_number,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        original_doc.close()
        processing_doc.close()
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_easyocr_sample_benchmark",
        "audited_at": date.today().isoformat(),
        "engine": {
            "name": "easyocr",
            "languages": ["ch_sim", "en"],
            "gpu": False,
            "dpi": 170,
            "script": "scripts/benchmark_stage5_easyocr_sample.py::grouped_text",
        },
        "scope": "36 frozen Golden Sample pages; comparison baseline only",
        "records": records,
        "errors": errors,
        "actual": {
            "sample_page_count": len(records),
            "failed_page_count": len(errors),
            "native_text_page_count": sum(record["source_has_native_text"] for record in records),
            "scanned_or_derived_page_count": sum(not record["source_has_native_text"] for record in records),
        },
        "status": "comparison_ready_for_codex_review",
        "boundaries": [
            "Similarity to an OCR derivative is not ground-truth accuracy.",
            "Critical numeric, unit, negation and table values still require original-page review.",
            "This comparison does not choose a primary engine without the Stage 5 truth record.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data" / "stage5" / f"stage5_easyocr_sample_benchmark_{date.today().isoformat()}.json",
    )
    args = parser.parse_args()
    result = benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output), "pages": result["actual"]["sample_page_count"]}, ensure_ascii=False))
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Freeze the Stage 7 775-page terminology input boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf

from turbine_kg.documents.ids import stable_id
from turbine_kg.settings import Settings
from turbine_kg.terminology.validation import content_fingerprint, validate_input_manifest


ROOT = Path(__file__).resolve().parents[1]
STAGE5 = ROOT / "data" / "stage5"
STAGE6 = ROOT / "data" / "stage6"
OUT = ROOT / "data" / "stage7"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _asset_index() -> dict[str, dict]:
    return {
        row["asset_id"]: row
        for row in _jsonl(ROOT / "data" / "registry" / "source_assets.jsonl")
    }


def _sample_categories(sample: dict) -> dict[tuple[str, int], list[str]]:
    result = {}
    for document in sample["documents"]:
        for page in document["sample_pages"]:
            result[(document["document_key"], page["physical_page"])] = page.get("categories", [])
    return result


def _stage6_page_status() -> dict[tuple[str, int], str]:
    result: dict[tuple[str, int], str] = {}
    for row in _jsonl(STAGE6 / "stage6_evidence_bundle.jsonl"):
        evidence = row["evidence"]
        key = (row["document_key"], int(row["input"]["physical_page"]))
        current = result.get(key)
        if evidence["disposition"] == "region_scoped":
            result[key] = "accepted_region_evidence"
        elif evidence["disposition"] == "structured" and current != "accepted_region_evidence":
            result[key] = "accepted_text_evidence"
    return result


def _status(categories: list[str], page_mode: str, metrics: dict, stage6_status: str | None) -> tuple[str, str]:
    if stage6_status == "accepted_region_evidence":
        return "visual_only", "Stage 6 accepted this sample only as region-scoped Evidence."
    if any(value in categories for value in ("cover", "contents", "blank_or_low_text", "boundary_page")):
        return "excluded_non_content", "Sample review classified the page as metadata, navigation, or boundary-only."
    if page_mode == "review_required" or not metrics.get("text_chars", 0):
        return "quarantined", "No reliable processing text is available; retain the page for later review."
    if metrics.get("low_text_flag"):
        return "quarantined", "Stage 5 marked the page as low-text and it is not safe for terminology discovery."
    return "text_accepted", "Stage 5 processing text is available within the admitted five-unit scope."


def _processing_path(settings: Settings, relative_path: str) -> Path:
    # Registry keeps the logical OCR asset path under OCR/, while the
    # configured derived root stores the actual generated PDF outside SOURCE_ROOT.
    if relative_path.startswith("OCR/"):
        return settings.ocr_derived_root / relative_path.removeprefix("OCR/")
    return settings.source_root / relative_path


def main() -> None:
    settings = Settings.from_environment()
    sample = _read(STAGE5 / "stage5_sample_manifest.json")
    baseline = _read(STAGE5 / "stage5_baseline_benchmark_2026-09-10.json")
    assets = _asset_index()
    categories = _sample_categories(sample)
    stage6_pages = _stage6_page_status()
    baseline_pages = {(row["document_key"], int(row["physical_page"])): row for row in baseline["page_records"]}
    pages: list[dict] = []
    for document in sample["documents"]:
        processing = assets[document["processing_asset_id"]]
        authority = assets[document["original_asset_id"]]
        processing_path = _processing_path(settings, document["processing_relative_path"])
        with pymupdf.open(processing_path) as pdf:
            if len(pdf) != document["page_count"]:
                raise ValueError(f"page count mismatch for {document['document_key']}")
            for physical_page in range(1, len(pdf) + 1):
                baseline_row = baseline_pages[(document["document_key"], physical_page)]
                metrics = baseline_row["processing_metrics"]
                page_mode = baseline_row["page_mode"]
                stage6_status = stage6_pages.get((document["document_key"], physical_page))
                page_status, reason = _status(
                    categories.get((document["document_key"], physical_page), []),
                    page_mode,
                    metrics,
                    stage6_status,
                )
                text = pdf[physical_page - 1].get_text("text").strip()
                page = {
                    "page_id": stable_id("page", processing["revision_id"], physical_page - 1),
                    "document_key": document["document_key"],
                    "document_logical_id": processing["document_logical_id"],
                    "revision_id": processing["revision_id"],
                    "processing_asset_id": processing["asset_id"],
                    "authority_asset_id": authority["asset_id"],
                    "physical_page": physical_page,
                    "page_status": page_status,
                    "text_source": "native_pdf_text" if processing["asset_id"] == authority["asset_id"] else "validated_ocr_pdf_text",
                    "processing_relative_path": processing["relative_path"],
                    "authority_relative_path": authority["relative_path"],
                    "processing_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
                    "processing_text_chars": len(text),
                    "stage5_page_mode": page_mode,
                    "stage6_sample_status": stage6_status,
                    "exclusion_reason": None if page_status == "text_accepted" else reason,
                }
                pages.append(page)
    status_counts = {name: sum(row["page_status"] == name for row in pages) for name in ("text_accepted", "visual_only", "quarantined", "excluded_non_content")}
    payload = {
        "schema_version": 1,
        "stage": "7",
        "artifact_kind": "terminology_input_manifest",
        "status": "frozen",
        "scope": "the five admitted Stage 3 research-trial processing units; 775 physical PDF pages",
        "formal_release": False,
        "producer": "scripts/build_stage7_input_manifest.py",
        "consumer": ["turbine_kg.terminology.analyzer", "scripts/audit_stage7_exit.py", "Stage 8 ontology capability mapping"],
        "input_boundary": {
            "source_registry": "data/registry/source_registry_summary.json",
            "stage5_baseline": "data/stage5/stage5_baseline_benchmark_2026-09-10.json",
            "stage6_canonical_sample": "data/stage6/stage6_evidence_bundle.jsonl",
            "source_root": "SOURCE_ROOT",
            "source_count": 5,
            "page_count": 775,
            "unauthorized_source_count": 0,
        },
        "inputs": {
            "stage5_baseline_sha256": _sha(STAGE5 / "stage5_baseline_benchmark_2026-09-10.json"),
            "stage6_exit_sha256": _sha(STAGE6 / "stage6_exit_audit.json"),
            "registry_summary_sha256": _sha(ROOT / "data" / "registry" / "source_registry_summary.json"),
            "terminology_contract_sha256": _sha(ROOT / "config" / "terminology_contract.json"),
        },
        "rounds": {
            "round_1": "metadata and reliable native text discovery; candidate-only",
            "round_2": "accepted processing text from the frozen 775-page manifest",
        },
        "status_counts": status_counts,
        "pages": pages,
    }
    validate_input_manifest(payload)
    payload["content_fingerprint"] = content_fingerprint(payload)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "terminology_input_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "page_count": len(pages), "status_counts": status_counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()

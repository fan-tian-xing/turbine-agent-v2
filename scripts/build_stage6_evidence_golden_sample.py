"""Freeze the Stage 6 Evidence Golden Sample boundary from Stage 5 truth."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.settings import Settings
from scripts.build_stage6_golden_evidence import DOCUMENTS, _reviewed_documents
from stage5_fingerprint import ocr_fingerprint
from build_stage5_truth_annotations import _table_truth


ROOT = Path(__file__).resolve().parents[1]
STAGE5 = ROOT / "data" / "stage5"
STAGE6 = ROOT / "data" / "stage6"
MANIFEST = STAGE5 / "stage5_sample_manifest.json"
REGISTRY = ROOT / "data/registry/source_assets.jsonl"
FULL_REVIEW = ROOT / "data/registry/ocr_validation_report.json"
TRUTH = STAGE5 / "stage5_truth_annotations_2026-09-28.json"
TABLE_TRUTH = STAGE5 / "stage5_table_truth_review.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _eligibility(gate: str) -> str:
    return {
        "structured_text_candidate": "structured_candidate",
        "region_scoped_only": "region_scoped",
        "quarantine_structured_ocr": "quarantined",
        "metadata_only": "metadata_only",
        "navigation_only": "navigation_only",
        "boundary_exception": "boundary_only",
    }.get(gate, "needs_review")


def _validate_current_truth(truth: dict, manifest: dict, review: dict,
                            *, catalog, settings: Settings, input_hashes: dict) -> list[dict]:
    _reviewed_documents(manifest, review, catalog=catalog,
                        settings=settings, definitions=DOCUMENTS)
    if any(truth.get("inputs", {}).get(field) != digest for field, digest in input_hashes.items()):
        raise ValueError("Stage 5 truth is not bound to current manifest/Registry/full review")
    entries = {entry["document_key"]: entry for entry in manifest["documents"]}
    expected = {(entry["document_key"], int(page.get("physical_page", page["pdf_page"])))
                for entry in entries.values() for page in entry["sample_pages"]}
    actual = [(row["document_key"], int(row["physical_page"])) for row in truth["records"]]
    if (manifest.get("sample_page_count") != len(expected)
            or len(actual) != len(set(actual)) or set(actual) != expected):
        raise ValueError("Stage 5 truth sample-page coverage differs from current manifest")
    checked_pdfs = []
    for key, entry in entries.items():
        original = catalog.asset_for_id(entry["original_asset_id"])
        processing = catalog.asset_for_id(entry["processing_asset_id"])
        checked_pdfs.append({"document_key": key,
                             "original_asset_id": original.asset_id, "original_sha256": original.sha256,
                             "processing_asset_id": processing.asset_id, "processing_sha256": processing.sha256})
        for row in (row for row in truth["records"] if row["document_key"] == key):
            if row.get("truth_source") != "Original materials original PDF":
                raise ValueError(f"Stage 5 truth does not name the original PDF authority: {key}")
            if any(row.get(field) != value for field, value in checked_pdfs[-1].items()):
                raise ValueError(f"Stage 5 truth PDF identity differs from current reviewed source: {key}")
    return checked_pdfs


def main() -> None:
    truth_path = TRUTH
    table_path = TABLE_TRUTH
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    review = json.loads(FULL_REVIEW.read_text(encoding="utf-8"))
    input_hashes = {"stage5_sample_manifest_sha256": _sha256(MANIFEST),
                    "source_assets_sha256": _sha256(REGISTRY),
                    "full_corpus_reviews_sha256": _sha256(FULL_REVIEW)}
    catalog = load_identity_catalog(REGISTRY, ROOT / "config/revision_identity.tsv",
                                    ROOT / "config/derived_asset_links.tsv")
    reviewed_pdfs = _validate_current_truth(truth, manifest, review, catalog=catalog,
                                           settings=Settings.from_environment(), input_hashes=input_hashes)
    source_input_fingerprint, components = ocr_fingerprint()
    if (truth.get("source_input_fingerprint") != source_input_fingerprint
            or truth.get("source_fingerprint_components") != components
            or truth.get("inputs", {}).get("stage5_table_truth_review_sha256") != _sha256(table_path)):
        raise ValueError("current Stage 5 truth fingerprint or table truth binding differs")
    assets = {asset.asset_id: {"asset_id": asset.asset_id, "sha256": asset.sha256,
                              "source_root_id": asset.source_root_id,
                              "relative_path": asset.relative_path}
              for asset in catalog.assets}
    table_by_page = _table_truth(table_path, manifest=manifest, assets=assets,
                                 fingerprint=source_input_fingerprint, components=components,
                                 input_hashes=input_hashes)
    records = []
    for index, row in enumerate(truth["records"], start=1):
        table = table_by_page.get((row["document_key"], row["physical_page"]))
        disposition = _eligibility(row["gate_disposition"])
        # A quarantined table may have native text, but that text is not a
        # Stage 6 structured reference until cell/row/header truth is reviewed.
        text_available = bool(row.get("reference_text")) and disposition != "quarantined"
        records.append({
            "sample_id": f"stage6-sample-{index:03d}",
            "document_key": row["document_key"],
            "original_asset_id": row["original_asset_id"],
            "processing_asset_id": row["processing_asset_id"],
            "original_sha256": row["original_sha256"],
            "processing_sha256": row["processing_sha256"],
            "physical_page": row["physical_page"],
            "logical_page": row.get("logical_page"),
            "truth_source": row["truth_source"],
            "categories": row["categories"],
            "visual_review_status": row["visual_review_status"],
            "evidence_eligibility": disposition,
            "source_span_requirement": {
                "minimum": 1,
                "coordinates_required": disposition in {"structured_candidate", "region_scoped", "quarantined"},
            },
            "reference_text": row.get("reference_text", "") if text_available else None,
            "reference_text_sha256": row.get("reference_text_sha256") if text_available else None,
            "reference_tokens": row.get("reference_tokens") if text_available else None,
            "reference_kind": row["reference_kind"],
            "table_context": table,
            "review_boundary": (
                "quarantined_structured_regions_require_region_or_cell_review"
                if disposition == "quarantined"
                else "original_page_location_and_text_must_be_rechecked"
                if disposition in {"structured_candidate", "region_scoped"}
                else "visual_or_metadata_only; not_structured_evidence"
            ),
        })

    payload = {
        "schema_version": 1,
        "stage": "6",
        "artifact_kind": "stage6_evidence_golden_sample",
        "status": "complete_with_quarantine",
        "formal_release": False,
        "authority": "Stage 5 Original materials truth annotations plus original-page review",
        "contract": "config/evidence_contract.json",
        "inputs": {
            "source_input_fingerprint": source_input_fingerprint,
            "stage5_truth_annotations": str(truth_path.relative_to(ROOT)).replace("\\", "/"),
            "stage5_truth_annotations_sha256": _sha256(truth_path),
            "stage5_table_truth_review": str(table_path.relative_to(ROOT)).replace("\\", "/"),
            "stage5_table_truth_review_sha256": _sha256(table_path),
            "stage5_sample_manifest": str(MANIFEST.relative_to(ROOT)).replace("\\", "/"),
            "source_assets": str(REGISTRY.relative_to(ROOT)).replace("\\", "/"),
            "full_corpus_reviews": str(FULL_REVIEW.relative_to(ROOT)).replace("\\", "/"),
            **input_hashes,
            "reviewed_pdfs": reviewed_pdfs,
        },
        "producer": "scripts/build_stage6_evidence_golden_sample.py",
        "consumers": [
            "turbine_kg.evidence.builder",
            "turbine_kg.evidence.validation",
            "tests/stage6/test_stage6_golden_sample.py",
        ],
        "sample_page_count": len(records),
        "structured_candidate_count": sum(item["evidence_eligibility"] == "structured_candidate" for item in records),
        "region_scoped_count": sum(item["evidence_eligibility"] == "region_scoped" for item in records),
        "quarantined_count": sum(item["evidence_eligibility"] == "quarantined" for item in records),
        "text_reference_count": sum(item["reference_text"] is not None for item in records),
        "zero_tolerance": [
            "physical page must be present and one-based",
            "logical page must not be invented",
            "source text must be traceable to ordered SourceSpans",
            "quarantined table or complex layout must not become structured Evidence",
            "OCR similarity must not be treated as acceptance",
        ],
        "next_stage_allowed": "Stage 6 Evidence construction may consume accepted, original-page-verified regions; quarantined pages remain isolated.",
        "records": records,
    }
    STAGE6.mkdir(exist_ok=True)
    output = STAGE6 / "stage6_evidence_golden_sample.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

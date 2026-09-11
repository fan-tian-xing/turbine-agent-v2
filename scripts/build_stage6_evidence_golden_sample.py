"""Freeze the Stage 6 Evidence Golden Sample boundary from Stage 5 truth."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAGE5 = ROOT / "data" / "stage5"
STAGE6 = ROOT / "data" / "stage6"


def _latest(pattern: str) -> Path:
    matches = sorted(STAGE5.glob(pattern))
    if not matches:
        raise FileNotFoundError(pattern)
    return matches[-1]


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


def main() -> None:
    truth_path = _latest("stage5_truth_annotations_*.json")
    table_path = _latest("stage5_table_truth_review_*.json")
    exit_path = _latest("stage5_exit_audit_*.json")
    truth = json.loads(truth_path.read_text(encoding="utf-8"))
    table_review = json.loads(table_path.read_text(encoding="utf-8"))
    exit_audit = json.loads(exit_path.read_text(encoding="utf-8"))
    rapidocr_path = ROOT / exit_audit["artifact_provenance"]["rapidocr"]
    table_by_page = {
        (row["document_key"], row["physical_page"]): row
        for row in table_review.get("records", [])
    }
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
            "stage5_truth_annotations": str(truth_path.relative_to(ROOT)).replace("\\", "/"),
            "stage5_table_truth_review": str(table_path.relative_to(ROOT)).replace("\\", "/"),
            "stage5_exit_audit": str(exit_path.relative_to(ROOT)).replace("\\", "/"),
            "stage5_exit_audit_sha256": _sha256(exit_path),
            "stage5_rapidocr_benchmark": str(rapidocr_path.relative_to(ROOT)).replace("\\", "/"),
            "stage5_rapidocr_benchmark_sha256": _sha256(rapidocr_path),
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

"""Summarize Stage 5 table-structure candidates without claiming cell accuracy."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from turbine_kg.settings import PROJECT_ROOT
from stage5_fingerprint import ocr_fingerprint, sha256_file
from build_stage5_truth_annotations import _table_truth


STAGE5_ROOT = PROJECT_ROOT / "data" / "stage5"
BASELINE = STAGE5_ROOT / "stage5_baseline_benchmark.json"
MANIFEST = STAGE5_ROOT / "stage5_sample_manifest.json"
ASSETS = PROJECT_ROOT / "data/registry/source_assets.jsonl"
FULL_REVIEW = PROJECT_ROOT / "data/registry/ocr_validation_report.json"
TABLE_REVIEW = STAGE5_ROOT / "stage5_table_truth_review.json"


def _validate_baseline(baseline: dict, sample: dict, fingerprint: str, components: dict) -> None:
    if (baseline.get("input_fingerprint") != fingerprint
            or baseline.get("fingerprint_components") != components
            or baseline.get("errors") != []):
        raise ValueError("table baseline requires the complete baseline for current PDF bytes")
    expected = {(doc["document_key"], page) for doc in sample["documents"]
                for page in range(1, doc["page_count"] + 1)}
    rows = baseline.get("page_records", [])
    actual = {(row["document_key"], row["physical_page"]) for row in rows}
    documents = {row["document_key"]: row for row in baseline.get("documents", [])}
    selected = {row["asset_id"]: row for row in components["selected_assets"]}
    if (len(actual) != len(rows) or actual != expected
            or len(documents) != len(baseline.get("documents", []))
            or set(documents) != {doc["document_key"] for doc in sample["documents"]}
            or baseline.get("actual", {}).get("failed_page_count") != 0
            or baseline.get("actual", {}).get("page_count") != len(expected)):
        raise ValueError("table baseline input has incomplete, duplicated or failed page coverage")
    for document in sample["documents"]:
        row = documents[document["document_key"]]
        if (row.get("failed_pages") != [] or row.get("failed_page_count") != 0
                or row.get("processed_page_count") != document["page_count"]
                or any(row.get(role + "_sha256") != selected[document[role + "_asset_id"]]["sha256"]
                       for role in ("original", "processing"))):
            raise ValueError("table baseline document failures or PDF fingerprints do not reconcile")


def audit() -> dict:
    baseline_path = BASELINE
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    sample = json.loads(MANIFEST.read_text(encoding="utf-8"))
    fingerprint, components = ocr_fingerprint()
    _validate_baseline(baseline, sample, fingerprint, components)
    assets = {row["asset_id"]: row for line in ASSETS.read_text(encoding="utf-8").splitlines()
              if line.strip() for row in (json.loads(line),)}
    input_hashes = {"stage5_sample_manifest_sha256": sha256_file(MANIFEST),
                    "source_assets_sha256": sha256_file(ASSETS),
                    "full_corpus_reviews_sha256": sha256_file(FULL_REVIEW)}
    table_truth = _table_truth(TABLE_REVIEW, manifest=sample, assets=assets, fingerprint=fingerprint,
                              components=components, input_hashes=input_hashes)
    records = [
        record
        for record in baseline["page_records"]
        if record["is_golden_sample"]
        and set(record["sample_categories"]) & {"table", "continuation_table", "table_candidate", "complex_layout"}
    ]
    candidates = []
    for record in records:
        metrics = record["processing_metrics"]["visual_table_metrics"] or {}
        has_grid = bool(metrics.get("ruled_table_candidate"))
        if table_truth[(record["document_key"], record["physical_page"])]["role"] == "complex_layout_not_table":
            status = "complex_layout_not_table_reviewed"
            cell_status = "not_applicable_not_table"
        elif has_grid:
            status = "ruled_grid_candidate_manual_cell_truth_pending"
            cell_status = "not_scored_manual_truth_required"
        elif metrics.get("horizontal_rule_count", 0) >= 2:
            status = "partial_rules_manual_column_boundary_review_pending"
            cell_status = "not_scored_manual_truth_required"
        else:
            status = "no_rule_detected_manual_layout_review_pending"
            cell_status = "not_scored_manual_truth_required"
        candidates.append({
            "document_key": record["document_key"],
            "pdf_page": record["pdf_page"],
            "physical_page": record.get("physical_page", record["pdf_page"]),
            "sample_categories": record["sample_categories"],
            "page_mode": record["page_mode"],
            "pymupdf_table_count": record["processing_metrics"]["table_count_detected"],
            "visual_table_metrics": metrics,
            "status": status,
            "cell_text_accuracy_status": cell_status,
            "review_scope": "complex_layout_not_table" if "complex_layout" in record["sample_categories"] else "table_candidate",
            "user_escalation": False,
            "user_escalation_rule": "Escalate only if Codex cannot resolve a numeric, unit, negation, cell-boundary, or continuation-table question from the original page.",
        })
    return {
        "schema_version": 1,
        "stage": "5",
        "artifact_kind": "stage5_table_structure_baseline",
        "audited_at": date.today().isoformat(),
        "baseline": str(baseline_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "input_fingerprint": fingerprint,
        "fingerprint_components": components,
        "inputs": {**input_hashes, "baseline_sha256": sha256_file(BASELINE),
                   "table_truth_review_sha256": sha256_file(TABLE_REVIEW)},
        "scope": "Golden Sample table and continuation-table pages only",
        "candidate_count": len(candidates),
        "ruled_grid_candidate_count": sum(item["status"].startswith("ruled_grid") for item in candidates),
        "partial_rule_candidate_count": sum(item["status"].startswith("partial_rules") for item in candidates),
        "table_candidate_count": sum(item["review_scope"] == "table_candidate" for item in candidates),
        "complex_layout_not_table_count": sum(item["review_scope"] == "complex_layout_not_table" for item in candidates),
        "candidates": candidates,
        "status": "table_structure_baseline_ready_original_page_truth_recorded",
        "boundaries": [
            "Rule detection estimates candidate regions; it does not establish cell text or row/column accuracy.",
            "PyMuPDF returning zero tables is recorded as detector output, not as proof that no table exists.",
            "The original PDF page remains the authority for manual truth.",
        ],
    }


def main() -> int:
    output = STAGE5_ROOT / "stage5_table_baseline.json"
    result = audit()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output), "candidates": result["candidate_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

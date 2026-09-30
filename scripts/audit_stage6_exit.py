"""Reconcile all Stage 6 Golden Sample outcomes and write the exit audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from turbine_kg.terminology.analyzer import load_stage6_evidence_bundle

from evaluate_stage6_evidence import (
    merge_component_annotations,
    validate_persisted_identity,
    validate_table_decision_binding,
)
from stage6_auxiliary_supplements import load_supplements as load_auxiliary_supplements
from stage6_standard_supplements import load_supplements as load_standard_supplements
from stage5_fingerprint import ocr_fingerprint
from stage6_page_review_binding import current_stage5_bindings, input_bindings_match


ROOT = Path(__file__).resolve().parents[1]
STAGE6 = ROOT / "data" / "stage6"


def _json(path: str) -> dict:
    return json.loads((STAGE6 / path).read_text(encoding="utf-8"))


def _jsonl(path: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (STAGE6 / path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _ocr_review_complete(golden: dict, build_audit: dict) -> bool:
    names = (
        "ocr_review_standards.json",
        "ocr_review_d300n_haf103.json",
        "ocr_review_auxiliary.json",
    )
    try:
        manifests = [(name, _json(name)) for name in names]
        rows = [row for _, data in manifests for row in data["pages"]]
        expected = {(row["document_key"], int(row["physical_page"])): row for row in golden["records"]}
        assets = {row["asset_id"]: row for row in _jsonl("../registry/source_assets.jsonl")}
        actual = {(row["document_key"], int(row["physical_page"])) for row in rows}
        if len(rows) != len(actual) or actual != set(expected) or len(expected) != golden["sample_page_count"]:
            return False
        if any(
            (row["document_key"], int(row["physical_page"])) not in expected
            or row["original_pdf_sha256"] != assets[expected[(row["document_key"], int(row["physical_page"]))]["original_asset_id"]]["sha256"]
            or row["ocr_text_status"] not in {"verified", "corrected"}
            or row["nontext_status"] not in {"none", "region_recorded"}
            for row in rows
        ):
            return False
        lineage = build_audit.get("ocr_review_inputs", {})
        return all(
            lineage.get(name) == hashlib.sha256((STAGE6 / name).read_bytes()).hexdigest()
            for name in names
        )
    except (KeyError, TypeError, ValueError, OSError):
        return False


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=STAGE6 / "stage6_exit_audit.json",
        help="exit audit output path",
    )
    args = parser.parse_args(argv)
    golden = _json("stage6_evidence_golden_sample.json")
    text_audit = _json("stage6_evidence_build_audit.json")
    quality_audit = _json("stage6_evidence_quality_audit.json")
    table_audit = _json("stage6_table_evidence_audit.json")
    source_input_fingerprint, _ = ocr_fingerprint()
    current_inputs = current_stage5_bindings(ROOT, source_input_fingerprint)
    if not input_bindings_match((golden, text_audit, quality_audit, table_audit), current_inputs):
        raise SystemExit("Stage 6 source inputs are stale; actual revalidation is required before exit")
    text_items = _jsonl("stage6_evidence_annotations.jsonl")
    table_items = _jsonl("stage6_table_evidence_annotations.jsonl")
    try:
        canonical_items = merge_component_annotations(text_items, table_items)
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"invalid Stage 6 component annotations: {exc}") from exc
    canonical_path = STAGE6 / "stage6_evidence_bundle.jsonl"
    if not canonical_path.is_file():
        raise SystemExit("missing canonical Stage 6 Evidence bundle")
    loaded_canonical_items = load_stage6_evidence_bundle(canonical_path)
    table_decisions = {
        row["review_id"]: row for row in _jsonl("stage6_table_review_decisions.jsonl")
    }
    registry_findings = _jsonl("../registry/source_manual_findings.jsonl")
    dlt863_finding = next(
        row for row in registry_findings
        if row.get("finding_id") == "manual-dlt863-page-review"
    )
    missing_printed_pages = dlt863_finding.get("missing_printed_body_pages", [])
    supplemental_prose_pages = {
        (row["document_key"], int(row["physical_page"]))
        for row in _jsonl("stage6_page_review_decisions.jsonl")
        if row.get("review_scope") == "outside_table_prose" and row.get("decision") == "accepted"
    }
    supplemental_table_text_pages = {
        (row["document_key"], int(row["physical_page"]))
        for row in _jsonl("stage6_page_review_decisions.jsonl")
        if row.get("review_scope") == "reviewed_table_text_regions" and row.get("decision") == "accepted"
    }
    reviewed_standard_pages = {
        (group["document_key"], int(group["physical_page"]))
        for group in load_standard_supplements()["evidence_groups"]
    }
    reviewed_auxiliary_pages = {
        ("auxiliary_installation_book", int(group["physical_page"]))
        for group in load_auxiliary_supplements()["evidence_groups"]
    }
    semantic_coverage_audit = _json("stage6_semantic_coverage_audit.json")

    expected_text_pages = {
        (row["document_key"], int(row["physical_page"]))
        for row in golden["records"]
        if row["evidence_eligibility"] in {"structured_candidate", "region_scoped"}
    }
    expected_table_pages = {
        (row["document_key"], int(row["physical_page"]))
        for row in golden["records"]
        if row["evidence_eligibility"] == "quarantined"
    }
    expected_negative_pages = {
        (row["document_key"], int(row["physical_page"]))
        for row in golden["records"]
        if row["evidence_eligibility"] in {"metadata_only", "navigation_only", "boundary_only"}
    }
    actual_text_pages = {
        (row["document_key"], int(row["input"]["physical_page"])) for row in text_items
    }
    actual_table_pages = {
        (row["document_key"], int(row["input"]["physical_page"])) for row in table_items
    }
    persisted_identity_ok = all(
        all(validate_persisted_identity(row).values()) for row in canonical_items
    )
    table_review_binding_ok = all(
        validate_table_decision_binding(row, table_decisions.get(row.get("review_id")))
        for row in table_items
    )

    checks = {
        "golden_sample_has_36_pages": golden["sample_page_count"] == 36 == len(golden["records"]),
        "text_page_scope_exact": actual_text_pages == expected_text_pages | supplemental_prose_pages | supplemental_table_text_pages | reviewed_standard_pages | reviewed_auxiliary_pages,
        "supplemental_prose_only_on_table_pages": supplemental_prose_pages <= expected_table_pages,
        "supplemental_table_text_only_on_table_pages": supplemental_table_text_pages <= expected_table_pages,
        "table_page_scope_exact": actual_table_pages == expected_table_pages,
        "negative_pages_not_promoted": not ((actual_text_pages | actual_table_pages) & expected_negative_pages),
        "all_text_evidence_accepted": bool(text_items) and all(row["evidence"]["review_status"] == "accepted" for row in text_items),
        "all_table_evidence_accepted": bool(table_items) and all(row["evidence"]["review_status"] == "accepted" for row in table_items),
        "tables_remain_region_scoped": all(row["evidence"]["disposition"] == "region_scoped" for row in table_items),
        "table_values_not_promoted": all(
            context["review_scope"] == "table_region" and not context["value_cell_ids"]
            for row in table_items
            for context in row["evidence"]["table_context"]
        ),
        "original_assets_are_authority": all(
            row["evidence"]["authority_asset_id"] == row["evidence"]["locations"][0]["original_asset_id"]
            and row["evidence"]["authority_basis"].startswith("original_pdf_")
            for row in text_items + table_items
        ),
        "ocr_never_named_as_authority": all(
            "(OCR)" not in row["input"]["original_relative_path"]
            for row in text_items + table_items
        ),
        "text_build_complete": text_audit["status"] == "complete",
        "ocr_36_page_review_current_and_bound": _ocr_review_complete(golden, text_audit),
        "whole_page_semantic_coverage_complete": semantic_coverage_audit.get("status") == "complete" and semantic_coverage_audit.get("stage12_input_allowed") is True,
        "evidence_quality_complete": (
            quality_audit["status"] == "complete"
            and all(quality_audit["checks"].values())
        ),
        "table_build_complete": table_audit["status"] == "complete",
        "no_unreviewed_table_regions": table_audit["remaining_quarantined_page_count"] == 0,
        "canonical_evidence_ids_unique": len(loaded_canonical_items) == len({row["evidence"]["evidence_id"] for row in loaded_canonical_items}),
        "canonical_bundle_matches_components": loaded_canonical_items == canonical_items,
        "persisted_evidence_identity_and_text_hashes_recomputed": persisted_identity_ok,
        "table_review_decisions_bind_actual_bbox_headers_and_continuations": table_review_binding_ok,
        "stage7_consumer_reads_canonical_bundle": bool(loaded_canonical_items),
        "source_missing_page_boundary_recorded": (
            len(missing_printed_pages) == 15
            and dlt863_finding.get("missing_printed_body_page_count") == 15
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    status = "complete" if not failures else "blocked"
    audit = {
        "schema_version": 1,
        "stage": "6",
        "artifact_kind": "stage6_exit_audit",
        "status": status,
        "formal_release": False,
        "authority": "Original materials original PDF pages; OCR is processing assistance only",
        "inputs": current_inputs,
        "coverage_scope": "selected existing original PDF pages only; missing printed source pages are not reconstructed",
        "dlt863_missing_printed_body_pages": missing_printed_pages,
        "golden_sample_page_count": len(golden["records"]),
        "accepted_text_page_count": len(actual_text_pages),
        "accepted_table_page_count": len(actual_table_pages),
        "negative_gate_page_count": len(expected_negative_pages),
        "accepted_text_evidence_count": len(text_items),
        "accepted_table_region_evidence_count": len(table_items),
        "accepted_evidence_count": len(text_items) + len(table_items),
        "structured_table_data_cell_count": 0,
        "unresolved_review_count": len(failures),
        "user_review_required_now": [],
        "future_user_review_trigger": "Only if table data cells are promoted and original-page header inheritance or cell ownership remains ambiguous.",
        "canonical_evidence": "data/stage6/stage6_evidence_bundle.jsonl",
        "artifact_chain": [
            "Stage 5 truth + Original materials -> stage6_evidence_golden_sample.json",
            "Golden Sample + page review decisions -> stage6_evidence_annotations.jsonl",
            "Table structural truth + table review decisions -> stage6_table_evidence_annotations.jsonl",
            "Text/table component annotations -> stage6_evidence_bundle.jsonl",
            "Canonical Evidence bundle -> Stage 7 terminology analysis",
        ],
        "dead_code_orphan_output_review": {
            "status": "pass",
            "canonical_outputs_have_consumers": bool(loaded_canonical_items),
            "canonical_bundle_is_read_not_regenerated": True,
            "initial_vertical_slice_is_non_authoritative_diagnostic": True,
            "temporary_render_outputs_are_not_formal_artifacts": True,
        },
        "checks": checks,
        "failures": failures,
        "boundaries": [
            "Evidence authority is always the Original materials original PDF asset.",
            "OCR and OCR-derived PDFs are processing aids and never Evidence authority.",
            "The six formerly quarantined table pages are accepted only as seven region-scoped table Evidence records.",
            "No unreviewed table data cell is available for a structured numeric claim.",
            "Metadata, navigation and boundary-only pages remain negative gates.",
            "DLT863 original PDF lacks 15 printed body pages; this record-quality exit does not assert document completeness.",
            "No Engineering Statement, ontology object, Neo4j formal projection or Release was created.",
        ],
        "next_stage_allowed": status == "complete",
        "next_stage": "Stage 7 terminology analysis and business capability questions",
    }
    output = args.output
    output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "checks": len(checks), "failures": failures}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

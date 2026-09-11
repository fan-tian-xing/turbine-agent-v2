"""Evaluate Stage 6 Evidence against the frozen page Golden Sample."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAGE6 = ROOT / "data" / "stage6"


def _rows(name: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (STAGE6 / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _ratio(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": 1.0 if denominator == 0 else numerator / denominator,
    }


def main() -> None:
    golden = json.loads((STAGE6 / "stage6_evidence_golden_sample.json").read_text(encoding="utf-8"))
    annotations = _rows("stage6_evidence_annotations.jsonl")
    table_annotations = _rows("stage6_table_evidence_annotations.jsonl")
    review_queue = _rows("stage6_evidence_page_review_queue.jsonl")
    decisions = _rows("stage6_page_review_decisions.jsonl")
    table_decisions = _rows("stage6_table_review_decisions.jsonl")
    build_audit = json.loads((STAGE6 / "stage6_evidence_build_audit.json").read_text(encoding="utf-8"))
    table_audit = json.loads((STAGE6 / "stage6_table_evidence_audit.json").read_text(encoding="utf-8"))

    golden_by_page = {
        (row["document_key"], row["physical_page"]): row
        for row in golden["records"]
    }
    positive_pages = {
        key for key, row in golden_by_page.items()
        if row["evidence_eligibility"] in {"structured_candidate", "region_scoped"}
    }
    table_pages = {
        key for key, row in golden_by_page.items()
        if row["evidence_eligibility"] == "quarantined"
    }
    negative_pages = set(golden_by_page) - positive_pages - table_pages
    annotation_pages = {
        (row["document_key"], row["input"]["physical_page"])
        for row in annotations
    }
    table_annotation_pages = {
        (row["document_key"], row["input"]["physical_page"])
        for row in table_annotations
    }
    decision_by_id = {row["review_id"]: row for row in decisions}
    decision_ids = {row["review_id"] for row in decisions if row["decision"] == "accepted"}
    table_decision_ids = {
        row["review_id"] for row in table_decisions
        if row["decision"] == "accepted_region_scoped"
    }

    authority_pass = 0
    page_identity_pass = 0
    bbox_pass = 0
    text_integrity_pass = 0
    disposition_pass = 0
    review_pass = 0
    failures: list[str] = []
    evidence_ids: list[str] = []
    version_ids: list[str] = []
    annotations_by_page: dict[tuple[str, int], list[dict]] = defaultdict(list)
    all_annotations = annotations + table_annotations
    for row in all_annotations:
        evidence = row["evidence"]
        key = (row["document_key"], row["input"]["physical_page"])
        truth = golden_by_page[key]
        if row in annotations:
            annotations_by_page[key].append(row)
        evidence_ids.append(evidence["evidence_id"])
        version_ids.append(evidence["evidence_version_id"])

        authority_ok = (
            evidence["authority_asset_id"] == truth["original_asset_id"]
            and all(location["original_asset_id"] == truth["original_asset_id"] for location in evidence["locations"])
            and evidence["authority_basis"].startswith("original_pdf_")
        )
        authority_pass += authority_ok
        page_ok = all(
            location["physical_page"] == truth["physical_page"]
            and location["logical_page"] == truth["logical_page"]
            for location in evidence["locations"]
        )
        page_identity_pass += page_ok
        bbox_ok = bool(evidence["locations"]) and all(location["bbox"] is not None for location in evidence["locations"])
        bbox_pass += bbox_ok
        text_ok = (
            hashlib.sha256(evidence["source_text"].encode("utf-8")).hexdigest() == evidence["source_text_sha256"]
            and evidence["effective_text"] == evidence["source_text"]
        )
        text_integrity_pass += text_ok
        allowed = evidence["disposition"] == "region_scoped" if truth["evidence_eligibility"] in {"region_scoped", "quarantined"} else evidence["disposition"] in {"structured", "region_scoped"}
        disposition_pass += allowed
        review_id = row.get("page_review_id", row.get("review_id"))
        reviewed = evidence["review_status"] == "accepted" and review_id in (decision_ids | table_decision_ids)
        review_pass += reviewed
        if not all((authority_ok, page_ok, bbox_ok, text_ok, allowed, reviewed)):
            failures.append(evidence["evidence_id"])

    reviewed_page_fingerprint_pass = 0
    reference_token_pass = 0
    reference_token_page_count = 0
    for key, page_rows in annotations_by_page.items():
        review_id = page_rows[0]["page_review_id"]
        page_fingerprint = hashlib.sha256(
            "\x1f".join(row["evidence"]["evidence_id"] for row in page_rows).encode("utf-8")
        ).hexdigest()
        decision = decision_by_id.get(review_id)
        reviewed_page_fingerprint_pass += bool(
            decision
            and decision["decision"] == "accepted"
            and decision["expected_page_fingerprint"] == page_fingerprint
        )
        truth = golden_by_page[key]
        tokens = truth.get("reference_tokens")
        if tokens:
            reference_token_page_count += 1
            page_text = "\n".join(row["evidence"]["source_text"] for row in page_rows)
            expected_numbers = set(tokens.get("numbers", []))
            if truth.get("logical_page"):
                expected_numbers.discard(str(truth["logical_page"]))
            expected_units = set(tokens.get("units", []))
            expected_negations = set(tokens.get("negations", {}))
            token_ok = (
                all(number in page_text for number in expected_numbers)
                and all(unit in page_text for unit in expected_units)
                and all(value in page_text for value in expected_negations)
            )
            reference_token_pass += token_ok

    checks = {
        "positive_page_coverage": annotation_pages == positive_pages,
        "negative_pages_excluded": annotation_pages.isdisjoint(negative_pages),
        "reviewed_table_page_coverage": table_annotation_pages == table_pages,
        "negative_pages_excluded_from_table_evidence": table_annotation_pages.isdisjoint(negative_pages),
        "table_regions_are_not_in_text_evidence": annotation_pages.isdisjoint(table_pages),
        "no_remaining_review_queue": not review_queue,
        "all_table_regions_remain_non_cell_scoped": all(
            row["evidence"]["disposition"] == "region_scoped"
            and all(context["review_scope"] == "table_region" and not context["value_cell_ids"] for context in row["evidence"]["table_context"])
            for row in table_annotations
        ),
        "build_audit_complete": build_audit["status"] == "complete",
        "table_audit_complete": table_audit["status"] == "complete",
        "evidence_ids_unique": len(evidence_ids) == len(set(evidence_ids)),
        "evidence_version_ids_unique": len(version_ids) == len(set(version_ids)),
        "all_evidence_zero_tolerance_checks_pass": not failures,
        "all_accepted_page_fingerprints_match_reviewed_text_and_bboxes": reviewed_page_fingerprint_pass == len(positive_pages),
        "all_available_reference_numbers_units_and_negations_retained": reference_token_pass == reference_token_page_count,
    }
    audit = {
        "schema_version": 1,
        "stage": "6",
        "artifact_kind": "stage6_evidence_quality_audit",
        "status": "complete" if all(checks.values()) else "failed",
        "formal_release": False,
        "producer": "scripts/evaluate_stage6_evidence.py",
        "inputs": {
            "golden_sample": "data/stage6/stage6_evidence_golden_sample.json",
            "evidence_annotations": "data/stage6/stage6_evidence_annotations.jsonl",
            "table_evidence_annotations": "data/stage6/stage6_table_evidence_annotations.jsonl",
            "review_decisions": "data/stage6/stage6_page_review_decisions.jsonl",
            "table_review_decisions": "data/stage6/stage6_table_review_decisions.jsonl",
        },
        "counts": {
            "golden_pages": len(golden_by_page),
            "positive_pages": len(positive_pages),
            "accepted_text_evidence": sum(row["evidence"]["review_status"] == "accepted" for row in annotations),
            "accepted_table_region_evidence": sum(row["evidence"]["review_status"] == "accepted" for row in table_annotations),
            "reviewed_table_pages": len(table_pages),
            "negative_gate_pages": len(negative_pages),
        },
        "metrics": {
            "original_authority_integrity": _ratio(authority_pass, len(all_annotations)),
            "physical_logical_page_accuracy": _ratio(page_identity_pass, len(all_annotations)),
            "bbox_presence": _ratio(bbox_pass, len(all_annotations)),
            "source_effective_text_integrity": _ratio(text_integrity_pass, len(all_annotations)),
            "golden_disposition_compliance": _ratio(disposition_pass, len(all_annotations)),
            "accepted_review_binding": _ratio(review_pass, len(all_annotations)),
            "table_region_resolution": _ratio(len(table_annotation_pages), len(table_pages)),
            "reviewed_page_text_bbox_fingerprint_binding": _ratio(reviewed_page_fingerprint_pass, len(positive_pages)),
            "reference_number_unit_negation_retention": _ratio(reference_token_pass, reference_token_page_count),
        },
        "checks": checks,
        "failed_evidence_ids": failures,
        "reviewed_table_boundary": [
            {
                "document_key": key[0],
                "physical_page": key[1],
                "reason": golden_by_page[key]["review_boundary"],
            }
            for key in sorted(table_pages)
        ],
        "user_review_required_now": [],
        "future_user_review_trigger": "Only if a table region is later promoted to cell-level Evidence and a specific header/cell relation remains ambiguous after original-page review.",
        "boundaries": [
            "Original materials is the sole Evidence authority; OCR remains processing assistance.",
            "Formerly quarantined tables are accepted only as region-scoped Evidence; no data cells are promoted.",
            "No Engineering Statement, ontology object, Neo4j projection or Release was created.",
        ],
    }
    output = STAGE6 / "stage6_evidence_quality_audit.json"
    output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "counts": audit["counts"]}))


if __name__ == "__main__":
    main()

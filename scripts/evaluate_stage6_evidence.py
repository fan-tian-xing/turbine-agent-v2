"""Evaluate Stage 6 Evidence against the frozen page Golden Sample."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from turbine_kg.documents.ids import evidence_id, evidence_version_id
from turbine_kg.documents.models import BBox


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


def _bbox(value: dict | None) -> BBox | None:
    if value is None:
        return None
    return BBox(float(value["x0"]), float(value["y0"]), float(value["x1"]), float(value["y1"]))


def recompute_evidence_identity(row: dict) -> tuple[str, str]:
    """Recompute both Evidence identities from the persisted source fields."""

    evidence = row["evidence"]
    locations = evidence["locations"]
    physical_pages = tuple(int(location["physical_page"]) for location in locations)
    bboxes = tuple(_bbox(location["bbox"]) for location in locations)
    expected_id = evidence_id(
        evidence["revision_id"],
        physical_pages,
        bboxes,
        evidence["source_text"],
        evidence["evidence_role"],
    )
    expected_version_id = evidence_version_id(
        row["input"]["document_ir_output_fingerprint"],
        tuple(evidence["source_span_ids"]),
    )
    return expected_id, expected_version_id


def validate_persisted_identity(row: dict) -> dict[str, bool]:
    """Check the persisted identity and source text hash without rebuilding an IR."""

    evidence = row["evidence"]
    expected_id, expected_version_id = recompute_evidence_identity(row)
    return {
        "evidence_id": evidence.get("evidence_id") == expected_id,
        "evidence_version_id": evidence.get("evidence_version_id") == expected_version_id,
        "source_text_sha256": hashlib.sha256(evidence["source_text"].encode("utf-8")).hexdigest()
        == evidence.get("source_text_sha256"),
    }


def merge_component_annotations(text_rows: list[dict], table_rows: list[dict]) -> list[dict]:
    """Merge the two Stage 6 component streams into the canonical row order.

    The merge is deliberately narrow: it does not infer Evidence or rewrite a
    component.  It rejects duplicate IDs and any persisted identity/hash
    mismatch before a caller can write a canonical bundle.
    """

    merged = list(text_rows) + list(table_rows)
    evidence_ids: set[str] = set()
    version_ids: set[str] = set()
    for row in merged:
        checks = validate_persisted_identity(row)
        if not all(checks.values()):
            raise ValueError(f"persisted Stage 6 Evidence identity mismatch: {row['evidence'].get('evidence_id')}")
        evidence_id_value = row["evidence"]["evidence_id"]
        version_id_value = row["evidence"]["evidence_version_id"]
        if evidence_id_value in evidence_ids:
            raise ValueError(f"duplicate Stage 6 Evidence ID: {evidence_id_value}")
        if version_id_value in version_ids:
            raise ValueError(f"duplicate Stage 6 Evidence version ID: {version_id_value}")
        evidence_ids.add(evidence_id_value)
        version_ids.add(version_id_value)
    return merged


def validate_table_decision_binding(row: dict, decision: dict | None) -> bool:
    """Match a persisted table Evidence region to its reviewed table decision."""

    if decision is None or decision.get("decision") != "accepted_region_scoped":
        return False
    evidence = row["evidence"]
    locations = evidence.get("locations", [])
    contexts = evidence.get("table_context", [])
    if len(locations) != 1 or len(contexts) != 1:
        return False
    context = contexts[0]
    location_bbox = locations[0].get("bbox")
    if location_bbox != decision.get("bbox"):
        return False
    if context.get("table_label") != decision.get("table_label"):
        return False
    if context.get("header_hierarchy") != decision.get("header_hierarchy"):
        return False
    if context.get("inherited_header_text") != decision.get("inherited_header_text"):
        return False
    if context.get("continuation_from_physical_page") != decision.get("continuation_from_physical_page"):
        return False
    if context.get("continuation_to_physical_page") != decision.get("continuation_to_physical_page"):
        return False
    if context.get("leaf_column_count") != len(decision.get("leaf_headers", [])):
        return False
    return (
        context.get("review_scope") == "table_region"
        and not context.get("value_cell_ids")
        and evidence.get("disposition") == "region_scoped"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build-canonical",
        action="store_true",
        help="write the canonical bundle after merging and validating components",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=STAGE6 / "stage6_evidence_quality_audit.json",
        help="quality audit output path",
    )
    args = parser.parse_args(argv)
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
    table_decision_by_id = {row["review_id"]: row for row in table_decisions}

    authority_pass = 0
    page_identity_pass = 0
    bbox_pass = 0
    text_integrity_pass = 0
    disposition_pass = 0
    review_pass = 0
    identity_pass = 0
    version_identity_pass = 0
    source_hash_pass = 0
    table_decision_binding_pass = 0
    failures: list[str] = []
    evidence_ids: list[str] = []
    version_ids: list[str] = []
    annotations_by_page: dict[tuple[str, int], list[dict]] = defaultdict(list)
    all_annotations = annotations + table_annotations
    try:
        merged_annotations = merge_component_annotations(annotations, table_annotations)
    except (KeyError, TypeError, ValueError) as exc:
        merged_annotations = []
        failures.append(f"canonical_merge:{exc}")
    if args.build_canonical and merged_annotations:
        canonical_path = STAGE6 / "stage6_evidence_bundle.jsonl"
        canonical_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in merged_annotations),
            encoding="utf-8",
        )
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
        identity_checks = validate_persisted_identity(row)
        identity_pass += identity_checks["evidence_id"]
        version_identity_pass += identity_checks["evidence_version_id"]
        source_hash_pass += identity_checks["source_text_sha256"]
        table_binding = True
        if row in table_annotations:
            table_binding = validate_table_decision_binding(row, table_decision_by_id.get(review_id))
            table_decision_binding_pass += table_binding
        if not all((authority_ok, page_ok, bbox_ok, text_ok, allowed, reviewed, *identity_checks.values(), table_binding)):
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
        "persisted_evidence_ids_recomputed": identity_pass == len(all_annotations),
        "persisted_evidence_version_ids_recomputed": version_identity_pass == len(all_annotations),
        "persisted_source_text_hashes_recomputed": source_hash_pass == len(all_annotations),
        "table_review_decisions_bind_bbox_headers_and_continuations": table_decision_binding_pass == len(table_annotations),
        "canonical_components_merge_without_identity_errors": bool(merged_annotations),
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
            "persisted_evidence_identity": _ratio(identity_pass, len(all_annotations)),
            "persisted_evidence_version_identity": _ratio(version_identity_pass, len(all_annotations)),
            "persisted_source_text_hash": _ratio(source_hash_pass, len(all_annotations)),
            "table_review_decision_binding": _ratio(table_decision_binding_pass, len(table_annotations)),
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
    output = args.output
    output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "counts": audit["counts"]}))
    if audit["status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""Evaluate Stage 6 Evidence against the frozen page Golden Sample."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from turbine_kg.documents.ids import evidence_id, evidence_version_id
from turbine_kg.documents.models import BBox

from stage6_cross_page_supplements import load_supplements as load_cross_page_supplements
from stage6_auxiliary_supplements import load_supplements as load_auxiliary_supplements
from stage6_standard_supplements import load_supplements as load_standard_supplements
from stage6_visual_regions import load_regions as load_visual_regions
from stage6_page_review_binding import page_review_fingerprint, current_stage5_bindings, input_bindings_match
from stage5_fingerprint import ocr_fingerprint


ROOT = Path(__file__).resolve().parents[1]
STAGE6 = ROOT / "data" / "stage6"
REFERENCE_EXCLUSIONS = STAGE6 / "stage6_reference_token_exclusions.json"


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
    # Evidence IDs include BBox's serialized numeric representation. Preserve
    # reviewed integer coordinates instead of changing 539 to 539.0 here.
    return BBox(value["x0"], value["y0"], value["x1"], value["y1"])


def _intersects(left: dict, right: dict) -> bool:
    return (
        float(left["x0"]) < float(right["x1"])
        and float(right["x0"]) < float(left["x1"])
        and float(left["y0"]) < float(right["y1"])
        and float(right["y0"]) < float(left["y1"])
    )


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


def validate_visual_binding(row: dict, region: dict | None) -> bool:
    """Allow only a caption bound to a SHA-checked original drawing region."""
    if region is None or row.get("source_supplement_kind") != "reviewed_visual_only":
        return False
    evidence = row["evidence"]
    caption = region["caption"]
    locations = evidence.get("locations", [])
    contexts = evidence.get("figure_context", [])
    same_page = caption["physical_page"] == region["physical_page"]
    if len(locations) != 1 or len(contexts) != 1:
        return False
    location, context = locations[0], contexts[0]
    return (
        row["document_key"] == region["document_key"]
        and row["input"]["physical_page"] == region["physical_page"]
        and row.get("visual_region_id") == region["region_id"]
        and row.get("figure_physical_page") == region["physical_page"]
        and row.get("figure_bbox_pdf_pt") == region["figure_bbox_pdf_pt"]
        and row.get("caption_physical_page") == caption["physical_page"]
        and row.get("cross_page_caption") == (not same_page)
        and row.get("semantic_use") == "visual_context_only"
        and row.get("stage12_extractability") == "context_only"
        and evidence["disposition"] == "visual_only"
        and evidence["content_kind"] == "caption"
        and evidence["source_text"] == caption["text"]
        and evidence["effective_text"] == caption["text"]
        and len(evidence["source_span_ids"]) == 1
        and location["source_span_id"] == evidence["source_span_ids"][0]
        and location["physical_page"] == caption["physical_page"]
        and location["logical_page"] == caption["logical_page"]
        and isinstance(location.get("bbox"), dict)
        and all(abs(float(location["bbox"][key]) - value) < 0.1
                for key, value in zip(("x0", "y0", "x1", "y1"), caption["bbox_pdf_pt"]))
        and context["context_kind"] == "whole_figure"
        and bool(context.get("figure_id"))
        and context["caption_source_span_ids"] == (
            evidence["source_span_ids"] if same_page else []
        )
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
    reference_exclusions = json.loads(REFERENCE_EXCLUSIONS.read_text(encoding="utf-8"))
    annotations = _rows("stage6_evidence_annotations.jsonl")
    table_annotations = _rows("stage6_table_evidence_annotations.jsonl")
    review_queue = _rows("stage6_evidence_page_review_queue.jsonl")
    decisions = _rows("stage6_page_review_decisions.jsonl")
    table_decisions = _rows("stage6_table_review_decisions.jsonl")
    build_audit = json.loads((STAGE6 / "stage6_evidence_build_audit.json").read_text(encoding="utf-8"))
    table_audit = json.loads((STAGE6 / "stage6_table_evidence_audit.json").read_text(encoding="utf-8"))
    source_input_fingerprint, _ = ocr_fingerprint()

    golden_by_page = {
        (row["document_key"], row["physical_page"]): row
        for row in golden["records"]
    }
    exclusion_rows = reference_exclusions.get("records", [])
    exclusions_by_page = {
        (row["document_key"], row["physical_page"]): row for row in exclusion_rows
    }
    exclusions_valid = (
        reference_exclusions.get("schema_version") == 1
        and len(exclusions_by_page) == len(exclusion_rows)
        and all(
            key in golden_by_page
            and row.get("original_pdf_sha256") == golden_by_page[key]["original_sha256"]
            and row.get("review_scope") == "navigation_or_visual_only_region_checked_against_original"
            and bool(row.get("reason"))
            for key, row in exclusions_by_page.items()
        )
    )
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
    supplemental_prose_pages = {
        (row["document_key"], int(row["physical_page"]))
        for row in decisions
        if row.get("review_scope") == "outside_table_prose" and row.get("decision") == "accepted"
    }
    supplemental_table_text_pages = {
        (row["document_key"], int(row["physical_page"]))
        for row in decisions
        if row.get("review_scope") == "reviewed_table_text_regions" and row.get("decision") == "accepted"
    }
    standard_supplements = load_standard_supplements()
    reviewed_standard_pages = {
        (group["document_key"], int(group["physical_page"]))
        for group in standard_supplements["evidence_groups"]
    }
    auxiliary_supplements = load_auxiliary_supplements()
    reviewed_auxiliary_pages = {
        ("auxiliary_installation_book", int(group["physical_page"]))
        for group in auxiliary_supplements["evidence_groups"]
    }
    cross_page_units = {
        unit["source_unit_id"]: unit
        for unit in load_cross_page_supplements()["source_units"]
    }
    visual_regions = {
        region["region_id"]: region for region in load_visual_regions()["regions"]
    }
    table_boxes_by_page: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in table_decisions:
        table_boxes_by_page[(row["document_key"], int(row["physical_page"]))].append(row["bbox"])
    supplemental_prose_outside_tables = all(
        not _intersects(location["bbox"], table_bbox)
        for row in annotations
        if not row.get("source_supplement_kind")
        if (row["document_key"], row["input"]["physical_page"]) in supplemental_prose_pages
        for location in row["evidence"]["locations"]
        for table_bbox in table_boxes_by_page[(row["document_key"], row["input"]["physical_page"])]
    )
    supplemental_table_text_inside_tables = all(
        any(_intersects(location["bbox"], table_bbox) for table_bbox in table_boxes_by_page[(row["document_key"], row["input"]["physical_page"])])
        for row in annotations
        if not row.get("source_supplement_kind")
        if (row["document_key"], row["input"]["physical_page"]) in supplemental_table_text_pages
        for location in row["evidence"]["locations"]
    )

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
        source_supplement_kind = row.get("source_supplement_kind")
        cross_page_unit_id = row.get("cross_page_source_unit_id")
        visual_binding = validate_visual_binding(
            row, visual_regions.get(row.get("visual_region_id")),
        ) if source_supplement_kind == "reviewed_visual_only" else False
        if source_supplement_kind == "reviewed_visual_only":
            page_ok = visual_binding
        elif source_supplement_kind == "cross_page_reviewed" and cross_page_unit_id in cross_page_units:
            unit = cross_page_units[cross_page_unit_id]
            expected_pages = {
                (fragment["physical_page"], fragment["logical_page"])
                for fragment in unit["fragments"]
            }
            page_ok = (
                unit["document_key"] == row["document_key"]
                and truth["physical_page"] in unit["physical_pages"]
                and {(location["physical_page"], location["logical_page"])
                     for location in evidence["locations"]} == expected_pages
            )
        else:
            page_ok = all(
                location["physical_page"] == truth["physical_page"]
                and location["logical_page"] == truth["logical_page"]
                for location in evidence["locations"]
            )
            if cross_page_unit_id is not None:
                page_ok = False
        page_identity_pass += page_ok
        bbox_ok = bool(evidence["locations"]) and all(location["bbox"] is not None for location in evidence["locations"])
        bbox_pass += bbox_ok
        text_ok = (
            hashlib.sha256(evidence["source_text"].encode("utf-8")).hexdigest() == evidence["source_text_sha256"]
            and evidence["effective_text"] == evidence["source_text"]
        )
        text_integrity_pass += text_ok
        if source_supplement_kind == "reviewed_visual_only":
            allowed = visual_binding
        elif source_supplement_kind == "cross_page_reviewed":
            allowed = cross_page_unit_id in cross_page_units and evidence["disposition"] in {"structured", "region_scoped"}
        elif source_supplement_kind == "standard_reviewed":
            allowed = key in reviewed_standard_pages and evidence["disposition"] in {"structured", "region_scoped"}
        elif source_supplement_kind == "auxiliary_reviewed":
            allowed = key in reviewed_auxiliary_pages and (
                evidence["disposition"] == "region_scoped"
                or (key == ("auxiliary_installation_book", 480)
                    and evidence["content_kind"] == "paragraph"
                    and evidence["disposition"] == "structured")
            )
        else:
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
    checked_exclusion_pages = set()
    for key, page_rows in annotations_by_page.items():
        review_id = page_rows[0]["page_review_id"]
        page_fingerprint = page_review_fingerprint([row["evidence"] for row in page_rows],
                                                   source_input_fingerprint=source_input_fingerprint)
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
            page_text = re.sub(r"\s+", "", "\n".join(
                row["evidence"]["source_text"] for row in page_rows))
            expected_numbers = set(tokens.get("numbers", []))
            if truth.get("logical_page"):
                expected_numbers.discard(str(truth["logical_page"]))
            expected_units = set(tokens.get("units", []))
            expected_negations = set(tokens.get("negations", {}))
            missing = {
                "numbers": sorted(value for value in expected_numbers if re.sub(r"\s+", "", value) not in page_text),
                "units": sorted(value for value in expected_units if re.sub(r"\s+", "", value) not in page_text),
                "negations": sorted(value for value in expected_negations if re.sub(r"\s+", "", value) not in page_text),
            }
            exclusion = exclusions_by_page.get(key)
            if exclusion:
                checked_exclusion_pages.add(key)
            token_ok = (
                all(not values for values in missing.values()) if exclusion is None
                else missing == exclusion.get("excluded_reference_tokens")
                and all(
                    re.sub(r"\s+", "", value) in re.sub(r"\s+", "", truth["reference_text"])
                    for values in missing.values() for value in values
                )
            )
            reference_token_pass += token_ok
    exclusions_valid = exclusions_valid and checked_exclusion_pages == set(exclusions_by_page)

    checks = {
        "positive_page_coverage": annotation_pages == positive_pages | supplemental_prose_pages | supplemental_table_text_pages | reviewed_standard_pages | reviewed_auxiliary_pages,
        "supplemental_prose_only_on_reviewed_table_pages": supplemental_prose_pages <= table_pages,
        "supplemental_table_text_only_on_reviewed_table_pages": supplemental_table_text_pages <= table_pages,
        "negative_pages_excluded": annotation_pages.isdisjoint(negative_pages),
        "reviewed_table_page_coverage": table_annotation_pages == table_pages,
        "negative_pages_excluded_from_table_evidence": table_annotation_pages.isdisjoint(negative_pages),
        "table_regions_are_not_in_text_evidence": supplemental_prose_outside_tables,
        "reviewed_table_text_is_inside_reviewed_table_region": supplemental_table_text_inside_tables,
        "no_remaining_review_queue": not review_queue,
        "all_table_regions_remain_non_cell_scoped": all(
            row["evidence"]["disposition"] == "region_scoped"
            and all(context["review_scope"] == "table_region" and not context["value_cell_ids"] for context in row["evidence"]["table_context"])
            for row in table_annotations
        ),
        "build_audit_complete": build_audit["status"] == "complete",
        "reviewed_pdf_inputs_current": input_bindings_match(
            (golden, build_audit, table_audit), current_stage5_bindings(ROOT, source_input_fingerprint)),
        "table_audit_complete": table_audit["status"] == "complete",
        "evidence_ids_unique": len(evidence_ids) == len(set(evidence_ids)),
        "evidence_version_ids_unique": len(version_ids) == len(set(version_ids)),
        "all_evidence_zero_tolerance_checks_pass": not failures,
        "all_accepted_page_fingerprints_match_reviewed_text_and_bboxes": reviewed_page_fingerprint_pass == len(annotation_pages),
        "all_available_reference_numbers_units_and_negations_retained": reference_token_pass == reference_token_page_count,
        "reference_token_exclusions_bound_to_reviewed_pages": exclusions_valid,
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
            **current_stage5_bindings(ROOT, source_input_fingerprint),
            "golden_sample": "data/stage6/stage6_evidence_golden_sample.json",
            "evidence_annotations": "data/stage6/stage6_evidence_annotations.jsonl",
            "table_evidence_annotations": "data/stage6/stage6_table_evidence_annotations.jsonl",
            "review_decisions": "data/stage6/stage6_page_review_decisions.jsonl",
            "reference_token_exclusions": "data/stage6/stage6_reference_token_exclusions.json",
            "table_review_decisions": "data/stage6/stage6_table_review_decisions.jsonl",
        },
        "counts": {
            "golden_pages": len(golden_by_page),
            "positive_pages": len(positive_pages),
            "supplemental_prose_pages": len(supplemental_prose_pages),
            "supplemental_table_text_pages": len(supplemental_table_text_pages),
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
            "reviewed_page_text_bbox_fingerprint_binding": _ratio(reviewed_page_fingerprint_pass, len(annotation_pages)),
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
    if args.build_canonical and audit["status"] == "complete":
        canonical_path = STAGE6 / "stage6_evidence_bundle.jsonl"
        canonical_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in merged_annotations),
            encoding="utf-8",
        )
    output = args.output
    output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "counts": audit["counts"]}))
    if audit["status"] != "complete":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

import json
import copy
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))
from evaluate_stage6_evidence import (  # noqa: E402
    merge_component_annotations,
    validate_persisted_identity,
    validate_table_decision_binding,
)


ROOT = Path(__file__).parents[2]


def _sample():
    return json.loads((ROOT / "data/stage6/stage6_evidence_golden_sample.json").read_text(encoding="utf-8"))


def test_stage6_golden_sample_is_frozen_to_stage5_scope():
    sample = _sample()
    assert sample["status"] == "complete_with_quarantine"
    assert sample["formal_release"] is False
    assert sample["sample_page_count"] == 36
    assert sample["structured_candidate_count"] == 15
    assert sample["region_scoped_count"] == 7
    assert sample["quarantined_count"] == 6
    assert sample["text_reference_count"] == 10
    assert len(sample["records"]) == 36


def test_stage6_golden_sample_keeps_quarantine_and_page_contract():
    sample = _sample()
    for record in sample["records"]:
        assert record["physical_page"] >= 1
        if record["logical_page"] is None:
            assert record["logical_page"] is None
        if record["evidence_eligibility"] == "quarantined":
            assert record["reference_text"] is None
            assert "quarantined" in record["review_boundary"]
        if record["reference_text"] is None:
            assert record["reference_text_sha256"] is None


def test_stage6_golden_sample_declares_producer_and_consumers():
    sample = _sample()
    assert sample["producer"]
    assert set(sample["consumers"]) >= {
        "turbine_kg.evidence.builder",
        "turbine_kg.evidence.validation",
    }
    assert sample["inputs"]["stage5_exit_audit_sha256"]
    assert sample["zero_tolerance"]


def test_evidence_contract_manifest_names_runtime_source_and_original_authority():
    contract = json.loads((ROOT / "config/evidence_contract.json").read_text(encoding="utf-8"))
    assert contract["runtime_source_of_truth"].startswith("turbine_kg.evidence.models")
    assert contract["requirements"]["authority_asset_must_be_original_material"] is True
    assert contract["requirements"]["processing_asset_is_never_evidence_authority"] is True
    assert contract["requirements"]["evidence_and_version_ids_are_recomputed_during_validation"] is True


def test_stage6_vertical_slice_is_candidate_only_and_keeps_quarantine():
    audit = json.loads((ROOT / "data/stage6/stage6_vertical_slice_audit.json").read_text(encoding="utf-8"))
    candidates = [
        json.loads(line)
        for line in (ROOT / "data/stage6/stage6_evidence_candidate_annotations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    queue = [
        json.loads(line)
        for line in (ROOT / "data/stage6/stage6_evidence_review_queue.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert audit["status"] == "complete_initial_slice"
    assert audit["formal_release"] is False
    assert len(candidates) == 3
    assert sum(row["evidence"]["review_status"] == "accepted" for row in candidates) == 3
    assert sum(row["evidence"]["review_status"] == "needs_review" for row in candidates) == 0
    assert sum(row["evidence"]["disposition"] == "structured" for row in candidates) == 0
    assert sum(row["evidence"]["disposition"] == "region_scoped" for row in candidates) == 3
    assert all(row["evidence"]["authority_basis"] == "original_pdf_visual_review" for row in candidates)
    assert all(row["evidence"]["authority_asset_id"] == row["evidence"]["locations"][0]["original_asset_id"] for row in candidates)
    assert all("original_relative_path" in row["input"] for row in candidates)
    assert any(row["decision"] == "quarantined" for row in queue)
    assert all("statement" not in json.dumps(row, ensure_ascii=False).lower() for row in candidates)


def test_full_golden_evidence_covers_every_positive_page_and_keeps_tables_out():
    sample = _sample()
    annotations = [
        json.loads(line)
        for line in (ROOT / "data/stage6/stage6_evidence_annotations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pages = {(row["document_key"], row["input"]["physical_page"]) for row in annotations}
    positive = {
        (row["document_key"], row["physical_page"])
        for row in sample["records"]
        if row["evidence_eligibility"] in {"structured_candidate", "region_scoped"}
    }
    quarantined = {
        (row["document_key"], row["physical_page"])
        for row in sample["records"]
        if row["evidence_eligibility"] == "quarantined"
    }
    assert pages == positive
    assert pages.isdisjoint(quarantined)
    assert len(annotations) == 280
    assert all(row["evidence"]["review_status"] == "accepted" for row in annotations)
    assert all(row["evidence"]["authority_asset_id"] == row["input"]["authority_asset_id"] for row in annotations)


def test_rotated_auxiliary_pages_carry_explicit_original_rotation_mapping():
    annotations = [
        json.loads(line)
        for line in (ROOT / "data/stage6/stage6_evidence_annotations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip() and '"document_key": "auxiliary_installation_book"' in line
    ]
    assert annotations
    for row in annotations:
        assert row["input"]["authority_rotation_deg"] == 90
        assert row["input"]["coordinate_transform"] == "aligned_display_pdf_points_with_explicit_authority_rotation_v1"
        assert all(location["authority_rotation_deg"] == 90 for location in row["evidence"]["locations"])


def test_stage6_quality_audit_closes_after_region_scoped_table_review():
    audit = json.loads((ROOT / "data/stage6/stage6_evidence_quality_audit.json").read_text(encoding="utf-8"))
    assert audit["status"] == "complete"
    assert audit["counts"] == {
        "golden_pages": 36,
        "positive_pages": 22,
        "accepted_text_evidence": 280,
        "accepted_table_region_evidence": 7,
        "reviewed_table_pages": 6,
        "negative_gate_pages": 8,
    }
    assert all(value is True for value in audit["checks"].values())
    assert all(value["rate"] == 1.0 for value in audit["metrics"].values())
    assert audit["user_review_required_now"] == []


def test_component_annotations_rebuild_canonical_rows_and_recompute_persisted_identity():
    text_rows = [
        json.loads(line)
        for line in (ROOT / "data/stage6/stage6_evidence_annotations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    table_rows = [
        json.loads(line)
        for line in (ROOT / "data/stage6/stage6_table_evidence_annotations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    merged = merge_component_annotations(text_rows, table_rows)
    canonical = [
        json.loads(line)
        for line in (ROOT / "data/stage6/stage6_evidence_bundle.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert merged == canonical
    assert all(all(validate_persisted_identity(row).values()) for row in merged)


def test_persisted_evidence_identity_and_hash_tampering_is_rejected():
    row = json.loads(
        next(
            line
            for line in (ROOT / "data/stage6/stage6_evidence_annotations.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    )
    altered = copy.deepcopy(row)
    altered["evidence"]["source_text"] += "篡改"
    checks = validate_persisted_identity(altered)
    assert checks["evidence_id"] is False
    assert checks["source_text_sha256"] is False
    with pytest.raises(ValueError, match="identity mismatch"):
        merge_component_annotations([altered], [])


def test_table_review_decision_binds_actual_region_and_table_structure():
    rows = [
        json.loads(line)
        for line in (ROOT / "data/stage6/stage6_table_evidence_annotations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    decisions = {
        row["review_id"]: row
        for row in (
            json.loads(line)
            for line in (ROOT / "data/stage6/stage6_table_review_decisions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    row = rows[0]
    assert validate_table_decision_binding(row, decisions[row["review_id"]])

    altered = copy.deepcopy(row)
    altered["evidence"]["locations"][0]["bbox"]["x0"] += 1
    assert not validate_table_decision_binding(altered, decisions[row["review_id"]])

    altered = copy.deepcopy(row)
    altered["evidence"]["table_context"][0]["continuation_to_physical_page"] = 999
    assert not validate_table_decision_binding(altered, decisions[row["review_id"]])

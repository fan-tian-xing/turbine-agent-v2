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
    records = sample["records"]
    assert len(records) == sample["sample_page_count"]
    for eligibility, count_field in (
        ("structured_candidate", "structured_candidate_count"),
        ("region_scoped", "region_scoped_count"),
        ("quarantined", "quarantined_count"),
    ):
        assert sample[count_field] == sum(
            record["evidence_eligibility"] == eligibility for record in records
        )
    assert sample["text_reference_count"] == sum(
        record["reference_text"] is not None for record in records
    )
    assert len({(record["document_key"], record["physical_page"]) for record in records}) == len(records)


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
    assert sample["inputs"]["stage5_truth_annotations"]
    assert sample["zero_tolerance"]


def test_evidence_contract_manifest_names_runtime_source_and_original_authority():
    contract = json.loads((ROOT / "config/evidence_contract.json").read_text(encoding="utf-8"))
    assert contract["runtime_source_of_truth"].startswith("turbine_kg.evidence.models")
    assert contract["requirements"]["authority_asset_must_be_original_material"] is True
    assert contract["requirements"]["processing_asset_is_never_evidence_authority"] is True
    assert contract["requirements"]["evidence_and_version_ids_are_recomputed_during_validation"] is True


def test_reviewed_table_prose_and_table_items_have_distinct_source_regions():
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
    table_annotations = [
        json.loads(line)
        for line in (ROOT / "data/stage6/stage6_table_evidence_annotations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    table_pages = {
        (row["document_key"], row["input"]["physical_page"])
        for row in table_annotations
    }
    assert pages == positive | table_pages
    assert pages & quarantined == table_pages
    supplementary = [row for row in annotations if (row["document_key"], row["input"]["physical_page"]) == ("HAF103", 29)]
    assert supplementary
    assert any("正常运行或预计运行事件两类状态" in row["evidence"]["effective_text"] for row in supplementary)
    assert any(row["evidence"]["effective_text"].startswith("预计运行事件\n") for row in supplementary)
    table_items = [row for row in annotations if (row["document_key"], row["input"]["physical_page"]) == ("D300N", 50)]
    row_six = next(row for row in table_items if row["evidence"]["effective_text"].startswith("6\n转子装配"))
    assert "g)推力轴承安装调整:" in row_six["evidence"]["effective_text"]
    assert {location["physical_page"] for location in row_six["evidence"]["locations"]} == {49, 50}
    row_eight = next(row for row in table_items if row["evidence"]["effective_text"].startswith("8\n轴承箱上半装配"))
    records_column = next(row for row in table_items if row["evidence"]["effective_text"].startswith("前轴承箱和中低压轴"))
    assert set(row_eight["evidence"]["source_span_ids"]).isdisjoint(records_column["evidence"]["source_span_ids"])
    assert row_eight["evidence"]["locations"][0]["bbox"]["x1"] < records_column["evidence"]["locations"][0]["bbox"]["x0"]
    assert all(row["evidence"]["content_kind"] == "paragraph" for row in supplementary)
    assert all(not row["evidence"]["table_context"] for row in supplementary)
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
        assert row["input"]["coordinate_transform"] == "document_ir_canonical_pdf_points_v1"
        assert all(location["authority_rotation_deg"] == 90 for location in row["evidence"]["locations"])
        assert all(
            location["coordinate_transform"] == row["input"]["coordinate_transform"]
            for location in row["evidence"]["locations"]
        )


def test_stage6_quality_audit_closes_after_region_scoped_table_review():
    audit = json.loads((ROOT / "data/stage6/stage6_evidence_quality_audit.json").read_text(encoding="utf-8"))
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
    assert audit["status"] == "complete"
    assert audit["counts"]["golden_pages"] == _sample()["sample_page_count"]
    assert audit["counts"]["accepted_text_evidence"] == len(text_rows)
    assert audit["counts"]["accepted_table_region_evidence"] == len(table_rows)
    assert audit["counts"]["reviewed_table_pages"] == len({
        (row["document_key"], row["input"]["physical_page"]) for row in table_rows
    })
    assert all(row["evidence"]["review_status"] == "accepted" for row in text_rows + table_rows)
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

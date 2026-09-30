"""Original-PDF source and Evidence checks for independent standard supplements."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.documents.page_identity import apply_page_identity
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.evidence import validate_evidence_bundle
from turbine_kg.evidence.models import EvidenceBundle

from scripts.stage6_standard_supplements import (
    append_reviewed_source_spans,
    blank_cell_coordinates,
    build_reviewed_group_evidence,
    build_reviewed_row_evidence,
    build_reviewed_unit_evidence,
    evidence_groups_for_page,
    form_schema_for_page,
    load_supplements,
    source_units_for_page,
)


ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = ROOT.parent / "Original materials" / "标准法规"
FILES = {
    "DL5190.3": "DL5190.3—2019电力建设施工技术规范第3部分：汽轮发电机组.pdf",
    "DLT863": "DLT 863-2016汽轮机启动调试导则.pdf",
}


@pytest.fixture(scope="module")
def manifest():
    return load_supplements()


@pytest.fixture(scope="module")
def catalog():
    return load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )


def _source_page(catalog, key: str, physical_page: int, logical_page: str):
    filename = FILES[key]
    relative = f"标准法规/{filename}"
    ir = parse_registered_pdf(
        ORIGINAL / filename, relative, catalog,
        title=key, page_indices=(physical_page - 1,),
        parser_version="test-stage6-standard-supplements-v1",
    )
    return apply_page_identity(ir, {physical_page: logical_page})


def test_original_page_manifest_keeps_blank_template_cells_out_of_observations(manifest):
    assert len(manifest["form_schema"]) == 12
    assert len(form_schema_for_page(manifest, "DLT863", 27)) == 2
    assert len(form_schema_for_page(manifest, "DLT863", 28)) == 10
    assert len(manifest["blank_cell_rules"]) == 8
    assert len(blank_cell_coordinates(manifest)) == 299
    assert all(field["value"] is None and field["is_observation"] is False for field in manifest["form_schema"])
    assert all(unit["semantic_use"] == "form_template_context_only"
               for unit in manifest["source_units"] if unit["document_key"] == "DLT863")
    figure_3 = next(row for row in manifest["visual_regions"] if row["figure_label"] == "图4.5.8-3")
    assert figure_3["physical_page"] == 25
    assert figure_3["cross_page_caption"]["physical_page"] == 26
    assert not any(unit["document_key"] == "DLT863" and unit["physical_page"] in (17, 18, 19)
                   for unit in manifest["source_units"])


def test_evidence_groups_partition_all_nonempty_units(manifest):
    groups = manifest["evidence_groups"]
    assert len(groups) == 104
    assert len(evidence_groups_for_page(manifest, "DL5190.3", 113)) == 15
    assert len(evidence_groups_for_page(manifest, "DLT863", 27)) == 66
    assert len(evidence_groups_for_page(manifest, "DLT863", 28)) == 23
    assigned = [uid for group in groups for uid in group["source_unit_ids"]]
    assert len(assigned) == len(set(assigned)) == len(manifest["source_units"]) == 222
    assert set(assigned) == {unit["unit_id"] for unit in manifest["source_units"]}
    assert all(group["semantic_use"] == "form_template_context_only"
               for group in groups if group["document_key"] == "DLT863")


def test_dl5190_native_prose_is_reused_and_cells_are_validated(manifest, catalog):
    ir = _source_page(catalog, "DL5190.3", 113, "102")
    adapted, links = append_reviewed_source_spans(ir, "DL5190.3", manifest=manifest)
    units = source_units_for_page(manifest, "DL5190.3", 113)
    assert len(adapted.source_spans) == len(ir.source_spans) + 5
    line_above_figure = next(u for u in units if u["unit_id"] == "dl5190-p113-native-09")
    assert line_above_figure["bbox"][3] < 511.92
    assert links[line_above_figure["unit_id"]].get("correction_id")
    assert len(adapted.figures) == len(ir.figures)  # Existing image bbox is reused.
    assert all(links[u["unit_id"]]["source_span_id"] in {s.source_span_id for s in ir.source_spans}
               for u in units if u["reuse_existing"])
    evidence = [build_reviewed_unit_evidence(adapted, u, links[u["unit_id"]], manifest=manifest) for u in units]
    assert len(evidence) == len(units)
    assert all(item.authority_asset_id == item.locations[0].original_asset_id for item in evidence)
    assert {item.source_text for item in evidence if "ƒ≤S/" in item.source_text} == {
        "低定位精度要求ƒ≤S/500", "中定位精度要求ƒ≤S/750", "高定位精度要求ƒ≤S/1000",
    }


@pytest.mark.parametrize("physical_page,logical_page", [(27, "38"), (28, "39")])
def test_dlt_form_cells_and_combined_parameter_rows_validate(manifest, catalog, physical_page, logical_page):
    ir = _source_page(catalog, "DLT863", physical_page, logical_page)
    adapted, links = append_reviewed_source_spans(ir, "DLT863", manifest=manifest)
    units = source_units_for_page(manifest, "DLT863", physical_page)
    evidence = [build_reviewed_unit_evidence(adapted, u, links[u["unit_id"]], manifest=manifest) for u in units]
    assert len(evidence) == len(units)
    assert all(item.disposition == "region_scoped" and item.evidence_role == "requirement_source"
               and item.correction_ids for item in evidence)
    if physical_page == 27:
        pair = [u for u in units if u.get("table_key") == "dlt863-d1-p27"
                and u.get("row_index") == 1 and u.get("column_index") in (0, 1)]
        pair.sort(key=lambda row: row["column_index"])
        row_evidence = build_reviewed_row_evidence(adapted, pair, links, manifest=manifest)
        assert row_evidence.source_text == "汽包/分离器压力\nMPa"
        assert len(row_evidence.locations) == 2
    else:
        pair = [u for u in units if u.get("table_key") == "dlt863-d1-p28"
                and u.get("row_index") == 1 and u.get("column_index") in (4, 5)]
        pair.sort(key=lambda row: row["column_index"])
        row_evidence = build_reviewed_row_evidence(adapted, pair, links, manifest=manifest)
        assert row_evidence.source_text == "六段抽汽压力\nMPa"
        printed = next(u for u in units if u["source_text"] == "3000r/min")
        assert printed["row_index"] == 1 and printed["column_index"] == 2
        assert not any(field["is_observation"] for field in form_schema_for_page(manifest, "DLT863", 28))
        date_fields = [item for item in blank_cell_coordinates(manifest, "DLT863", 28)
                       if item[0] == "dlt863-d2-p28" and item[2] == 0]
        assert len(date_fields) == 4  # Printed date cell spans each three-row operating state.


def test_cross_page_groups_reference_existing_evidence(manifest):
    evidence_ids = {
        json.loads(line)["evidence"]["evidence_id"]
        for line in (ROOT / "data/stage6/stage6_evidence_bundle.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    for group in manifest["source_structure"]:
        assert group["parent_evidence_id"] in evidence_ids
        assert len(group["child_labels"]) == len(group["child_evidence_ids"])
        assert set(group["child_evidence_ids"]) <= evidence_ids
    gap = next(group for group in manifest["source_structure"] if group["group_id"] == "dlt863-5.2.15.2")
    assert gap["status"] == "source_incomplete_after_printed_11"
    assert gap["missing_next_printed_page"] == 12


@pytest.mark.parametrize("key,physical_page,logical_page,expected_count", [
    ("DL5190.3", 113, "102", 15),
    ("DLT863", 27, "38", 66),
    ("DLT863", 28, "39", 23),
])
def test_group_evidence_validates_with_every_member_bbox(
    manifest, catalog, key, physical_page, logical_page, expected_count,
):
    source_ir = _source_page(catalog, key, physical_page, logical_page)
    ir, links = append_reviewed_source_spans(source_ir, key, manifest=manifest)
    groups = evidence_groups_for_page(manifest, key, physical_page)
    units = {unit["unit_id"]: unit for unit in manifest["source_units"]}
    evidence = tuple(build_reviewed_group_evidence(ir, group, links, manifest=manifest)
                     for group in groups)
    assert len(evidence) == expected_count
    validate_evidence_bundle(EvidenceBundle(1, ir.revision.revision_id, evidence,
                                            ir.parsing_run.output_fingerprint), ir)
    for group, item in zip(groups, evidence):
        assert len(item.locations) == len(group["source_unit_ids"])
        for location, uid in zip(item.locations, group["source_unit_ids"]):
            assert location.bbox.as_list() == pytest.approx(units[uid]["bbox"], abs=0.01)
        assert item.disposition == ("structured" if group["semantic_use"] == "printed_standard_source"
                                    else "region_scoped")
        if group["document_key"] == "DLT863":
            assert item.correction_ids
            assert item.effective_text_origin == "manual_correction"
    if key == "DLT863" and physical_page == 28:
        fixed = next(group for group in groups if group["group_id"] == "group-dlt863-p28-d2-r01")
        item = evidence[groups.index(fixed)]
        assert item.source_text == "空负荷\n3000r/min"
        assert item.disposition == "region_scoped"

"""Original-page auxiliary supplements stay source-bound and non-promotional."""

import copy
from pathlib import Path

import pytest

from scripts.stage6_auxiliary_supplements import (
    DOCUMENT_KEY,
    audit_supplements,
    append_reviewed_source_spans,
    build_reviewed_group_evidence,
    build_reviewed_header_evidence,
    evidence_groups_for_page,
    load_bundle,
    load_supplements,
    source_units_for_page,
)
from turbine_kg.evidence import build_evidence
from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[2]


def test_twenty_marked_pairs_are_bound_to_their_original_page_answer_option():
    data = load_supplements()
    report = audit_supplements(data)
    assert report["status"] == "current", report["errors"]
    assert report["confirmed_question_count"] == 20
    assert {row["physical_page"] for row in data["marked_questions"]} == {32, 64, 120}
    assert all(row["current_evidence_answer_bound"] for row in report["question_results"])
    assert all(row["answer_option"] is None for row in data["unmarked_questions"])
    assert {row["physical_page"] for row in data["unmarked_questions"]} == {468}
    assert not {"Lb4A3231", "Lb4A4238"} & {row["question_id"] for row in data["marked_questions"]}
    assert {"Lb4A3231", "Lb4A4238"} <= {row["source_key"] for row in data["cross_page_fragments"]}
    last_p64 = next(row for row in report["question_results"] if row["question_id"] == "Lb4A4237")
    assert last_p64["integration_status"] == "ready_for_group_integration"
    assert all("Lb4A4238" not in item["source_quote"] for item in last_p64["evidence_support_slices"])
    assert not any(item["partial_member"] for item in last_p64["evidence_support_slices"])
    p120 = [row for row in report["question_results"] if row["physical_page"] == 120]
    assert [row["current_question_id"] for row in p120] == [
        "Jf5A2624", "Jf5A2625", "Jf5A3626", "Jf5A4627",
        "Jf4A1628", "Jf4A1629", "Jf4A1630", "Jf4A2631",
    ]
    assert all(row["integration_status"] == "ready_for_group_integration"
               and [item["source_part"] for item in row["evidence_support_slices"]] == ["stem", "options"]
               and all(not item["partial_member"] and item["source_span_ids_context_only"]
                       for item in row["evidence_support_slices"])
               for row in p120)


def test_wrong_option_is_not_accepted_as_marked_answer():
    data = copy.deepcopy(load_supplements())
    question = next(row for row in data["marked_questions"] if row["question_id"] == "La5A1001")
    question["answer_text"] = "cm"
    report = audit_supplements(data)
    assert report["status"] == "needs_reconciliation"
    assert "answer_not_currently_bound:La5A1001" in report["errors"]


@pytest.mark.parametrize("part, old, new", [
    ("stem", "(D)", "(C)"),
    ("options", "(D) 60°", "(D) 50°"),
])
def test_p120_answer_requires_separate_printed_stem_mark_and_option(part, old, new):
    rows = copy.deepcopy(load_bundle())
    target = next(row for row in rows
                  if row.get("source_supplement_group_id") == "aux-p120-Jf5A2624"
                  and row.get("source_supplement_part") == part)
    assert old in target["evidence"]["effective_text"]
    target["evidence"]["effective_text"] = target["evidence"]["effective_text"].replace(old, new)
    report = audit_supplements(rows=rows)
    assert report["status"] == "needs_reconciliation"
    assert "answer_not_currently_bound:J5A2624" in report["errors"]


def test_confirmed_p300_regions_and_p480_cells_become_source_units_without_diagram_geometry():
    data = load_supplements()
    p300 = source_units_for_page(data, DOCUMENT_KEY, 300)
    assert len(p300) == 15
    assert p300[0]["unit_id"] == "aux-p300-question-2098"
    assert [unit["source_text"] for unit in p300 if unit["unit_id"] in {
        "aux-p300-equation-1", "aux-p300-equation-2",
    }] == ["p₁/T₁=p₂/T₂", "T₂=p₂T₁/p₁"]
    assert all(unit["stage12_extractability"] == "context_only" for unit in p300)
    assert {unit["unit_id"] for unit in p300 if unit["content_kind"] == "caption"} == {
        "aux-p300-figure-caption",
    }
    assert {unit["unit_id"] for unit in p300[-2:]} == {
        "aux-p300-figure-caption", "aux-p300-figure-legend",
    }
    assert any(unit["unit_id"] == "aux-p300-question-2098" for unit in p300)
    assert any(unit["unit_id"] == "aux-p300-answer-2098" for unit in p300)
    assert any(unit["unit_id"] == "aux-p300-question-2099" for unit in p300)
    assert not any(unit["unit_id"] == "aux-p300-t-section-d12" for unit in p300)
    assert len(source_units_for_page(data, DOCUMENT_KEY, 244)) == 11
    assert len(source_units_for_page(data, DOCUMENT_KEY, 360)) == 11
    assert not any("鉴定试题库" in unit["source_text"] for page in (244, 360)
                   for unit in source_units_for_page(data, DOCUMENT_KEY, page))
    assert source_units_for_page(data, DOCUMENT_KEY, 468) == []
    units = source_units_for_page(data, DOCUMENT_KEY, 480)
    assert len(units) == 40
    assert units[0]["source_text"] == "6\n组卷方案"
    assert [unit["bbox"][1] for unit in units] == sorted(unit["bbox"][1] for unit in units)
    table = [unit for unit in units if unit["content_kind"] == "table"]
    assert len(table) == 32
    assert len({(unit["row_index"], unit["column_index"]) for unit in table}) == 32
    assert any(unit["source_text"] == "试卷的题型与题量分配（组卷方案）表" for unit in units)
    assert any(unit["source_text"] == "47～60" for unit in table)
    assert any("不应少于五种题型" in unit["source_text"] for unit in units)
    assert all(unit["source_text"].strip() for unit in units)
    assert data["table_cells"]["column_labels"] == [
        "题型", "鉴定工程等级：初级、中级", "鉴定工程等级：高级工、技师",
        "配分：初级、中级", "配分：高级工、技师",
    ]
    assert data["table_cells"]["rows"][3] == [
        "绘图/论述", "1题（10分/题）", "1题（5分/题）\n2题（10分/题）", "10", "15",
    ]
    assert len(data["formula_and_figure_regions"]) == 2
    assert all(item["not_a_source_span"] for item in data["formula_and_figure_regions"])
    assert data["formula_and_figure_regions"][1]["status"] == "visual_only_diagram_geometry_pending"
    assert evidence_groups_for_page(data, DOCUMENT_KEY, 300) == []
    groups = evidence_groups_for_page(data, DOCUMENT_KEY, 480)
    assert len(groups) == 5
    assert groups[0]["column_labels"][0] == "题型"
    assert groups[0]["printed_header"][1] == {"text": "鉴定工程等级", "columns": [1, 2]}
    assert groups[0]["table_context_status"] == "source_bound_two_tier_header"
    assert groups[0]["row_index"] == 2
    assert groups[0]["source_unit_ids"][0] == "aux-p480-exam-composition-r0-c0"


def test_p300_original_page_regions_have_local_spans_and_citable_context_only_evidence():
    settings = Settings.from_environment()
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    ir = parse_registered_pdf(
        settings.ocr_derived_root / "汽轮机辅机安装（第二版）(OCR).pdf",
        "OCR/汽轮机辅机安装（第二版）(OCR).pdf", catalog,
        title="汽轮机辅机安装（第二版）", page_indices=(299,),
    )
    adapted, links = append_reviewed_source_spans(ir, DOCUMENT_KEY)
    units = source_units_for_page(load_supplements(), DOCUMENT_KEY, 300)
    assert len(links) == len(units) == 15
    assert len(adapted.source_spans) == len(ir.source_spans) + 15
    assert not any("图形高度关系" in span.quote for span in adapted.source_spans)
    items = [build_evidence(adapted, (links[unit["unit_id"]]["source_span_id"],),
                            evidence_role="background", disposition="region_scoped",
                            effective_text_origin="manual_correction",
                            correction_ids=(links[unit["unit_id"]]["correction_id"],))
             for unit in units]
    assert all(item.authority_asset_id != item.processing_asset_id for item in items)
    assert all(item.locations[0].physical_page == 300 for item in items)
    by_unit = {unit["unit_id"]: item for unit, item in zip(units, items)}
    assert by_unit["aux-p300-equation-2"].source_text == "T₂=p₂T₁/p₁"
    assert "×" not in by_unit["aux-p300-equation-2"].source_text
    assert items[-2].content_kind == "caption" and items[-2].source_text == "图 D-12"
    assert any(item.source_text.startswith("答：需将气体冷却到45.8℃") for item in items)


def test_adapter_validates_one_page_ir_and_preserves_pending_regions():
    settings = Settings.from_environment()
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    path = settings.ocr_derived_root / "汽轮机辅机安装（第二版）(OCR).pdf"
    ir = parse_registered_pdf(
        path, "OCR/汽轮机辅机安装（第二版）(OCR).pdf", catalog,
        title="汽轮机辅机安装（第二版）", page_indices=(479,),
    )
    adapted, refs = append_reviewed_source_spans(ir, DOCUMENT_KEY)
    assert len(refs) == 40
    assert len(adapted.tables) == len(ir.tables) + 1
    assert len(adapted.table_cells) == len(ir.table_cells) + 32
    assert all(ref["source_span_id"] in {span.source_span_id for span in adapted.source_spans} for ref in refs.values())
    assert not any("P_P2" in span.quote or "图D-12" in span.quote for span in adapted.source_spans)
    groups = evidence_groups_for_page(load_supplements(), DOCUMENT_KEY, 480)
    evidence = [build_reviewed_group_evidence(adapted, group, refs) for group in groups]
    header = build_reviewed_header_evidence(adapted, refs)
    assert header.source_text.startswith("题型\n鉴定工程等级\n配分")
    assert header.table_context[0].review_scope == "table_region"
    assert len(header.table_context[0].header_cell_ids) == 7
    assert len(evidence) == 5
    assert evidence[0].source_text.splitlines() == load_supplements()["table_cells"]["rows"][0]
    assert evidence[0].source_text.startswith("选择\n")
    assert all(len(item.table_context) == 1 for item in evidence)
    assert all(len(item.table_context[0].header_cell_ids) == 7 for item in evidence)
    assert [item.table_context[0].row_indices for item in evidence] == [(2,), (3,), (4,), (5,), (6,)]
    assert all(item.content_kind == "table" and len(item.source_span_ids) == 5 for item in evidence)
    assert all(item.review_status == "accepted" for item in evidence)
    wrong_group = copy.deepcopy(groups[0])
    wrong_group["source_unit_ids"][0] = groups[1]["source_unit_ids"][0]
    with pytest.raises(ValueError, match="reviewed manifest group"):
        build_reviewed_group_evidence(adapted, wrong_group, refs)
    with pytest.raises(ValueError, match="already appended"):
        append_reviewed_source_spans(adapted, DOCUMENT_KEY)

"""The five auxiliary sample pages stay bound to the reviewed originals."""

import copy
import json
import re
from pathlib import Path

import pymupdf
import pytest

from scripts.stage6_auxiliary_early_page_supplements import (
    audit_bundle_current_text,
    load_review,
    reviewed_group_specs,
    repair_early_page_reading_order,
    validate_current_pdfs,
)
from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.evidence import build_evidence
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[2]


def _reviewed_pages():
    settings = Settings.from_environment()
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    pdf_path = settings.ocr_derived_root / "汽轮机辅机安装（第二版）(OCR).pdf"
    with pymupdf.open(pdf_path) as pdf:
        for page in (12, 32, 64, 120, 180):
            ir = parse_registered_pdf(
                pdf_path, "OCR/汽轮机辅机安装（第二版）(OCR).pdf", catalog,
                title="汽轮机辅机安装（第二版）", page_indices=(page - 1,),
            )
            yield page, *repair_early_page_reading_order(ir, pdf, "auxiliary_installation_book")


def test_current_pdfs_contain_original_page_question_codes_and_correct_symbols():
    pages = validate_current_pdfs()
    assert sorted(pages) == [12, 32, 64, 120, 180]
    assert "273.15×T" in pages[32]
    assert "Q-η" in pages[180]
    assert "Jf5A2624" in pages[120]


def test_group_specs_repair_old_question_codes_and_preserve_boundary_blocks():
    groups = reviewed_group_specs()
    assert len(groups) == 28
    p120 = [group for group in groups if group["physical_pages"] == [120]]
    assert [group["start_at"] for group in p120] == load_review()["expected_local_question_codes"]["120"]
    assert all(group["reviewed_answer_option"] for group in p120)
    assert all("J5A" not in group["group_id"] and "J4A" not in group["group_id"] for group in p120)
    pending = [group["group_id"] for group in groups
               if group["source_binding_status"] == "pending_cross_page_context"]
    assert pending == ["aux-p12-2.4.1", "aux-p64-Lb4A3231", "aux-p64-Lb4A4238"]
    p180 = [group for group in groups if group["physical_pages"] == [180]]
    assert [group.get("numbered_item_count") for group in p180] == [None, 4, None, 3]
    assert p180[-1]["item_semantics"] == "parallel fixation categories"


def test_stale_bundle_text_is_rejected_until_rebuilt_from_current_ocr():
    stale_rows = [
        {"document_key": "auxiliary_installation_book", "input": {"physical_page": 32},
         "evidence": {"effective_text": "(D)t=273.15xT; (A) 1000: (B) 13.6:"}},
        {"document_key": "auxiliary_installation_book", "input": {"physical_page": 120},
         "evidence": {"effective_text": "J5A2624 J5A2625 J5A3626 J4A1629"}},
        {"document_key": "auxiliary_installation_book", "input": {"physical_page": 180},
         "evidence": {"effective_text": "Q-n"}},
    ]
    missing = audit_bundle_current_text(stale_rows)
    assert "p32:(D)t=273.15×T" in missing
    assert "p120:Jf5A2624" in missing
    assert "p180:Q-η" in missing


def test_reviewed_group_specs_fail_closed_on_missing_boundary():
    altered = copy.deepcopy(load_review())
    altered["pending_cross_page_group_ids"].append("aux-p64-does-not-exist")
    with pytest.raises(ValueError, match="cross-page boundary group"):
        reviewed_group_specs(altered, verify_pdfs=False)


def test_reviewed_original_page_windows_restore_reading_order_and_isolate_sidebar():
    pages = {page: (ir, groups) for page, ir, groups in _reviewed_pages()}
    assert {page: len(groups) for page, (_, groups) in pages.items()} == {
        12: 16, 32: 8, 64: 8, 120: 8, 180: 4,
    }
    assert len(pages[12][0].source_spans) == 24  # Two exact PDF-region replacements.
    assert len(pages[64][0].source_spans) == 32
    p12 = pages[12][1]
    assert [group["group_id"] for group in p12[11:14]] == [
        "aux-p12-2.3.1", "aux-p12-2.3.2", "aux-p12-2.3.3",
    ]
    assert "2.1.2" not in p12[2]["source_text"]
    assert "400标准学时" in p12[3]["source_text"].replace("\n", "")
    for page in (32, 64, 120, 180):
        assert all("鉴\n定\n试\n题\n库" not in group["source_text"]
                   for group in pages[page][1])
    assert "80%。" in pages[64][1][2]["source_text"]
    assert "Lb4A4237" not in pages[64][1][5]["source_text"]
    assert "1.10。" in pages[120][1][3]["source_text"]
    assert "Q-η" in pages[180][1][2]["source_text"]
    assert pages[180][1][1]["source_text"].count("（1）") == 1
    for page, first_question_index in ((32, 2), (120, 0)):
        for group in pages[page][1][first_question_index:]:
            ir = pages[page][0]
            stem = build_evidence(ir, group["stem_source_span_ids"],
                                  evidence_role="background", disposition="region_scoped")
            options = build_evidence(ir, group["options_source_span_ids"],
                                     evidence_role="background", disposition="region_scoped")
            assert stem.evidence_id != options.evidence_id
            assert group["group_id"].split("-")[-1] in stem.source_text
            assert options.source_text.startswith(("（A）", "(A)"))
    assert all(group["source_binding_status"] == "pending_cross_page_context"
               for group in (p12[-1], pages[64][1][0], pages[64][1][-1]))
    for page, (ir, groups) in pages.items():
        for group in groups:
            evidence = build_evidence(ir, group["source_span_ids"],
                                      evidence_role="background", disposition="region_scoped")
            assert evidence.source_text == group["source_text"]
            assert all(location.physical_page == page for location in evidence.locations)


def test_replacement_regions_reject_changed_text_or_layout():
    settings = Settings.from_environment()
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    pdf_path = settings.ocr_derived_root / "汽轮机辅机安装（第二版）(OCR).pdf"
    ir = parse_registered_pdf(
        pdf_path, "OCR/汽轮机辅机安装（第二版）(OCR).pdf", catalog,
        title="汽轮机辅机安装（第二版）", page_indices=(11,),
    )
    changed = copy.deepcopy(load_review())
    changed["replacement_source_boxes"][0]["text"] = "不存在的培训时数"
    with pymupdf.open(pdf_path) as pdf, pytest.raises(ValueError, match="current reviewed PDF text/region differs"):
        repair_early_page_reading_order(ir, pdf, "auxiliary_installation_book", review=changed)


def test_p32_question_index_uses_distinct_current_stem_and_options_evidence():
    stage6 = ROOT / "data/stage6"
    index = json.loads((stage6 / "stage6_source_structure_index.json").read_text(encoding="utf-8"))
    evidence = {
        row["evidence"]["evidence_id"]: row
        for line in (stage6 / "stage6_evidence_annotations.jsonl").read_text(encoding="utf-8").splitlines()
        if line
        for row in (json.loads(line),)
    }
    groups = [group for group in index["context_groups"]
              if group.get("group_kind") == "question_options" and group.get("physical_pages") == [32]]
    assert len(groups) == 6
    for group in groups:
        stem_id, options_id = group["member_evidence_ids"]
        assert stem_id != options_id
        stem, options = evidence[stem_id], evidence[options_id]
        assert stem["source_supplement_group_id"] == group["group_id"]
        assert options["source_supplement_group_id"] == group["group_id"]
        assert stem["source_supplement_part"] == "stem"
        assert options["source_supplement_part"] == "options"
        assert group["question_id"] in stem["evidence"]["source_text"]
        assert group["answer_source_quote"] in options["evidence"]["source_text"]


def test_p120_all_eight_printed_answers_bind_to_option_source_spans():
    pages = {page: (ir, groups) for page, ir, groups in _reviewed_pages()}
    ir, groups = pages[120]
    source = {group["start_at"]: group for group in reviewed_group_specs()
              if group["physical_pages"] == [120]}
    assert len(source) == len(groups) == 8
    for group in groups:
        code = group["group_id"].split("-")[-1]
        answer = source[code]
        stem = build_evidence(ir, group["stem_source_span_ids"],
                              evidence_role="background", disposition="region_scoped")
        options = build_evidence(ir, group["options_source_span_ids"],
                                 evidence_role="background", disposition="region_scoped")
        assert code in stem.source_text
        assert re.search(r"[（(]" + answer["reviewed_answer_option"] + r"[）)]\s*"
                         + re.escape(answer["reviewed_answer_text"]), options.source_text)


def test_p120_question_index_binds_eight_printed_answers_to_current_evidence():
    stage6 = ROOT / "data/stage6"
    index = json.loads((stage6 / "stage6_source_structure_index.json").read_text(encoding="utf-8"))
    evidence = {
        row["evidence"]["evidence_id"]: row
        for line in (stage6 / "stage6_evidence_annotations.jsonl").read_text(encoding="utf-8").splitlines()
        if line
        for row in (json.loads(line),)
    }
    groups = [group for group in index["context_groups"]
              if group.get("group_kind") == "question_options" and group.get("physical_pages") == [120]]
    assert [group["question_id"] for group in groups] == load_review()["expected_local_question_codes"]["120"]
    for group in groups:
        stem_id, options_id = group["member_evidence_ids"]
        stem, options = evidence[stem_id], evidence[options_id]
        assert stem_id != options_id
        assert stem["source_supplement_group_id"] == options["source_supplement_group_id"] == group["group_id"]
        assert stem["source_supplement_part"] == "stem"
        assert options["source_supplement_part"] == "options"
        assert group["question_id"] in stem["evidence"]["source_text"]
        assert group["answer_source_quote"] in options["evidence"]["source_text"]
        assert group["items"][0]["source_span_ids"] == stem["evidence"]["source_span_ids"]
        assert group["items"][1]["source_span_ids"] == options["evidence"]["source_span_ids"]

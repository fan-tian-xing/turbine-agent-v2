"""Original-page boundary facts remain proposals until canonical Evidence binds them."""

import copy
import json
from pathlib import Path

import pytest

from scripts.stage6_auxiliary_boundary_review import (
    DOCUMENT_KEY,
    append_continuation_source_spans,
    audit_boundary_review,
    load_review,
)
from scripts.stage6_auxiliary_supplements import load_supplements, source_units_for_page
from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.settings import Settings


def _newly_built_rows():
    """Use reviewed builder units until the pending annotation rebuild lands."""
    rows = []
    manifest = load_supplements()
    for page in (244, 360):
        for index, unit in enumerate(source_units_for_page(manifest, DOCUMENT_KEY, page)):
            rows.append({
                "document_key": DOCUMENT_KEY,
                "input": {"physical_page": page},
                "source_supplement_group_id": unit["unit_id"],
                "evidence": {"evidence_id": f"test-reviewed-p{page}-{index}",
                             "effective_text": unit["source_text"]},
            })
    path = Path(__file__).resolve().parents[2] / "data/stage6/stage6_evidence_annotations.jsonl"
    rows.extend(row for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
                if row.get("document_key") == DOCUMENT_KEY and row.get("input", {}).get("physical_page") == 468)
    return rows


def test_current_auxiliary_evidence_is_partitioned_without_promoting_cross_page_claims():
    report = audit_boundary_review(rows=_newly_built_rows())
    assert report["status"] == "current_proposal"
    assert report["canonical_binding_status"] == "pending"
    assert report["checked_sample_pages"] == [244, 360, 468]
    assert report["missing_canonical_continuation_pages"] == [243, 245, 469]
    assert len(report["group_links"]) == 14
    assert len(report["group_links"]["aux-La1F2009"]["evidence_ids"]) == 8
    assert report["group_links"]["aux-Je4C3270"]["integration_status"] == "pending_page_245_canonical_evidence"
    assert report["unmarked_answer_count"] == 7
    assert all(report["group_links"][f"aux-unmarked-q{number}"]["selected_answer"] is None
               for number in range(1, 8))
    assert report["group_links"]["aux-unmarked-q7"]["integration_status"] == "pending_page_469_option"


def test_unmarked_questions_and_six_parameter_classes_cannot_be_reinterpreted():
    data = load_review()
    assert [item["symbol"] for item in data["groups"][5]["items"]] == ["Q", "H", "n", "N", "η", None]
    assert data["groups"][5]["kind"] == "classification_list"
    assert data["unmarked_sample_questions"]["selected_answers"] == [None] * 7
    assert data["source_units"][-1]["source_text"] == "（D）环氧煤焦油玻璃钢。"
    wrong = copy.deepcopy(data)
    wrong["groups"][5]["items"][4]["symbol"] = "n"
    with pytest.raises(ValueError, match="parallel pump parameter"):
        audit_boundary_review(wrong, rows=_newly_built_rows())


@pytest.mark.parametrize("page,unit_id,quote", [
    (243, "aux-p243-Je4C3266-start", "Je4C3266"),
    (245, "aux-p245-Je4C3270-tail", "形，可加焊筋板增加刚性"),
    (469, "aux-p469-question-7-option-d", "（D）环氧煤焦油玻璃钢。"),
])
def test_original_reviewed_continuation_can_become_one_local_span(page, unit_id, quote):
    settings = Settings.from_environment()
    from scripts.stage6_auxiliary_boundary_review import ROOT
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    ir = parse_registered_pdf(
        settings.ocr_derived_root / "汽轮机辅机安装（第二版）(OCR).pdf",
        "OCR/汽轮机辅机安装（第二版）(OCR).pdf", catalog,
        title="汽轮机辅机安装（第二版）", page_indices=(page - 1,),
    )
    adapted, links = append_continuation_source_spans(ir)
    assert adapted.revision.document_logical_id == ir.revision.document_logical_id
    assert adapted.pages[0].display_page_number == page
    assert len(adapted.source_spans) == len(ir.source_spans) + 1
    assert len(adapted.manual_corrections) == len(ir.manual_corrections) + 1
    span = next(span for span in adapted.source_spans if span.source_span_id == links[unit_id])
    assert quote in span.quote
    assert span.text_origin == "manual_correction"
    with pytest.raises(ValueError, match="fresh reviewed continuation"):
        append_continuation_source_spans(adapted)

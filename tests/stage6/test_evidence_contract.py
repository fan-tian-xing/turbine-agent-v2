from dataclasses import replace

import pytest

from turbine_kg.documents.ids import stable_id, table_cell_id, table_id
from turbine_kg.documents.models import AssetPageRef, AssetRef, BBox, Document, DocumentRevision, Table, TableCell
from turbine_kg.documents.parser import parse_page_inputs
from turbine_kg.documents.profiles import PageInput, RawTextBlock
from turbine_kg.evidence import build_evidence, validate_evidence_bundle
from turbine_kg.evidence.coordinates import pixel_bbox_to_pdf_bbox, union_pixel_boxes
from turbine_kg.evidence.models import EvidenceBundle, TableContext


def _ir(run_id="run-" + "6" * 20):
    document_id = stable_id("doc", "stage6", "sample")
    revision_id = stable_id("rev", document_id, "v1")
    asset_id = stable_id("asset", "stage6", "sample.pdf")
    document = Document(document_id, "阶段6样本文档", "stage6-sample")
    revision = DocumentRevision(revision_id, document_id, "v1", "stage6_test")
    asset = AssetRef(asset_id, document_id, revision_id, "original", "sample.pdf", "a" * 64, "source")
    pages = (
        PageInput(
            asset_id, revision_id, 0, 600, 800, 0, "原始要求：数值为0.15 mm。" * 3,
            "native", 0.1, printed_page_label="3",
            text_blocks=(RawTextBlock("原始要求：数值为0.15 mm。" * 3, BBox(50, 100, 550, 180)),),
        ),
        PageInput(
            asset_id, revision_id, 1, 600, 800, 0, "第二页背景文本" * 10,
            "native", 0.1, text_blocks=(RawTextBlock("第二页背景文本" * 10, BBox(50, 100, 550, 180)),),
        ),
    )
    return parse_page_inputs(document, revision, (asset,), pages, parsing_run_id=run_id)


def _ocr_ir():
    document_id = stable_id("doc", "stage6", "ocr-sample")
    revision_id = stable_id("rev", document_id, "v1")
    original_id = stable_id("asset", "stage6", "original.pdf")
    ocr_id = stable_id("asset", "stage6", "ocr.pdf")
    document = Document(document_id, "OCR样本文档", "stage6-ocr-sample")
    revision = DocumentRevision(revision_id, document_id, "v1", "stage6_test")
    original = AssetRef(original_id, document_id, revision_id, "original", "original.pdf", "a" * 64, "source")
    ocr = AssetRef(ocr_id, document_id, revision_id, "derived_ocr", "ocr.pdf", "b" * 64, "ocr", original_id, "ocr")
    page = PageInput(
        ocr_id, revision_id, 0, 600, 800, 0, "OCR文字：0.15 mm。" * 3,
        "ocr", 0.1,
        related_asset_page_refs=(AssetPageRef(original_id, 0),),
        text_blocks=(RawTextBlock("OCR文字：0.15 mm。" * 3, BBox(50, 100, 550, 180)),),
    )
    return parse_page_inputs(document, revision, (ocr, original), (page,), parsing_run_id="run-" + "9" * 20)


def test_build_evidence_preserves_quote_page_identity_and_bbox():
    ir = _ir()
    item = build_evidence(
        ir, (ir.source_spans[0].source_span_id,), evidence_role="requirement_source",
        review_status="accepted", reviewer="reviewer", reviewed_at="2026-09-11T00:00:00Z",
        review_reason="original page and bbox checked",
    )
    assert item.source_text == ir.source_spans[0].quote
    assert item.locations[0].physical_page == 1
    assert item.locations[0].logical_page == "3"
    assert item.locations[0].bbox == ir.source_spans[0].bbox
    assert item.locations[0].original_asset_id == ir.assets[0].asset_id
    assert item.authority_asset_id == ir.assets[0].asset_id
    assert item.processing_asset_id is None
    assert item.authority_basis == "original_pdf_visual_review"


def test_multi_span_evidence_keeps_ordered_source_text_and_each_location():
    ir = _ir()
    item = build_evidence(
        ir, tuple(span.source_span_id for span in ir.source_spans), evidence_role="background",
    )
    assert item.source_text == "\n".join(span.quote for span in ir.source_spans)
    assert [loc.physical_page for loc in item.locations] == [1, 2]
    assert item.content_kind == "paragraph"


def test_evidence_rejects_wrong_physical_or_logical_page():
    ir = _ir()
    item = build_evidence(ir, (ir.source_spans[0].source_span_id,), evidence_role="background")
    bad_location = replace(item.locations[0], physical_page=99)
    bad = replace(item, locations=(bad_location,))
    bundle = EvidenceBundle(1, ir.revision.revision_id, (bad,), ir.parsing_run.output_fingerprint or "")
    with pytest.raises(ValueError, match="physical page"):
        validate_evidence_bundle(bundle, ir)


def test_evidence_rejects_a_processing_asset_as_authority():
    ir = _ir()
    item = build_evidence(ir, (ir.source_spans[0].source_span_id,), evidence_role="background")
    bad = replace(item, authority_asset_id="asset-" + "b" * 20)
    bundle = EvidenceBundle(1, ir.revision.revision_id, (bad,), ir.parsing_run.output_fingerprint or "")
    with pytest.raises(ValueError, match="authority asset"):
        validate_evidence_bundle(bundle, ir)


def test_ocr_processing_asset_cannot_be_hidden_or_mislabeled():
    ir = _ocr_ir()
    item = build_evidence(ir, (ir.source_spans[0].source_span_id,), evidence_role="background")
    assert item.processing_asset_id is not None
    bad = replace(item, processing_asset_id=None)
    bundle = EvidenceBundle(1, ir.revision.revision_id, (bad,), ir.parsing_run.output_fingerprint or "")
    with pytest.raises(ValueError, match="processing_asset_id"):
        validate_evidence_bundle(bundle, ir)


def test_evidence_identity_is_independent_of_review_status():
    ir = _ir()
    first = build_evidence(ir, (ir.source_spans[0].source_span_id,), evidence_role="background")
    accepted = build_evidence(
        ir, (ir.source_spans[0].source_span_id,), evidence_role="background",
        review_status="accepted", reviewer="reviewer", reviewed_at="2026-09-11T00:00:00Z",
        review_reason="original page and bbox checked",
    )
    assert first.evidence_id == accepted.evidence_id
    assert first.evidence_version_id == accepted.evidence_version_id


def test_stable_evidence_identity_survives_a_new_parsing_run():
    first_ir = _ir("run-" + "7" * 20)
    second_ir = _ir("run-" + "8" * 20)
    first = build_evidence(first_ir, (first_ir.source_spans[0].source_span_id,), evidence_role="background")
    second = build_evidence(second_ir, (second_ir.source_spans[0].source_span_id,), evidence_role="background")
    assert first.evidence_id == second.evidence_id
    assert first.evidence_version_id != second.evidence_version_id


def test_quarantined_evidence_is_recordable_but_mixed_content_cannot_be_structured():
    ir = _ir()
    item = build_evidence(
        ir, (ir.source_spans[0].source_span_id,), evidence_role="requirement_source",
        disposition="quarantined", review_status="needs_review",
    )
    assert item.disposition == "quarantined"
    mixed = replace(item, content_kind="mixed", disposition="structured")
    bundle = EvidenceBundle(1, ir.revision.revision_id, (mixed,), ir.parsing_run.output_fingerprint or "")
    with pytest.raises(ValueError, match="mixed content"):
        validate_evidence_bundle(bundle, ir)


def test_effective_text_cannot_change_without_a_traceable_manual_correction():
    ir = _ir()
    item = build_evidence(ir, (ir.source_spans[0].source_span_id,), evidence_role="background")
    bad = replace(item, effective_text="invented replacement", effective_text_origin="ocr_text")
    bundle = EvidenceBundle(1, ir.revision.revision_id, (bad,), ir.parsing_run.output_fingerprint or "")
    with pytest.raises(ValueError, match="uncorrected effective text"):
        validate_evidence_bundle(bundle, ir)
    bad = replace(item, effective_text="corrected", effective_text_origin="manual_correction")
    bundle = EvidenceBundle(1, ir.revision.revision_id, (bad,), ir.parsing_run.output_fingerprint or "")
    with pytest.raises(ValueError, match="correction IDs"):
        validate_evidence_bundle(bundle, ir)


def test_source_spans_must_follow_document_order():
    ir = _ir()
    with pytest.raises(ValueError, match="reading order"):
        build_evidence(
            ir,
            tuple(span.source_span_id for span in reversed(ir.source_spans)),
            evidence_role="background",
        )


def test_different_original_rotation_requires_explicit_mapping():
    ir = _ir()
    item = build_evidence(ir, (ir.source_spans[0].source_span_id,), evidence_role="background")
    bad_location = replace(item.locations[0], authority_rotation_deg=90)
    bad = replace(item, locations=(bad_location,))
    bundle = EvidenceBundle(1, ir.revision.revision_id, (bad,), ir.parsing_run.output_fingerprint or "")
    with pytest.raises(ValueError, match="explicit coordinate transform"):
        validate_evidence_bundle(bundle, ir)


def test_evidence_rejects_forged_identity_version_and_text_origin():
    ir = _ir()
    item = build_evidence(ir, (ir.source_spans[0].source_span_id,), evidence_role="background")
    for bad, message in (
        (replace(item, evidence_id="evidence-" + "0" * 20), "stable identity"),
        (replace(item, evidence_version_id="evver-" + "0" * 20), "version ID"),
        (replace(item, text_origin="ocr_text"), "text_origin"),
    ):
        bundle = EvidenceBundle(1, ir.revision.revision_id, (bad,), ir.parsing_run.output_fingerprint or "")
        with pytest.raises(ValueError, match=message):
            validate_evidence_bundle(bundle, ir)


def test_table_value_cells_must_match_declared_row_and_column():
    ir = _ir()
    block = replace(ir.blocks[0], block_type="table")
    table_value = table_id(block.block_version_id)
    header_value = table_cell_id(table_value, 0, 0)
    cell_value = table_cell_id(table_value, 1, 1)
    table = Table(table_value, block.block_version_id, None, 2, 2, (header_value, cell_value))
    header = TableCell(header_value, table_value, block.block_version_id, 0, 0)
    cell = TableCell(cell_value, table_value, block.block_version_id, 1, 1)
    span = replace(ir.source_spans[0], content_kind="table", table_id=table_value, row_index=1, column_index=1)
    table_ir = replace(
        ir,
        blocks=(block, *ir.blocks[1:]),
        tables=(table,),
        table_cells=(header, cell),
        source_spans=(span, *ir.source_spans[1:]),
    )
    bad_context = TableContext(
        table_id=table_value,
        row_indices=(0,),
        column_indices=(0,),
        header_cell_ids=(header_value,),
        value_cell_ids=(cell_value,),
        leaf_column_count=2,
        header_hierarchy=("reviewed header",),
    )
    with pytest.raises(ValueError, match="declared row and column"):
        build_evidence(
            table_ir,
            (span.source_span_id,),
            evidence_role="requirement_source",
            table_context=(bad_context,),
        )


def test_ocr_pixel_bbox_is_converted_to_pdf_points_and_rotation_is_not_guessed():
    converted = pixel_bbox_to_pdf_bbox(
        {"x0": 170, "y0": 170, "x1": 850, "y1": 1275},
        dpi=170,
        page_width_pt=595,
        page_height_pt=842,
    )
    assert converted.as_list() == [72.0, 72.0, 360.0, 540.0]
    assert union_pixel_boxes([{"x0": 10, "y0": 20, "x1": 40, "y1": 50}, {"x0": 5, "y0": 25, "x1": 60, "y1": 45}]) == {
        "x0": 5.0, "y0": 20.0, "x1": 60.0, "y1": 50.0
    }
    with pytest.raises(ValueError, match="rotated"):
        pixel_bbox_to_pdf_bbox(
            {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
            dpi=170,
            page_width_pt=595,
            page_height_pt=842,
            rotation_deg=90,
        )

import json
from dataclasses import replace
from pathlib import Path

import pytest
import fitz

from turbine_kg.documents.ids import stable_id
from turbine_kg.documents.identity import load_revision_catalog
from turbine_kg.documents.models import AssetRef, BBox, Document, DocumentRevision, ManualCorrection, Table
from turbine_kg.documents.parser import parse_page_inputs
from turbine_kg.documents.pdf import parse_pdf
from turbine_kg.documents.profiles import PageInput, RawTextBlock, choose_page_mode, inspect_page, load_layout_profile
from turbine_kg.documents.validation import validate_document_ir
from turbine_kg.documents.vocabulary import display_name, load_display_terms


def _identity():
    document_id = stable_id("doc", "stage4", "sample-document")
    revision_id = stable_id("rev", document_id, "registry-v1")
    asset_id = stable_id("asset", "stage4", "sample.pdf")
    return (
        Document(document_id, "阶段4合成文档", "stage4-sample"),
        DocumentRevision(revision_id, document_id, "registry-v1", "controlled_registry_baseline"),
        AssetRef(asset_id, document_id, revision_id, "original", "sample.pdf", "a" * 64, "source"),
    )


def test_page_capability_routes_native_scan_and_mixed_without_filename_logic():
    assert choose_page_mode(inspect_page(PageInput("asset-" + "a" * 20, "rev-" + "b" * 20, 0, 600, 800, 0, "有足够多的原生文本" * 4, "native", 0.1))) == "native_text"
    assert choose_page_mode(inspect_page(PageInput("asset-" + "a" * 20, "rev-" + "b" * 20, 1, 600, 800, 0, "", "scan_only", 0.9))) == "scan_only"
    assert choose_page_mode(inspect_page(PageInput("asset-" + "a" * 20, "rev-" + "b" * 20, 2, 600, 800, 0, "混合页面存在文本" * 8, "native", 0.9))) == "mixed"


def test_three_page_modes_enter_one_document_ir():
    document, revision, asset = _identity()
    pages = (
        PageInput(asset.asset_id, revision.revision_id, 0, 600, 800, 0, "原生文本" * 20, "native", 0.1),
        PageInput(asset.asset_id, revision.revision_id, 1, 600, 800, 0, "", "scan_only", 0.9),
        PageInput(asset.asset_id, revision.revision_id, 2, 600, 800, 90, "混合文本" * 12, "native", 0.9),
    )
    ir = parse_page_inputs(document, revision, (asset,), pages, parsing_run_id="run-" + "1" * 20)
    assert [page.page_mode for page in ir.pages] == ["native_text", "scan_only", "mixed"]
    assert len(ir.pages) == 3
    assert len(ir.blocks) == 2
    assert len(ir.source_spans) == 2
    assert ir.pages[2].rotation_deg == 90
    validate_document_ir(ir)


def test_reparse_keeps_page_and_revision_but_changes_run_and_block_version():
    document, revision, asset = _identity()
    page = PageInput(asset.asset_id, revision.revision_id, 0, 600, 800, 0, "原生文本" * 20, "native", 0.1)
    first = parse_page_inputs(document, revision, (asset,), (page,), parsing_run_id="run-" + "1" * 20)
    second = parse_page_inputs(document, revision, (asset,), (page,), parsing_run_id="run-" + "2" * 20)
    assert first.pages[0].page_id == second.pages[0].page_id
    assert first.revision.revision_id == second.revision.revision_id
    assert first.parsing_run.parsing_run_id != second.parsing_run.parsing_run_id
    assert first.blocks[0].block_version_id != second.blocks[0].block_version_id


def test_ocr_asset_can_share_revision_without_creating_new_revision():
    document, revision, original = _identity()
    derived = AssetRef(
        stable_id("asset", "stage4", "sample-ocr.pdf"),
        document.document_logical_id,
        revision.revision_id,
        "derived_ocr",
        "OCR/sample.pdf",
        "b" * 64,
        "ocr",
        derived_from_asset_id=original.asset_id,
        derivation_type="ocr",
        derivation_run_id="run-" + "3" * 20,
    )
    page = PageInput(derived.asset_id, revision.revision_id, 0, 600, 800, 0, "OCR文本" * 12, "ocr", 0.8)
    ir = parse_page_inputs(document, revision, (original, derived), (page,), parsing_run_id="run-" + "4" * 20)
    assert ir.revision.revision_id == revision.revision_id
    assert ir.assets[1].derived_from_asset_id == original.asset_id
    assert ir.pages[0].asset_page_refs[0].asset_id == derived.asset_id


def test_source_span_and_block_bbox_are_precise_and_bounded():
    document, revision, asset = _identity()
    raw = RawTextBlock("页内段落" * 12, BBox(10, 20, 100, 50))
    page = PageInput(asset.asset_id, revision.revision_id, 0, 600, 800, 0, raw.text, "native", 0.1, text_blocks=(raw,))
    ir = parse_page_inputs(document, revision, (asset,), (page,), parsing_run_id="run-" + "5" * 20)
    assert ir.blocks[0].bbox == raw.bbox
    assert ir.source_spans[0].char_start == 0
    assert ir.source_spans[0].char_end == len(raw.text)

    invalid_page = PageInput(asset.asset_id, revision.revision_id, 0, 600, 800, 0, raw.text, "native", 0.1, text_blocks=(RawTextBlock(raw.text, BBox(0, 0, 601, 50)),))
    with pytest.raises(ValueError, match="bbox"):
        parse_page_inputs(document, revision, (asset,), (invalid_page,), parsing_run_id="run-" + "6" * 20)


def test_page_number_contract_rejects_inconsistent_display_number():
    document, revision, asset = _identity()
    page = PageInput(asset.asset_id, revision.revision_id, 0, 600, 800, 0, "页码合同" * 12, "native", 0.1)
    ir = parse_page_inputs(document, revision, (asset,), (page,), parsing_run_id="run-" + "7" * 20)
    invalid = ir.pages[0].__class__(
        **{**{field: getattr(ir.pages[0], field) for field in ir.pages[0].__dataclass_fields__}, "display_page_number": 9}
    )
    with pytest.raises(ValueError, match="page number"):
        validate_document_ir(ir.__class__(**{
            **{field: getattr(ir, field) for field in ir.__dataclass_fields__},
            "pages": (invalid,),
        }))


def test_table_source_span_requires_table_location_and_accepts_table_level_location():
    document, revision, asset = _identity()
    page = PageInput(asset.asset_id, revision.revision_id, 0, 600, 800, 0, "表格文本" * 12, "native", 0.1)
    ir = parse_page_inputs(document, revision, (asset,), (page,), parsing_run_id="run-" + "8" * 20)
    table = Table(
        table_id=stable_id("table", ir.blocks[0].block_version_id),
        block_version_id=ir.blocks[0].block_version_id,
        caption_block_version_id=None,
        row_count=1,
        column_count=1,
        cell_ids=(),
    )
    table_span = replace(ir.source_spans[0], content_kind="table", table_id=table.table_id)
    validate_document_ir(replace(ir, tables=(table,), source_spans=(table_span,)))
    with pytest.raises(ValueError, match="table source span"):
        validate_document_ir(replace(ir, source_spans=(replace(table_span, table_id=None),)))


def test_manual_correction_is_an_overlay_and_does_not_mutate_parser_output():
    document, revision, asset = _identity()
    page = PageInput(asset.asset_id, revision.revision_id, 0, 600, 800, 0, "原始文本" * 12, "native", 0.1)
    ir = parse_page_inputs(document, revision, (asset,), (page,), parsing_run_id="run-" + "9" * 20)
    correction = ManualCorrection(
        correction_id=stable_id("correction", ir.blocks[0].block_version_id, "reviewer", "修正文本"),
        block_version_id=ir.blocks[0].block_version_id,
        original_text=ir.blocks[0].text,
        corrected_text="修正文本" * 12,
        reason="人工核对数字",
        reviewer="reviewer",
        reviewed_at="2026-09-08T00:00:00Z",
    )
    validated = validate_document_ir(replace(ir, manual_corrections=(correction,)))
    assert validated.blocks[0].text == ir.blocks[0].text
    assert validated.manual_corrections[0].corrected_text == "修正文本" * 12


def test_parsing_fingerprints_cover_layout_and_output_content():
    document, revision, asset = _identity()
    first_page = PageInput(asset.asset_id, revision.revision_id, 0, 600, 800, 0, "指纹文本" * 12, "native", 0.1)
    second_page = replace(first_page, width_pt=601)
    first = parse_page_inputs(document, revision, (asset,), (first_page,), parsing_run_id="run-" + "a" * 20)
    second = parse_page_inputs(document, revision, (asset,), (second_page,), parsing_run_id="run-" + "a" * 20)
    assert first.parsing_run.input_fingerprint != second.parsing_run.input_fingerprint
    assert first.parsing_run.output_fingerprint != second.parsing_run.output_fingerprint


def test_source_span_cannot_join_blocks_from_different_pages():
    document, revision, asset = _identity()
    pages = (
        PageInput(asset.asset_id, revision.revision_id, 0, 600, 800, 0, "第一页文本" * 12, "native", 0.1),
        PageInput(asset.asset_id, revision.revision_id, 1, 600, 800, 0, "第二页文本" * 12, "native", 0.1),
    )
    ir = parse_page_inputs(document, revision, (asset,), pages, parsing_run_id="run-" + "b" * 20)
    invalid_span = replace(ir.source_spans[0], block_version_ids=(ir.blocks[0].block_version_id, ir.blocks[1].block_version_id))
    with pytest.raises(ValueError, match="belong to its page"):
        validate_document_ir(replace(ir, source_spans=(invalid_span,)))


def test_revision_catalog_rejects_invalid_controlled_ids(tmp_path):
    catalog = tmp_path / "invalid.tsv"
    catalog.write_text(
        "document_logical_id\trevision_id\trevision_label\n"
        "doc-not-controlled\trev-not-controlled\tA\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid"):
        load_revision_catalog(catalog)


def test_contract_file_freezes_coordinate_and_page_numbering():
    contract = json.loads(open("config/document_ir_contract.json", encoding="utf-8").read())
    assert contract["coordinate_system"]["origin"] == "top_left"
    assert contract["coordinate_system"]["unit"] == "pdf_point"
    assert contract["page_numbering"]["pdf_page_index"] == "zero_based"
    assert contract["page_numbering"]["display_page_number"] == "one_based"


def test_layout_profile_is_loaded_from_declarative_config():
    profile = load_layout_profile(Path("config/layout_profiles.json"))
    assert profile.profile_id == "adaptive_pdf_v1"
    assert profile.scan_min_image_coverage == 0.65


def test_legacy_revision_map_loads_without_collapsing_the_identity_boundary():
    records = load_revision_catalog(Path("config/revision_identity.tsv"))
    assert len(records) == 44
    assert len({record.document_logical_id for record in records}) == 44
    assert {record.revision_basis for record in records} == {"legacy_registry_baseline"}


def test_revision_catalog_allows_multiple_controlled_revisions_and_validates_supersession(tmp_path):
    catalog = tmp_path / "revisions.tsv"
    document_id = "doc-" + "a" * 20
    catalog.write_text(
        "document_logical_id\trevision_id\trevision_label\trevision_basis\tsupersedes_revision_id\n"
        f"{document_id}\t{'rev-' + '1' * 20}\t2019\tpublication\t\n"
        f"{document_id}\t{'rev-' + '2' * 20}\t2024\tcontent_revision\t{'rev-' + '1' * 20}\n",
        encoding="utf-8",
    )
    records = load_revision_catalog(catalog)
    assert len(records) == 2
    assert records[1].supersedes_revision_id == records[0].revision_id


def test_display_vocabulary_has_a_safe_fallback():
    terms = load_display_terms(Path("config/term_display_map.json"))
    assert display_name("Evidence", terms) == "证据"
    assert display_name("FutureTerm", terms) == "FutureTerm"


def test_revision_catalog_rejects_cross_document_supersession(tmp_path):
    catalog = tmp_path / "invalid.tsv"
    catalog.write_text(
        "document_logical_id\trevision_id\trevision_label\tsupersedes_revision_id\n"
        f"{'doc-' + 'a' * 20}\t{'rev-' + '1' * 20}\tA\t\n"
        f"{'doc-' + 'b' * 20}\t{'rev-' + '2' * 20}\tB\t{'rev-' + '1' * 20}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="same logical document"):
        load_revision_catalog(catalog)


def test_pdf_entry_preserves_native_page_location_in_document_ir(tmp_path):
    document, revision, asset = _identity()
    pdf_path = tmp_path / "native.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    page.insert_text((72, 72), "原生文本页面" * 20)
    pdf.save(pdf_path)
    pdf.close()
    ir = parse_pdf(pdf_path, document, revision, asset)
    assert ir.pages[0].page_mode == "native_text"
    assert ir.blocks[0].bbox is not None
    assert ir.source_spans[0].text_origin == "native_text"

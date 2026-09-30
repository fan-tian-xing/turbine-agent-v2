"""Regressions for current reviewed PDF identity, authority and display coordinates."""

from dataclasses import asdict, replace
import copy
import hashlib
import json
from types import SimpleNamespace

import pymupdf
import pytest

from scripts import benchmark_stage5_baseline as baseline
from scripts import build_stage6_evidence_golden_sample as boundary
from scripts import build_stage6_golden_evidence as builder
from scripts import build_stage6_table_evidence as table_builder
from scripts import build_stage6_vertical_slice as initial_slice
from scripts import benchmark_stage5_quality as quality
from scripts import audit_stage6_exit as stage6_exit
from scripts.stage6_reviewed_pdf_regions import region_text, verify_region_text
from scripts.stage6_cross_page_supplements import build_cross_page_evidence
from scripts.stage6_page_review_binding import page_review_fingerprint
from scripts.stage6_page_review_binding import STAGE5_BINDING_PATHS
from turbine_kg.documents.catalog import AssetIdentity, IdentityCatalog
from turbine_kg.documents.identity import RevisionRecord
from turbine_kg.documents.pdf import _clip_bbox, _image_boxes, inspect_pdf_page
from turbine_kg.documents.profiles import LayoutProfile
from turbine_kg.evidence import build_evidence
from turbine_kg.settings import Settings


TEXT = "Corrected tolerance 0.02 mm"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def displayed(box, width, height, rotation):
    x0, y0, x1, y1 = box
    return {0: [x0, y0, x1, y1], 90: [height-y1, x0, height-y0, x1],
            180: [width-x1, height-y1, width-x0, height-y0],
            270: [y0, width-x1, y1, width-x0]}[rotation]


@pytest.fixture
def current(tmp_path):
    source, derived = tmp_path / "source", tmp_path / "derived"
    source.mkdir()
    derived.mkdir()
    original_path, processed_path = source / "original.pdf", derived / "current.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=300, height=400)
    pixel = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 400), False)
    pixel.clear_with(210)
    page.insert_image(page.rect, pixmap=pixel)
    page.set_cropbox(pymupdf.Rect(20, 30, 260, 370))
    page.set_rotation(90)
    pdf.save(original_path)
    pdf.close()
    pdf = pymupdf.open(original_path)
    pdf[0].insert_text((40, 80), TEXT, fontsize=9, render_mode=3)
    pdf.save(processed_path)
    pdf.close()
    docid, revid = "doc-" + "1"*20, "rev-" + "2"*20
    original = AssetIdentity("asset-" + "3"*20, docid, revid, "original", "original.pdf", sha(original_path), "source")
    processing = AssetIdentity("asset-" + "4"*20, docid, revid, "derived_ocr", "OCR/current.pdf", sha(processed_path),
                               "ocr_derived", original.asset_id, "ocr_derivative")
    catalog = IdentityCatalog((original, processing), (RevisionRecord(docid, revid, "same source revision"),),
                              {original.relative_path: original.asset_id, processing.relative_path: processing.asset_id})
    settings = Settings(source, derived)
    entry = {"document_key": "new_derivative", "document_logical_id": docid,
             "original_asset_id": original.asset_id, "processing_asset_id": processing.asset_id,
             "original_relative_path": original.relative_path, "processing_relative_path": processing.relative_path,
             "page_count": 1, "sample_pages": [{"physical_page": 1, "pdf_page": 1}]}
    manifest = {"documents": [entry], "sample_page_count": 1,
                "scope": "synthetic current batch"}
    review_record = {"document_key": entry["document_key"], "original_sha256": original.sha256,
                     "processing_sha256": processing.sha256, "reviewed_page_ranges": [[1, 1]],
                     "line_by_line_reviewed": True, "table_cells_reviewed": True, "reading_order_reviewed": True,
                     "unresolved_text_count": 0, "unresolved_table_cell_count": 0,
                     "unresolved_page_mapping_count": 0, "blank_pages": []}
    review = {"full_corpus_reviews": [review_record]}
    exit_audit = {"status": "complete", "next_stage_allowed": True, "blocking_items": [], "documents": [
        {"document_key": entry["document_key"], "expected_pages": 1, "original_sha256": original.sha256,
         "processing_sha256": processing.sha256, "issues": []}]}
    # Simulate the unchanged historical definition that previously selected an original.
    definitions = {entry["document_key"]: {"original": "original.pdf", "registered": "original.pdf",
                                           "processing": None, "title": "Current source", "role": "background"}}
    return SimpleNamespace(**locals())


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_display_boxes_rotate_once_without_adding_crop_offset(current, rotation):
    with pymupdf.open(current.processed_path) as pdf:
        page = pdf[0]
        page.set_rotation(rotation)
        raw = page.get_text("blocks")[0][:4]
        expected = displayed(raw, 240, 340, rotation)
        assert _clip_bbox(raw, page).as_list() == pytest.approx(expected, abs=0.001)
        assert region_text(page, expected) == TEXT
        # The original raster extends past the crop. Its visible box is the
        # displayed page, and coverage is still exactly one.
        assert _image_boxes(page)[0].as_list() == pytest.approx([0, 0, page.rect.width, page.rect.height])
        assert inspect_pdf_page(page).image_coverage == 1
        assert verify_region_text(page, expected, TEXT, "region") == TEXT
        with pytest.raises(ValueError, match="source review required"):
            verify_region_text(page, expected, TEXT.replace("0.02", "0.20"), "old OCR")


def test_current_manifest_selects_new_derived_id_and_keeps_original_authority(current):
    documents = builder._reviewed_documents(current.manifest, current.review,
                                            catalog=current.catalog, settings=current.settings,
                                            definitions=current.definitions)
    document = documents[current.entry["document_key"]]
    assert document["registered"] == "OCR/current.pdf"
    ir, rotation = builder._parse_reviewed_page(
        {"physical_page": 1, "logical_page": "printed-1", "original_asset_id": current.original.asset_id},
        document, catalog=current.catalog, settings=current.settings)
    item = build_evidence(ir, (ir.source_spans[0].source_span_id,), evidence_role="background")
    assert item.source_text == TEXT
    assert item.authority_asset_id == current.original.asset_id
    assert item.processing_asset_id == current.processing.asset_id
    assert item.locations[0].rotation_deg == rotation == 90
    assert item.locations[0].bbox.as_list() == pytest.approx(ir.source_spans[0].bbox.as_list())
    assert sha(current.original_path) == current.original.sha256
    with pymupdf.open(current.original_path) as original, pymupdf.open(current.processed_path) as processed:
        assert original[0].get_pixmap().samples == processed[0].get_pixmap().samples


@pytest.mark.parametrize("failure", ["review_hash", "review_range", "wrong_derivation"])
def test_stale_or_incomplete_delivery_never_becomes_stage6_input(current, failure):
    review, catalog = copy.deepcopy(current.review), current.catalog
    if failure == "review_hash":
        review["full_corpus_reviews"][0]["processing_sha256"] = "old"
    elif failure == "review_range":
        review["full_corpus_reviews"][0]["reviewed_page_ranges"] = []
    else:
        catalog = replace(catalog, assets=(current.original, replace(current.processing, derived_from_asset_id="wrong")))
    with pytest.raises(ValueError):
        builder._reviewed_documents(current.manifest, review, catalog=catalog,
                                    settings=current.settings, definitions=current.definitions)


def test_stage6_pdf_input_uses_current_assets_without_stage5_exit_or_agent_review_flags(current):
    review = copy.deepcopy(current.review)
    record = review["full_corpus_reviews"][0]
    record.update(line_by_line_reviewed=False, table_cells_reviewed=False,
                  reading_order_reviewed=False, unresolved_text_count=None,
                  unresolved_table_cell_count=None, unresolved_page_mapping_count=None)
    documents = builder._reviewed_documents(current.manifest, review,
                                            catalog=current.catalog, settings=current.settings,
                                            definitions=current.definitions)
    assert documents[current.entry["document_key"]]["registered"] == current.processing.relative_path
    assert "stage5_exit_audit_sha256" not in STAGE5_BINDING_PATHS


def test_geometry_and_wrong_authority_are_rejected(current):
    document = {**current.definitions[current.entry["document_key"]], "registered": "OCR/current.pdf",
                "processing": "current.pdf"}
    with pytest.raises(ValueError, match="authority"):
        builder._parse_reviewed_page({"physical_page": 1, "logical_page": None, "original_asset_id": "wrong"},
                                    document, catalog=current.catalog, settings=current.settings)
    pdf = pymupdf.open(current.processed_path)
    pdf[0].set_rotation(0)
    changed = current.derived / "changed-geometry.pdf"
    pdf.save(changed)
    pdf.close()
    with pytest.raises(ValueError, match="geometry"):
        builder._parse_reviewed_page({"physical_page": 1, "logical_page": None},
                                    {**document, "processing": changed.name}, catalog=current.catalog, settings=current.settings)


def test_supplement_path_uses_current_pdf_instead_of_original_scan(current, monkeypatch):
    key = current.entry["document_key"]
    documents = builder._reviewed_documents(current.manifest, current.review,
                                            catalog=current.catalog, settings=current.settings, definitions=current.definitions)
    with pymupdf.open(current.processed_path) as pdf:
        box = _clip_bbox(pdf[0].get_text("blocks")[0][:4], pdf[0]).as_list()
    monkeypatch.setattr(builder, "evidence_groups_for_page", lambda *args: [{"group_id": "reviewed"}])
    monkeypatch.setattr(builder, "standard_units_for_page", lambda *args: [{"unit_id": "u", "bbox": box, "source_text": TEXT}])
    monkeypatch.setattr(builder, "append_reviewed_source_spans", lambda ir, *args, **kw: (ir, {}))
    monkeypatch.setattr(builder, "build_reviewed_group_evidence", lambda ir, *args, **kw:
                        build_evidence(ir, (ir.source_spans[0].source_span_id,), evidence_role="background"))
    rows = builder._reviewed_source_supplements_for_sample(
        {"document_key": key, "physical_page": 1, "logical_page": None}, catalog=current.catalog,
        settings=current.settings, documents=documents, standard_manifest={}, cross_page_manifest={"source_units": []},
        auxiliary_manifest={})
    assert rows[0][0].source_text == TEXT
    assert rows[0][0].processing_asset_id == current.processing.asset_id
    assert rows[0][0].authority_asset_id == current.original.asset_id
    monkeypatch.setattr(builder, "standard_units_for_page", lambda *args:
                        [{"unit_id": "u", "bbox": box, "source_text": TEXT.replace("0.02", "0.20")}])
    with pytest.raises(ValueError, match="source review required"):
        builder._reviewed_source_supplements_for_sample(
            {"document_key": key, "physical_page": 1, "logical_page": None}, catalog=current.catalog,
            settings=current.settings, documents=documents, standard_manifest={}, cross_page_manifest={"source_units": []},
            auxiliary_manifest={})


def test_cross_page_regions_read_current_text_and_convert_old_display_pixels_once(current):
    original_path = current.source / "two-original.pdf"
    processing_path = current.derived / "two-current.pdf"
    pdf = pymupdf.open()
    with pymupdf.open(current.original_path) as original:
        pdf.insert_pdf(original)
        pdf.insert_pdf(original)
    pdf.save(original_path)
    pdf.close()
    fragments, steps = [], []
    pdf = pymupdf.open(original_path)
    for index, page in enumerate(pdf):
        values = [("title", "Reviewed heading")] if index == 0 else []
        values.extend((f"step-{number}", f"Printed step {number}: 0.02 mm")
                      for number in (range(1, 6) if index == 0 else range(6, 10)))
        for offset, (_, text) in enumerate(values):
            page.insert_text((35, 65 + offset * 25), text, fontsize=9, render_mode=3)
        raw_lines = [line for block in page.get_text("dict")["blocks"] for line in block.get("lines", [])]
        boxes = []
        for (box_id, text), line in zip(values, raw_lines):
            bbox = displayed(line["bbox"], 240, 340, 90)
            boxes.append({"box_id": box_id, "text": text, "bbox_ocr_px": [value * 170 / 72 for value in bbox]})
            if box_id != "title":
                steps.append({"source_order": len(steps) + 1, "source_box_ids": [box_id],
                              "source_text": text, "source_label": box_id})
        fragments.append({"physical_page": index + 1, "logical_page": str(index + 1), "source_boxes": boxes})
    pdf.save(processing_path)
    pdf.close()
    original = replace(current.original, relative_path=original_path.name, sha256=sha(original_path))
    processing = replace(current.processing, relative_path="OCR/" + processing_path.name, sha256=sha(processing_path))
    catalog = replace(current.catalog, assets=(original, processing),
                      path_aliases={original.relative_path: original.asset_id, processing.relative_path: processing.asset_id})
    documents = {"D300N": {"original": original_path.name, "registered": processing.relative_path,
                            "processing": processing_path.name, "title": "Synthetic adjacent table", "role": "requirement_source"}}
    unit = {"document_key": "D300N", "source_unit_id": "synthetic-cross-page", "physical_pages": [1, 2],
            "fragments": fragments, "title_source_box_ids": ["title"], "steps": steps}
    item, ir = build_cross_page_evidence(unit, catalog=catalog, documents=documents, settings=current.settings)
    assert item.authority_asset_id == original.asset_id
    assert item.processing_asset_id == processing.asset_id
    assert {location.physical_page for location in item.locations} == {1, 2}
    assert all(location.rotation_deg == 90 for location in item.locations)
    assert all(step["source_text"] in item.source_text for step in steps)
    assert len(ir.source_spans) > len(item.source_span_ids)  # Current page spans are retained.
    old_unit = copy.deepcopy(unit)
    old_unit["fragments"][0]["source_boxes"][1]["text"] = "Printed step 1: 0.20 mm"
    with pytest.raises(ValueError, match="source review required"):
        build_cross_page_evidence(old_unit, catalog=catalog, documents=documents, settings=current.settings)


def test_baseline_classifies_new_derivative_from_actual_page_without_stage4(current):
    assets = {identity.asset_id: asdict(identity) for identity in current.catalog.assets}
    processing_path, original_path = baseline._registered_inputs(current.entry, assets, current.catalog, current.settings)
    assert processing_path == current.processed_path
    assert original_path == current.original_path
    with pymupdf.open(processing_path) as pdf:
        mode, basis = baseline._current_page_mode(pdf[0], LayoutProfile())
    assert mode == "mixed"
    assert basis["capability"]["text_character_count"] == len(TEXT)
    assert basis["capability"]["rotation_deg"] == 90
    assert basis["capability"]["image_coverage"] == 1
    # An unmodified original asset can also be a legitimate processing input.
    native_entry = {**current.entry, "processing_asset_id": current.original.asset_id,
                    "processing_relative_path": current.original.relative_path}
    assert baseline._registered_inputs(native_entry, assets, current.catalog, current.settings) == (original_path, original_path)
    with pymupdf.open(original_path) as pdf:
        assert baseline._current_page_mode(pdf[0], LayoutProfile())[0] == "scan_only"
    with pytest.raises(ValueError, match="bytes differ"):
        baseline._registered_inputs(current.entry, assets,
                                    replace(current.catalog, assets=(current.original, replace(current.processing, sha256="old"))),
                                    current.settings)


def test_blank_page_classification_requires_current_full_review_hash(current):
    assets = {identity.asset_id: asdict(identity) for identity in current.catalog.assets}
    review = {**current.review_record, "blank_pages": [1]}
    assert baseline._reviewed_blank_pages(current.entry, assets, {current.entry["document_key"]: review}) == ({1}, "bound_to_current_pdf")
    review["processing_sha256"] = "old"
    assert baseline._reviewed_blank_pages(current.entry, assets, {current.entry["document_key"]: review}) == (set(), "not_bound")


def test_full_baseline_uses_current_pages_without_any_stage4_artifact(current, monkeypatch):
    root = current.tmp_path
    manifest, audit, registry, review = [root / name for name in ("manifest.json", "input-audit.json", "assets.jsonl", "review.json")]
    manifest.write_text(json.dumps(current.manifest), encoding="utf-8")
    audit.write_text(json.dumps({"status": "pass", "errors": [], "input_fingerprint": "current",
                                 "fingerprint_components": {"pdfs": "current"}}), encoding="utf-8")
    registry.write_text("\n".join(json.dumps(asdict(asset)) for asset in current.catalog.assets), encoding="utf-8")
    review.write_text(json.dumps(current.review), encoding="utf-8")
    for field, value in {"PROJECT_ROOT": root, "SAMPLE_MANIFEST": manifest, "INPUT_AUDIT": audit,
                         "REGISTRY_ASSETS": registry, "FULL_REVIEW": review}.items():
        monkeypatch.setattr(baseline, field, value)
    monkeypatch.setattr(baseline, "Settings", SimpleNamespace(from_environment=lambda: current.settings))
    monkeypatch.setattr(baseline, "ocr_fingerprint", lambda: ("current", {"pdfs": "current"}))
    monkeypatch.setattr(baseline, "load_identity_catalog", lambda *args: current.catalog)
    result = baseline.benchmark()
    assert result["errors"] == []
    assert result["actual"]["page_count"] == 1
    assert result["documents"][0]["processing_asset_id"] == current.processing.asset_id
    assert result["page_records"][0]["page_mode"] == "mixed"
    assert result["page_records"][0]["full_review_binding_status"] == "bound_to_current_pdf"
    assert result["page_records"][0]["reviewed_blank"] is False
    assert result["documents"][0]["original_sha256"] == current.original.sha256
    audit.write_text(json.dumps({"status": "pass", "input_fingerprint": "historical"}), encoding="utf-8")
    with pytest.raises(ValueError, match="exact input fingerprint"):
        baseline.benchmark()


def test_boundary_main_uses_current_assets_and_keeps_quarantine_without_rapidocr(current, monkeypatch):
    root = current.tmp_path
    stage5, stage6 = root / "stage5", root / "stage6"
    stage5.mkdir()
    stage6.mkdir()
    manifest, registry, review = [stage5 / name for name in ("manifest.json", "assets.jsonl", "review.json")]
    manifest.write_text(json.dumps(current.manifest), encoding="utf-8")
    registry.write_text("\n".join(json.dumps(asdict(asset)) for asset in current.catalog.assets), encoding="utf-8")
    review.write_text(json.dumps(current.review), encoding="utf-8")
    table_path = stage5 / "stage5_table_truth_review.json"
    table_path.write_text(json.dumps({"records": []}), encoding="utf-8")
    truth_path = stage5 / "stage5_truth_annotations_2026-09-28.json"
    truth = {"source_input_fingerprint": "current", "source_fingerprint_components": {},
              "inputs": {"stage5_table_truth_review_sha256": sha(table_path),
                         "stage5_sample_manifest_sha256": sha(manifest), "source_assets_sha256": sha(registry),
                         "full_corpus_reviews_sha256": sha(review)}, "records": [{
        "document_key": current.entry["document_key"], "original_asset_id": current.original.asset_id,
        "processing_asset_id": current.processing.asset_id, "original_sha256": current.original.sha256,
        "processing_sha256": current.processing.sha256, "physical_page": 1, "logical_page": None,
        "truth_source": "Original materials original PDF", "categories": ["table"],
        "visual_review_status": "confirmed", "gate_disposition": "quarantine_structured_ocr",
        "reference_text": TEXT, "reference_kind": "table"}]}
    truth_path.write_text(json.dumps(truth), encoding="utf-8")
    (stage5 / "stage5_truth_annotations_2099-01-01.json").write_text("stale automatic dated truth", encoding="utf-8")
    (stage5 / "stage5_table_truth_review_2099-01-01.json").write_text("stale automatic dated table truth", encoding="utf-8")
    for field, value in {"ROOT": root, "STAGE5": stage5, "STAGE6": stage6, "MANIFEST": manifest,
                         "REGISTRY": registry, "FULL_REVIEW": review,
                         "TRUTH": truth_path, "TABLE_TRUTH": table_path,
                         "DOCUMENTS": current.definitions}.items():
        monkeypatch.setattr(boundary, field, value)
    monkeypatch.setattr(boundary, "Settings", SimpleNamespace(from_environment=lambda: current.settings))
    monkeypatch.setattr(boundary, "ocr_fingerprint", lambda: ("current", {}))
    monkeypatch.setattr(boundary, "load_identity_catalog", lambda *args: current.catalog)
    monkeypatch.setattr(boundary, "_table_truth", lambda *args, **kwargs: {})
    boundary.main()
    result = json.loads((stage6 / "stage6_evidence_golden_sample.json").read_text(encoding="utf-8"))
    assert "stage5_exit_audit" not in result["inputs"]
    assert "stage5_exit_audit_sha256" not in result["inputs"]
    assert result["inputs"]["source_input_fingerprint"] == "current"
    assert result["inputs"]["stage5_truth_annotations"] == "stage5/stage5_truth_annotations_2026-09-28.json"
    assert result["inputs"]["stage5_table_truth_review"] == "stage5/stage5_table_truth_review.json"
    assert result["inputs"]["stage5_table_truth_review_sha256"] == sha(table_path)
    assert not any("rapidocr" in field for field in result["inputs"])
    assert result["records"][0]["evidence_eligibility"] == "quarantined"
    assert result["records"][0]["reference_text"] is None


def test_golden_boundary_requires_current_identity_and_no_rapidocr(current, monkeypatch):
    monkeypatch.setattr(boundary, "DOCUMENTS", current.definitions)
    hashes = {"stage5_sample_manifest_sha256": "manifest", "source_assets_sha256": "assets",
              "full_corpus_reviews_sha256": "review"}
    truth = {"inputs": hashes, "records": [{"document_key": current.entry["document_key"], "physical_page": 1,
             "original_asset_id": current.original.asset_id, "processing_asset_id": current.processing.asset_id,
             "original_sha256": current.original.sha256, "processing_sha256": current.processing.sha256,
             "truth_source": "Original materials original PDF"}]}
    checked = boundary._validate_current_truth(truth, current.manifest, current.review,
                                               catalog=current.catalog, settings=current.settings, input_hashes=hashes)
    assert checked[0]["processing_asset_id"] == current.processing.asset_id
    assert "artifact_provenance" not in current.exit_audit
    for field in ("processing_sha256", "processing_asset_id", "original_sha256"):
        bad = copy.deepcopy(truth)
        bad["records"][0][field] = "old"
        with pytest.raises(ValueError, match="identity differs"):
            boundary._validate_current_truth(bad, current.manifest, current.review,
                                             catalog=current.catalog, settings=current.settings, input_hashes=hashes)
    with pytest.raises(ValueError, match="not bound"):
        boundary._validate_current_truth({**truth, "inputs": {}}, current.manifest, current.review,
                                         catalog=current.catalog, settings=current.settings, input_hashes=hashes)
    wrong_authority = copy.deepcopy(truth)
    wrong_authority["records"][0]["truth_source"] = "OCR PDF"
    with pytest.raises(ValueError, match="original PDF authority"):
        boundary._validate_current_truth(wrong_authority, current.manifest, current.review,
                                         catalog=current.catalog, settings=current.settings, input_hashes=hashes)
    assert boundary._eligibility("quarantine_structured_ocr") == "quarantined"
    assert boundary._eligibility("metadata_only") == "metadata_only"
    decision = {"decision": "accepted", "expected_page_fingerprint": "old"}
    assert builder._decision_for_fingerprint("page", "new", {"page": decision}) == ("needs_review", decision, True)


def test_unchanged_evidence_quote_still_requires_review_for_new_processing_identity(current):
    documents = builder._reviewed_documents(current.manifest, current.review,
                                            catalog=current.catalog, settings=current.settings, definitions=current.definitions)
    ir, _ = builder._parse_reviewed_page({"physical_page": 1, "logical_page": None},
                                        documents[current.entry["document_key"]], catalog=current.catalog, settings=current.settings)
    item = build_evidence(ir, (ir.source_spans[0].source_span_id,), evidence_role="background")
    fingerprint = page_review_fingerprint([item], source_input_fingerprint="current")
    assert fingerprint == page_review_fingerprint([asdict(item)], source_input_fingerprint="current")
    assert fingerprint != page_review_fingerprint([replace(item, processing_asset_id="new")], source_input_fingerprint="current")
    assert fingerprint != page_review_fingerprint([replace(item, evidence_version_id="new")], source_input_fingerprint="current")
    assert fingerprint != page_review_fingerprint([item], source_input_fingerprint="changed-PDF-bytes")
    assert fingerprint != hashlib.sha256(item.evidence_id.encode()).hexdigest()


def test_old_table_acceptance_cannot_bind_to_current_processing_pdf(current):
    documents = builder._reviewed_documents(current.manifest, current.review,
                                             catalog=current.catalog, settings=current.settings,
                                             definitions=current.definitions)
    decision = {"review_id": "historical-table", "document_key": current.entry["document_key"],
                "decision": "accepted_region_scoped", "source_text": TEXT}
    with pytest.raises(ValueError, match="requires revalidation"):
        table_builder._validate_review_bindings([decision], documents=documents, catalog=current.catalog,
                                                source_input_fingerprint="current")
    decision.update(source_input_fingerprint="current", original_asset_id=current.original.asset_id,
                    original_sha256=current.original.sha256, processing_asset_id=current.processing.asset_id,
                    processing_sha256=current.processing.sha256)
    table_builder._validate_review_bindings([decision], documents=documents, catalog=current.catalog,
                                            source_input_fingerprint="current")
    decision["processing_sha256"] = "previous PDF bytes"
    with pytest.raises(ValueError, match="requires revalidation"):
        table_builder._validate_review_bindings([decision], documents=documents, catalog=current.catalog,
                                                source_input_fingerprint="current")


def test_initial_rapidocr_slice_cannot_regenerate_current_outputs():
    with pytest.raises(RuntimeError, match="vertical slice is retired"):
        initial_slice.main()


def test_stage6_exit_rejects_stale_inputs_before_replacing_historical_audit(tmp_path, monkeypatch):
    output = tmp_path / "historical-exit.json"
    output.write_text("historical accepted audit must remain unchanged", encoding="utf-8")
    expected = {"source_input_fingerprint": "current", "full_corpus_reviews_sha256": "current review"}
    monkeypatch.setattr(stage6_exit, "ocr_fingerprint", lambda: ("current", {}))
    monkeypatch.setattr(stage6_exit, "current_stage5_bindings", lambda *args: expected)
    monkeypatch.setattr(stage6_exit, "_json", lambda name: {"status": "complete", "inputs": {
        "source_input_fingerprint": "current", "full_corpus_reviews_sha256": "old review"}})
    with pytest.raises(SystemExit, match="actual revalidation is required"):
        stage6_exit.main(["--output", str(output)])
    assert output.read_text(encoding="utf-8") == "historical accepted audit must remain unchanged"


def test_current_quality_reads_pdf_text_instead_of_echoing_its_reference(current, monkeypatch):
    manifest = copy.deepcopy(current.manifest)
    manifest["sample_page_count"] = 1
    manifest["documents"][0]["sample_pages"][0]["categories"] = ["numeric_and_unit"]
    truth = {"records": [{"document_key": current.entry["document_key"], "physical_page": 1,
                           "reference_kind": "independent_original_page_transcription",
                           "reference_text_sha256": "manual-text-hash", "reference_lines": [],
                           "structured_text_scoring_allowed": True, "gate_disposition": "structured_text_candidate",
                           "reference_text": TEXT.replace("0.02", "0.03"), "table_truth": None}]}
    assets = {asset.asset_id: asdict(asset) for asset in current.catalog.assets}
    monkeypatch.setattr(quality, "Settings", SimpleNamespace(from_environment=lambda: current.settings))
    monkeypatch.setattr(quality, "_current_inputs", lambda settings: (manifest, assets, {}, truth, "current", {}))
    monkeypatch.setattr(quality, "PROJECT_ROOT", current.tmp_path)
    for field in ("BASELINE", "TRUTH", "TABLE_TRUTH"):
        path = current.tmp_path / (field.lower() + ".json")
        path.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(quality, field, path)
    result = quality.benchmark()
    row = result["records"][0]
    assert row["processing_asset_id"] == current.processing.asset_id
    assert row["text_metrics"]["reviewed_pdf"]["edit_distance"] == 1
    assert row["text_metrics"]["reviewed_pdf"]["critical_tokens"]["numbers"]["f1"] < 1
    assert row["runtime"]["reviewed_pdf"]["coordinate_units"] == "display_pdf_points"
    assert "rapidocr" not in result["engines"]
    assert "ocr_artifact" not in result
    with pymupdf.open(current.processed_path) as pdf:
        assert quality._pdf_line_boxes(pdf[0])[0] == pytest.approx(
            dict(zip(("x0", "y0", "x1", "y1"), _clip_bbox(pdf[0].get_text("blocks")[0][:4], pdf[0]).as_list())))

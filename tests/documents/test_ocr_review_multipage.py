from turbine_kg.documents.ids import stable_id
from turbine_kg.documents.models import AssetPageRef, AssetRef, Document, DocumentRevision
from turbine_kg.documents.ocr_review import apply_reviewed_ocr_page_set
from turbine_kg.documents.parser import parse_page_inputs
from turbine_kg.documents.profiles import PageInput
from turbine_kg.evidence.builder import build_evidence


def test_reviewed_ocr_page_set_supports_one_evidence_across_two_real_pages():
    document_id = stable_id("doc", "multipage-ocr-test")
    revision_id = stable_id("rev", document_id, "v1")
    original_id = stable_id("asset", "multipage-original")
    derived_id = stable_id("asset", "multipage-derived")
    document = Document(document_id, "跨页材料", "test")
    revision = DocumentRevision(revision_id, document_id, "v1", "test")
    original = AssetRef(original_id, document_id, revision_id, "original", "source.pdf", "a" * 64, "source")
    derived = AssetRef(
        derived_id, document_id, revision_id, "derived_ocr", "OCR/source.pdf", "b" * 64,
        "ocr", derived_from_asset_id=original_id, derivation_type="ocr",
        derivation_run_id=stable_id("run", "multipage-ocr-derivation"),
    )
    inputs = tuple(
        PageInput(
            derived_id, revision_id, index, 600, 800, 0, "旧 OCR 文字" * 5,
            "ocr", 0.8, related_asset_page_refs=(AssetPageRef(original_id, index),),
        )
        for index in range(2)
    )
    parsed = parse_page_inputs(
        document, revision, (original, derived), inputs,
        parsing_run_id=stable_id("run", "multipage-ocr-parse"),
    )
    boxes = {
        1: [{"text": "建立监督", "x0": 80, "y0": 700, "x1": 180, "y1": 720}],
        2: [{"text": "和考核制度。", "x0": 80, "y0": 90, "x1": 200, "y1": 110}],
    }
    reviewed = apply_reviewed_ocr_page_set(
        parsed, boxes, dpi=72, logical_pages={1: "1", 2: "2"},
        authority_geometry={1: (600, 800, 0), 2: (600, 800, 0)},
    )
    evidence = build_evidence(
        reviewed, tuple(span.source_span_id for span in reviewed.source_spans),
        evidence_role="requirement_source",
    )
    assert [location.physical_page for location in evidence.locations] == [1, 2]
    assert [location.logical_page for location in evidence.locations] == ["1", "2"]
    assert evidence.source_text == "建立监督\n和考核制度。"

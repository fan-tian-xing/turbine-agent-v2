from __future__ import annotations

from turbine_kg.documents.ids import stable_id
from turbine_kg.documents.models import AssetRef, BBox, Document, DocumentRevision
from turbine_kg.documents.parser import parse_page_inputs
from turbine_kg.documents.profiles import LayoutProfile, PageInput, RawTextBlock
from turbine_kg.stage3.document_compat import project_document_ir


def _ir():
    document_id = stable_id("doc", "compat")
    revision_id = stable_id("rev", document_id, "registry-v1")
    asset = AssetRef("asset-" + "1" * 20, document_id, revision_id, "original", "x.pdf", "a" * 64, "source")
    revision = DocumentRevision(revision_id, document_id, "registry-v1", "controlled_registry_baseline")
    pages = (
        PageInput(
            asset.asset_id,
            revision_id,
            0,
            600,
            800,
            0,
            "兼容文本" * 20,
            "native",
            0.1,
            text_blocks=(RawTextBlock("兼容文本" * 20, BBox(10, 10, 100, 30)),),
        ),
        PageInput(asset.asset_id, revision_id, 1, 600, 800, 0, "", "scan_only", 0.9),
    )
    return parse_page_inputs(
        Document( document_id, "compat", "registry"),
        revision,
        (asset,),
        pages,
        profile=LayoutProfile(profile_id="test"),
        parser_version="1",
        config_fingerprint="cfg",
    )


def test_projection_preserves_structure_without_inventing_semantics():
    view = project_document_ir(_ir())
    assert view.revision.document_logical_id.startswith("doc-")
    assert [page.page_number for page in view.pages] == [1, 2]
    assert view.pages[0].text == "兼容文本" * 20
    assert view.pages[1].text == ""
    assert view.spans[0].quote == "兼容文本" * 20
    assert not hasattr(view, "logical_document")

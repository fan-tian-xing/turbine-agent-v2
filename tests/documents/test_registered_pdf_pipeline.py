from __future__ import annotations

import json
from pathlib import Path

import fitz

from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.stage3.document_compat import project_document_ir, project_registered_pdf


def test_registry_to_pdf_to_stage3_structural_projection(tmp_path: Path):
    document_id = "doc-" + "a" * 20
    revision_id = "rev-" + "b" * 20
    asset_id = "asset-" + "c" * 20
    revision_file = tmp_path / "revision_identity.tsv"
    revision_file.write_text(
        "document_logical_id\trevision_id\trevision_label\n"
        f"{document_id}\t{revision_id}\tregistry-v1\n",
        encoding="utf-8",
    )
    assets_file = tmp_path / "source_assets.jsonl"
    assets_file.write_text(
        json.dumps(
            {
                "asset_id": asset_id,
                "document_logical_id": document_id,
                "revision_id": revision_id,
                "asset_kind": "original",
                "relative_path": "sample/sample.pdf",
                "sha256": "a" * 64,
                "source_root_id": "source",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    catalog = load_identity_catalog(assets_file, revision_file)
    pdf_path = tmp_path / "sample.pdf"
    pdf = fitz.open()
    page = pdf.new_page(width=600, height=800)
    page.insert_text((72, 72), "Registry native text " * 3)
    pixmap = fitz.Pixmap(fitz.csRGB, (0, 0, 10, 10), 0)
    page.insert_image(fitz.Rect(400, 400, 500, 500), pixmap=pixmap)
    pdf.save(pdf_path)
    pixmap = None
    pdf.close()

    ir = parse_registered_pdf(pdf_path, "sample/sample.pdf", catalog, title="受控样本文档")
    assert len(ir.figures) == 1
    assert ir.blocks[-1].block_type == "image"
    assert ir.figures[0].block_version_id == ir.blocks[-1].block_version_id
    project_document_ir(ir)
    view = project_registered_pdf(pdf_path, "sample/sample.pdf", catalog, title="受控样本文档")
    assert view.revision.revision_id == revision_id
    assert view.pages[0].page_id.startswith("page-")
    assert view.spans[0].span_id.startswith("span-")
    assert view.pages[0].text == ("Registry native text " * 3).strip()

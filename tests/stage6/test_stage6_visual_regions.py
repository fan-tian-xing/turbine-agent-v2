"""Source-bound figure context must never become an invented drawing claim."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.evidence.models import EvidenceBundle
from turbine_kg.evidence.validation import validate_evidence_bundle
from turbine_kg.settings import Settings
from scripts.stage6_visual_regions import build_visual_evidence, load_regions


ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS = {
    "DL5190.3": {
        "registered": "OCR/DL5190.3—2019电力建设施工技术规范第3部分：汽轮发电机组(OCR).pdf",
        "original": "标准法规/DL5190.3—2019电力建设施工技术规范第3部分：汽轮发电机组.pdf",
        "processing": "DL5190.3—2019电力建设施工技术规范第3部分：汽轮发电机组(OCR).pdf",
        "title": "DL/T 5190.3—2019 电力建设施工技术规范 第3部分：汽轮发电机组",
        "role": "requirement_source",
    },
    "D300N": {
        "registered": "OCR/汽轮机本体安装及维护说明书(OCR).pdf",
        "original": "汽轮机说明书/汽轮机本体安装及维护说明书.pdf",
        "processing": "汽轮机本体安装及维护说明书(OCR).pdf",
        "title": "N-300 汽轮机本体安装及维护说明书",
        "role": "requirement_source",
    },
    "auxiliary_installation_book": {
        "registered": "OCR/汽轮机辅机安装（第二版）(OCR).pdf",
        "original": "2.书籍/260824 扫描文件/汽轮机辅机安装（第二版）.pdf",
        "processing": "汽轮机辅机安装（第二版）(OCR).pdf",
        "title": "汽轮机辅机安装（第二版）",
        "role": "background",
    },
}


def _catalog():
    return load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )


def test_eight_reviewed_figures_have_caption_only_visual_evidence() -> None:
    settings = Settings.from_environment()
    manifest = load_regions(settings=settings)
    catalog = _catalog()
    assert len(manifest["regions"]) == 8
    for region in manifest["regions"]:
        item, ir, metadata = build_visual_evidence(
            region, catalog=catalog, documents=DOCUMENTS, settings=settings,
        )
        assert item.disposition == "visual_only"
        assert item.review_status == "accepted"
        assert item.source_text == region["caption"]["text"]
        assert item.content_kind == "caption"
        assert item.figure_context[0].context_kind == "whole_figure"
        assert metadata["figure_bbox_pdf_pt"] == region["figure_bbox_pdf_pt"]
        assert {location.physical_page for location in item.locations} == {
            region["caption"]["physical_page"]
        }
        assert metadata["cross_page_caption"] == (
            region["caption"]["physical_page"] != region["physical_page"]
        )
        assert len(ir.figures) >= 1
        validate_evidence_bundle(
            EvidenceBundle(1, ir.revision.revision_id, (item,), ir.parsing_run.output_fingerprint), ir,
        )


def test_caption_drift_is_rejected(tmp_path: Path) -> None:
    manifest = load_regions()
    manifest["regions"][0]["caption"]["text"] = "图4.5.8-1 未经原件核对的图注"
    path = tmp_path / "visual_regions.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="text/region differs"):
        load_regions(path)


def test_original_pdf_fingerprint_drift_is_rejected(tmp_path: Path) -> None:
    manifest = load_regions()
    manifest["documents"]["DL5190.3"]["original_sha256"] = "0" * 64
    path = tmp_path / "visual_regions_changed_original.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="visual-region PDF fingerprint changed"):
        load_regions(path)

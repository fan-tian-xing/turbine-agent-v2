"""The work and record-directory columns of D300N row 8 stay separate."""

from pathlib import Path

from scripts.stage6_d300n_p50_regions import build_reviewed_evidence, load_regions
from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[2]


def test_p50_row8_original_page_columns_have_distinct_source_spans():
    data = load_regions()
    assert [unit["table_column"] for unit in data["regions"]] == [
        "表题", "项号", "工作内容", "附图号", "证明书目录", "工作内容", "证明书目录",
    ]
    assert data["regions"][0]["source_text"] == "续表2-14-1"
    assert "特别是底部间隙必须认真测量" in data["regions"][5]["source_text"]
    assert "前轴承箱和中低压轴" not in data["regions"][5]["source_text"]
    assert "前轴承箱和中低压轴" in data["regions"][6]["source_text"]
    settings = Settings.from_environment()
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    ir = parse_registered_pdf(
        settings.ocr_derived_root / data["ocr_pdf_filename"],
        "OCR/汽轮机本体安装及维护说明书(OCR).pdf", catalog,
        title="汽轮机本体安装及维护说明书", page_indices=(49,),
    )
    adapted, proposals = build_reviewed_evidence(ir, data=data)
    assert len(adapted.source_spans) == len(ir.source_spans) + 7
    assert len(proposals) == 7
    assert [item[0].source_text for item in proposals[:5]] == [
        "续表2-14-1", "项\n号", "工作内容", "附图号", "证明书目录",
    ]
    work, directory = proposals[5:]
    assert work[0].source_text.startswith("8\n轴承箱上半装配")
    assert directory[0].source_text.startswith("前轴承箱和中低压轴")
    assert all(item[1]["stage12_extractability"] == "context_only" for item in proposals)
    assert not work[0].table_context and not directory[0].table_context
    assert work[0].locations[0].bbox.x1 < directory[0].locations[0].bbox.x0

"""The quality gate accepts only manifest-bound visual captions."""

from __future__ import annotations

import copy
import sys
from pathlib import Path

from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.documents.models import record_value
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate_stage6_evidence import validate_visual_binding  # noqa: E402
from scripts.stage6_visual_regions import build_visual_evidence, load_regions  # noqa: E402


def test_cross_page_caption_requires_original_figure_binding() -> None:
    manifest = load_regions()
    region = next(row for row in manifest["regions"]
                  if row["region_id"] == "dl5190-p25-fig-4.5.8-3")
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    item, _, metadata = build_visual_evidence(region, catalog=catalog, settings=Settings.from_environment(),
                                              documents={"DL5190.3": {
                                                  "registered": "OCR/" + manifest["documents"]["DL5190.3"]["processing_relative_path"],
                                                  "original": manifest["documents"]["DL5190.3"]["original_relative_path"],
                                                  "processing": manifest["documents"]["DL5190.3"]["processing_relative_path"],
                                                  "title": "DL/T 5190.3—2019 电力建设施工技术规范 第3部分：汽轮发电机组",
                                                  "role": "requirement_source",
                                              }})
    row = {**metadata, "document_key": "DL5190.3", "input": {"physical_page": 25},
           "evidence": record_value(item)}
    assert row["evidence"]["locations"][0]["physical_page"] == 26
    assert validate_visual_binding(row, region)

    altered = copy.deepcopy(row)
    altered["figure_bbox_pdf_pt"][0] += 1
    assert not validate_visual_binding(altered, region)
    altered = copy.deepcopy(row)
    altered["evidence"]["disposition"] = "structured"
    assert not validate_visual_binding(altered, region)
    altered = copy.deepcopy(row)
    altered["evidence"]["locations"][0]["physical_page"] = 25
    assert not validate_visual_binding(altered, region)
    altered = copy.deepcopy(row)
    altered["stage12_extractability"] = "source_text"
    assert not validate_visual_binding(altered, region)
    assert not validate_visual_binding(row, None)

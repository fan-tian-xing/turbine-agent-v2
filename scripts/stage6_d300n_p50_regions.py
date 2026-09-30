"""Keep D300N p50 row 8 work and record-directory columns separate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf

from scripts.stage6_reviewed_pdf_regions import append_pdf_regions
from turbine_kg.documents.models import DocumentIR
from turbine_kg.evidence import build_evidence
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/stage6/stage6_d300n_p50_row8_regions.json"


def load_regions(path: Path = MANIFEST, *, settings: Settings | None = None) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if (data.get("schema_version") != 1
            or data.get("artifact_kind") != "stage6_d300n_p50_row8_regions"
            or data.get("document_key") != "D300N"
            or [unit.get("table_column") for unit in data.get("regions", [])]
            != ["表题", "项号", "工作内容", "附图号", "证明书目录", "工作内容", "证明书目录"]):
        raise ValueError("D300N p50 reviewed column manifest changed")
    settings = Settings.from_environment() if settings is None else settings
    for path_value, expected in (
        (settings.source_root / data["original_pdf_relative_path"], data["original_pdf_sha256"]),
        (settings.ocr_derived_root / data["ocr_pdf_filename"], data["ocr_pdf_sha256"]),
    ):
        if not path_value.is_file() or hashlib.sha256(path_value.read_bytes()).hexdigest() != expected:
            raise ValueError("D300N reviewed PDF identity changed")
    for unit in data["regions"]:
        box = unit["bbox"]
        if (unit.get("physical_page") != 50 or unit.get("status") != "confirmed_original_page_region"
                or len(box) != 4 or not (0 <= box[0] < box[2] <= 595 and 0 <= box[1] < box[3] <= 842)
                or unit.get("stage12_extractability") != "context_only"
                or unit.get("source_text") != unit.get("ocr_reference_text")):
            raise ValueError("invalid D300N p50 reviewed column region")
    return data


def build_reviewed_evidence(ir: DocumentIR, *, data: dict | None = None,
                            settings: Settings | None = None):
    """Return caption, four headers and two separate row-8 column proposals."""
    settings = Settings.from_environment() if settings is None else settings
    data = load_regions(settings=settings) if data is None else data
    if len(ir.pages) != 1 or ir.pages[0].display_page_number != 50:
        raise ValueError("D300N row-8 source requires original physical page 50")
    path = settings.ocr_derived_root / data["ocr_pdf_filename"]
    fragments = [{"physical_page": 50, "source_boxes": [{
        "box_id": unit["unit_id"], "bbox_pdf_pt": unit["bbox"], "text": unit["source_text"],
    }]} for unit in data["regions"]]
    with pymupdf.open(path) as pdf:
        adapted, links = append_pdf_regions(ir, pdf, fragments)
    result = []
    for unit in data["regions"]:
        item = build_evidence(
            adapted, (links[unit["unit_id"]],),
            evidence_role="requirement_source" if unit["table_column"] == "工作内容" else "background",
            disposition="region_scoped",
        )
        result.append((item, {
            "source_supplement_kind": "d300n_row8_reviewed_column",
            "source_supplement_group_id": unit["unit_id"],
            "stage12_extractability": unit["stage12_extractability"],
            "extractability_reason": "Original table row 8 has separate work and record-directory columns; no cell-level TableContext is yet claimed.",
            "table_column": unit["table_column"],
        }))
    return adapted, result

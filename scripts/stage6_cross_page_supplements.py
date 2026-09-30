"""Build one source-complete Evidence unit across verified adjacent PDF pages."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import pymupdf

from turbine_kg.documents.ids import stable_id
from turbine_kg.documents.page_identity import apply_page_identity
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.evidence import build_evidence
from turbine_kg.settings import Settings
from scripts.stage6_reviewed_pdf_regions import append_pdf_regions


ROOT = Path(__file__).resolve().parents[1]
SUPPLEMENT = ROOT / "data/stage6/stage6_cross_page_source_units.json"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def load_supplements(path: Path = SUPPLEMENT) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or len(data.get("source_units", [])) != 6:
        raise ValueError("Stage 6 cross-page supplement shape changed")
    settings = Settings.from_environment()
    originals = data["inputs"]["original_pdfs"]
    for key, record in originals.items():
        original = settings.source_root / record["relative_path"]
        if _sha(original.read_bytes()) != record["sha256"]:
            raise ValueError(f"cross-page original PDF changed: {key}")
    ids: set[str] = set()
    for unit in data["source_units"]:
        unit_id = unit["source_unit_id"]
        if unit_id in ids or unit.get("source_review_status") != "original_pdf_visually_confirmed":
            raise ValueError(f"duplicate or unreviewed cross-page source unit: {unit_id}")
        ids.add(unit_id)
        physical_pages = unit["physical_pages"]
        if len(physical_pages) != 2 or physical_pages != sorted(physical_pages):
            raise ValueError(f"cross-page source unit must name adjacent ordered pages: {unit_id}")
        if physical_pages[1] != physical_pages[0] + 1:
            raise ValueError(f"cross-page source pages are not adjacent: {unit_id}")
        if [fragment["physical_page"] for fragment in unit["fragments"]] != physical_pages:
            raise ValueError(f"cross-page fragments differ from declared pages: {unit_id}")
        source_sha = originals[unit["document_key"]]["sha256"]
        for fragment in unit["fragments"]:
            if fragment.get("original_pdf_sha256") != source_sha or not fragment.get("logical_page"):
                raise ValueError(f"cross-page fragment source identity is incomplete: {unit_id}")
            for box in fragment["source_boxes"]:
                if _sha(box["text"].encode("utf-8")) != box["source_text_sha256"]:
                    raise ValueError(f"cross-page source text hash mismatch: {unit_id}")
    return data


def unit_for_sample_page(data: dict, document_key: str, physical_page: int) -> dict | None:
    matches = [
        unit for unit in data["source_units"]
        if unit["document_key"] == document_key and physical_page in unit["physical_pages"]
    ]
    if len(matches) > 1:
        raise ValueError(f"multiple cross-page source units on {document_key} p{physical_page}")
    return matches[0] if matches else None


def build_cross_page_evidence(unit: dict, *, catalog, documents: dict, settings: Settings):
    """Return (Evidence, DocumentIR) after exact original-page source checks."""

    document = documents[unit["document_key"]]
    pages = tuple(unit["physical_pages"])
    original_path = settings.source_root / document["original"]
    processing_path = (settings.ocr_derived_root / document["processing"]
                       if document["processing"] is not None else original_path)
    with pymupdf.open(original_path) as authority, pymupdf.open(processing_path) as processing:
        for number in pages:
            original_page, current_page = authority[number - 1], processing[number - 1]
            if (original_page.mediabox != current_page.mediabox
                    or original_page.cropbox != current_page.cropbox
                    or original_page.rotation != current_page.rotation):
                raise ValueError("reviewed cross-page PDF geometry differs from original authority")
    logical_pages = {part["physical_page"]: part["logical_page"] for part in unit["fragments"]}
    processing_asset = catalog.asset_for_path(document["registered"])
    parsing_run_id = stable_id(
        "run", "stage6-cross-page-current-pdf-v2", unit["source_unit_id"],
        processing_asset.sha256,
    )
    if unit["document_key"] == "HAF103":
        ir = parse_registered_pdf(
            processing_path, document["registered"], catalog, title=document["title"],
            page_indices=tuple(page - 1 for page in pages),
            parser_version="stage6-cross-page-current-pdf-v2",
            parsing_run_id=parsing_run_id,
        )
        ir = apply_page_identity(ir, logical_pages)
        selected = []
        for fragment in unit["fragments"]:
            page = next(page for page in ir.pages if page.display_page_number == fragment["physical_page"])
            spans = [span for span in ir.source_spans if span.page_id == page.page_id]
            for box in fragment["source_boxes"]:
                bbox = box["bbox_pdf_pt"]
                matches = [
                    span for span in spans
                    if span.quote == box["text"] and span.bbox is not None
                    and all(abs(getattr(span.bbox, side) - float(value)) <= 0.6
                            for side, value in zip(("x0", "y0", "x1", "y1"), bbox))
                ]
                if len(matches) != 1:
                    raise ValueError(f"unbound native cross-page quote: {unit['source_unit_id']}")
                selected.append(matches[0].source_span_id)
        item = build_evidence(ir, tuple(selected), evidence_role=document["role"], disposition="structured")
        if _compact(item.source_text) != _compact(unit["complete_source_text"]):
            raise ValueError(f"cross-page source text changed: {unit['source_unit_id']}")
        return item, ir

    if unit["document_key"] != "D300N":
        raise ValueError(f"unsupported cross-page source: {unit['document_key']}")
    ir = parse_registered_pdf(
        processing_path, document["registered"], catalog, title=document["title"],
        page_indices=tuple(page - 1 for page in pages),
        parser_version="stage6-cross-page-current-pdf-v2",
        parsing_run_id=parsing_run_id,
    )
    ir = apply_page_identity(ir, logical_pages)
    with pymupdf.open(processing_path) as processing_pdf:
        ir, links = append_pdf_regions(ir, processing_pdf, unit["fragments"])
    by_span_id = {span.source_span_id: span for span in ir.source_spans}
    by_box_id = {box_id: by_span_id[sid] for box_id, sid in links.items()}
    used_ids = list(unit["title_source_box_ids"])
    if [step["source_order"] for step in unit["steps"]] != list(range(1, 10)):
        raise ValueError("D300N row 6 source steps are not a complete a-i sequence")
    for step in unit["steps"]:
        used_ids.extend(step["source_box_ids"])
        quote = "".join(by_box_id[box_id].quote for box_id in step["source_box_ids"])
        if _compact(quote) != _compact(step["source_text"]):
            raise ValueError(f"D300N step text changed: {step['source_label']}")
    selected = [by_box_id[box_id].source_span_id for box_id in dict.fromkeys(used_ids)]
    item = build_evidence(ir, tuple(selected), evidence_role=document["role"], disposition="region_scoped")
    if {location.physical_page for location in item.locations} != set(pages):
        raise ValueError("D300N row 6 Evidence did not bind both original pages")
    return item, ir

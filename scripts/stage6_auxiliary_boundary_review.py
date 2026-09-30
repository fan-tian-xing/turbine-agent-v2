"""Original-page boundary proposals for three auxiliary-book sample pages.

This adapter checks the current OCR and Evidence before returning group links.
Only the original-page transcriptions in the manifest may become manual
SourceSpans; this module never promotes a group to canonical Evidence.
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import pymupdf

from scripts.stage6_reviewed_pdf_regions import verify_region_text
from turbine_kg.documents.ids import block_version_id, source_span_id, stable_id
from turbine_kg.documents.models import BBox, BlockVersion, DocumentIR, ManualCorrection, SourceSpan, record_value
from turbine_kg.documents.validation import validate_document_ir
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/stage6/stage6_auxiliary_boundary_review.json"
ANNOTATIONS = ROOT / "data/stage6/stage6_evidence_annotations.jsonl"
DOCUMENT_KEY = "auxiliary_installation_book"


def load_review(path: Path = MANIFEST) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("artifact_kind") != "stage6_auxiliary_boundary_review_proposal":
        raise ValueError("unsupported auxiliary boundary review")
    if data.get("document_key") != DOCUMENT_KEY or data.get("sample_physical_pages") != [244, 360, 468]:
        raise ValueError("auxiliary boundary review scope changed")
    if data["unmarked_sample_questions"]["selected_answers"] != [None] * 7:
        raise ValueError("unmarked original-page question acquired a selected answer")
    units = data["source_units"]
    if len(units) != 3 or {u["physical_page"] for u in units} != {243, 245, 469}:
        raise ValueError("cross-page continuation source units changed")
    if len({u["unit_id"] for u in units}) != len(units):
        raise ValueError("duplicate continuation source unit")
    for unit in units:
        box = unit["bbox"]
        if (len(box) != 4 or not (0 <= box[0] < box[2] <= 397.1 and 0 <= box[1] < box[3] <= 572.8)
                or not unit["source_text"].strip() or not unit["ocr_reference_text"].strip()):
            raise ValueError(f"invalid reviewed continuation: {unit['unit_id']}")
    return data


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _paths(data: dict, settings: Settings) -> tuple[Path, Path]:
    return (settings.source_root / data["original_pdf_relative_path"],
            settings.ocr_derived_root / data["ocr_pdf_filename"])


def _page_rows(rows: list[dict], number: int) -> list[dict]:
    return [row for row in rows if row.get("document_key") == DOCUMENT_KEY
            and row.get("input", {}).get("physical_page") == number]


def _text(row: dict) -> str:
    evidence = row["evidence"]
    return evidence.get("effective_text") or evidence["source_text"]


def _members(rows: list[dict]) -> list[str]:
    return [row["evidence"]["evidence_id"] for row in rows]


def _partition(rows: list[dict], starts: list[str], counts: list[int]) -> list[list[dict]]:
    if sum(counts) != len(rows):
        raise ValueError("current Evidence count differs from reviewed page partition")
    groups = []
    offset = 0
    for anchor, count in zip(starts, counts):
        group = rows[offset:offset + count]
        if anchor and anchor not in _text(group[0]):
            raise ValueError(f"current Evidence boundary differs: {anchor}")
        groups.append(group)
        offset += count
    return groups


def audit_boundary_review(data: dict | None = None, rows: list[dict] | None = None,
                          *, settings: Settings | None = None) -> dict:
    """Bind current page fragments once, while leaving cross-page use pending."""
    data = load_review() if data is None else data
    if data["unmarked_sample_questions"]["selected_answers"] != [None] * 7:
        raise ValueError("unmarked original-page question acquired a selected answer")
    settings = Settings.from_environment() if settings is None else settings
    rows = ([json.loads(line) for line in ANNOTATIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
            if rows is None else rows)
    original, ocr = _paths(data, settings)
    if (not original.is_file() or not ocr.is_file()
            or _sha256(original) != data["original_pdf_sha256"]
            or _sha256(ocr) != data["ocr_pdf_sha256"]):
        raise ValueError("reviewed auxiliary PDF identity changed")
    with pymupdf.open(ocr) as pdf:
        for unit in data["source_units"]:
            verify_region_text(pdf[unit["physical_page"] - 1], unit["bbox"],
                               unit["ocr_reference_text"], unit["unit_id"])
    grouped = {}
    p244 = _page_rows(rows, 244)
    p360 = _page_rows(rows, 360)
    p468 = _page_rows(rows, 468)
    q244 = _partition(p244, ["在有裂纹处", "Je4C3267", "Je4C3268", "Je4C3269", "Je4C3270"], [1, 4, 2, 2, 2])
    q360 = _partition(p360, ["La1F2009", "La1F2010"], [8, 3])
    from scripts.stage6_auxiliary_supplements import source_units_for_page
    from scripts.stage6_auxiliary_supplements import load_supplements
    supplement = load_supplements()
    for number, page_rows in ((244, p244), (360, p360)):
        units = source_units_for_page(supplement, DOCUMENT_KEY, number)
        if ([row.get("source_supplement_group_id") for row in page_rows]
                != [unit["unit_id"] for unit in units]
                or [_text(row) for row in page_rows] != [unit["source_text"] for unit in units]
                or any("鉴定试题库" in _text(row) for row in page_rows)):
            raise ValueError(f"p{number} original-page body Evidence changed or includes sidebar")
    if not ("在有裂纹处" in _text(q244[0][0]) and "而变" in _text(q244[-1][-1])):
        raise ValueError("p244 cross-page answer boundaries changed")
    labels = [item["label"] for item in data["groups"][5]["items"]]
    symbols = [item["symbol"] for item in data["groups"][5]["items"]]
    if (labels != ["流量", "扬程", "转速", "轴功率", "效率", "比转速"]
            or symbols != ["Q", "H", "n", "N", "η", None]
            or any(f"（{index}）{label}" not in _text(q360[0][index + 1])
                   for index, label in enumerate(labels, 1))
            or "用字母η表示" not in re.sub(r"\s+", "", _text(q360[0][6]))):
        raise ValueError("p360 parallel pump parameter sources changed")
    if "真空也不能太高" not in _text(q360[-1][-1]):
        raise ValueError("p360 second answer lost the high-vacuum paragraph")
    if (len(p468) != 16 or "试卷样例" not in _text(p468[0])
            or data["unmarked_sample_questions"]["printed_section_heading"] not in _text(p468[1])):
        raise ValueError("p468 sample-question Evidence partition changed")
    for number in range(1, 8):
        stem, options = p468[2 * number:2 * number + 2]
        normalized_stem = unicodedata.normalize("NFKC", _text(stem))
        normalized_options = unicodedata.normalize("NFKC", _text(options))
        if (not re.match(rf"^{number}[\.．]", _text(stem))
                or re.search(r"\([ABCD]\)", normalized_stem)
                or "(A)" not in normalized_options
                or (number < 7 and "(D)" not in normalized_options)
                or (number == 7 and "(D)" in normalized_options)):
            raise ValueError(f"p468 question {number} or its printed options moved")
        # The original-page answer brackets are blank; option letters in the
        # choices themselves are never a selected answer.
        grouped[f"aux-unmarked-q{number}"] = {
            "evidence_ids": _members([stem, options]),
            "selected_answer": None,
            "semantic_use": "question_context_only",
            "integration_status": "pending_page_469_option" if number == 7 else "reviewed_question_context",
        }
    if "酚醛玻璃钢" not in _text(p468[-1]):
        raise ValueError("p468 question 7 option C changed")
    for group, members in zip(data["groups"], [*q244, *q360]):
        if len(members) != group["current_sample_evidence_count"]:
            raise ValueError(f"group member count changed: {group['group_id']}")
        grouped[group["group_id"]] = {
            "evidence_ids": _members(members),
            "integration_status": group["integration_status"],
            "kind": group["kind"],
        }
    return {
        "status": "current_proposal",
        "canonical_binding_status": "pending",
        "checked_sample_pages": [244, 360, 468],
        "original_context_pages": [243, 245, 469],
        "group_links": grouped,
        "unmarked_answer_count": 7,
        "missing_canonical_continuation_pages": [243, 245, 469],
    }


def append_continuation_source_spans(ir: DocumentIR, *, data: dict | None = None,
                                     settings: Settings | None = None) -> tuple[DocumentIR, dict[str, str]]:
    """Build in-memory original-reviewed spans for p243/245/469 only.

    These pages are outside the 36-page sample. The returned IR and links are
    proposals for later cross-page Evidence construction, never approval.
    """
    validate_document_ir(ir)
    data = load_review() if data is None else data
    settings = Settings.from_environment() if settings is None else settings
    original, ocr = _paths(data, settings)
    if (_sha256(original) != data["original_pdf_sha256"]
            or _sha256(ocr) != data["ocr_pdf_sha256"]):
        raise ValueError("reviewed auxiliary PDF identity changed")
    if len(ir.pages) != 1:
        raise ValueError("one reviewed continuation page is required")
    page = ir.pages[0]
    number = page.display_page_number
    units = [unit for unit in data["source_units"] if unit["physical_page"] == number]
    if len(units) != 1 or "+stage6-auxiliary-boundary-v1" in ir.parsing_run.parser_version:
        raise ValueError("page is not a fresh reviewed continuation page")
    with pymupdf.open(ocr) as pdf:
        verify_region_text(pdf[number - 1], units[0]["bbox"],
                           units[0]["ocr_reference_text"], units[0]["unit_id"])
    blocks, spans, corrections = list(ir.blocks), list(ir.source_spans), list(ir.manual_corrections)
    links = {}
    for unit in units:
        ordinal = max((block.block_ordinal for block in blocks), default=-1) + 1
        order = max((block.reading_order for block in blocks), default=-1) + 1
        bid = block_version_id(ir.parsing_run.parsing_run_id, page.page_id, ordinal)
        value = unit["source_text"]
        bbox = BBox(*unit["bbox"])
        blocks.append(BlockVersion(bid, page.page_id, ir.parsing_run.parsing_run_id, ordinal,
                                   "paragraph", value, bbox, order, text_origin="manual_correction"))
        sid = source_span_id(bid, 0, value)
        spans.append(SourceSpan(sid, page.page_id, (bid,), value, "paragraph", 0, len(value),
                                bbox, "manual_correction"))
        cid = stable_id("correction", "stage6-auxiliary-boundary", unit["unit_id"], value)
        corrections.append(ManualCorrection(cid, bid, value, value,
                                            "Original PDF visual transcription of cross-page text",
                                            "Codex_original_pdf_page_review", data["reviewed_at"]))
        links[unit["unit_id"]] = sid
    material = {"blocks": [record_value(x) for x in blocks],
                "source_spans": [record_value(x) for x in spans],
                "tables": [record_value(x) for x in ir.tables],
                "table_cells": [record_value(x) for x in ir.table_cells],
                "manual_corrections": [record_value(x) for x in corrections]}
    fingerprint = hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
    adapted = replace(ir, blocks=tuple(blocks), source_spans=tuple(spans),
                      manual_corrections=tuple(corrections),
                      parsing_run=replace(ir.parsing_run,
                                          parser_version=ir.parsing_run.parser_version + "+stage6-auxiliary-boundary-v1",
                                          output_fingerprint=fingerprint))
    return validate_document_ir(adapted), links

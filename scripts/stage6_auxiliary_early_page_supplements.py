"""Original-page checked source groups for five early auxiliary sample pages.

The older Stage 6 bundle predates the current OCR PDF.  This adapter fails
closed on that bundle; it does not promote a group to complete Evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import Any

import pymupdf

from turbine_kg.settings import Settings
from turbine_kg.documents.models import record_value
from turbine_kg.documents.validation import validate_document_ir
from scripts.stage6_reviewed_pdf_regions import append_pdf_regions


ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT / "data/stage6/stage6_auxiliary_early_page_supplements.json"
STRUCTURE_PATH = ROOT / "data/stage6/stage6_auxiliary_source_structure.json"
SOURCE_REVIEW_PATH = ROOT / "data/stage6/stage6_auxiliary_source_supplements.json"
DOCUMENT_KEY = "auxiliary_installation_book"
PAGES = (12, 32, 64, 120, 180)
OCR_NAME = "汽轮机辅机安装（第二版）(OCR).pdf"


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_review(path: Path = REVIEW_PATH) -> dict[str, Any]:
    review = json.loads(path.read_text(encoding="utf-8"))
    if (review.get("schema_version") != 1
            or review.get("artifact_kind") != "stage6_auxiliary_early_page_original_review"
            or review.get("document_key") != DOCUMENT_KEY
            or tuple(review.get("physical_pages", ())) != PAGES):
        raise ValueError("auxiliary early-page review scope changed")
    expected = review["expected_local_question_codes"]
    if {int(page) for page in expected} != {32, 64, 120, 180}:
        raise ValueError("auxiliary early-page question scope changed")
    for page, codes in expected.items():
        if not codes or len(codes) != len(set(codes)):
            raise ValueError(f"duplicate or missing question code on page {page}")
    return review


def validate_current_pdfs(
    review: dict[str, Any] | None = None,
    *,
    original_pdf: Path | None = None,
    ocr_pdf: Path | None = None,
) -> dict[int, str]:
    """Check exact assets and page-local OCR text against the visual review."""
    review = review or load_review()
    settings = Settings.from_environment()
    source_review = json.loads(SOURCE_REVIEW_PATH.read_text(encoding="utf-8"))
    original_pdf = original_pdf or settings.source_root / source_review["source_pdf_relative_path"]
    ocr_pdf = ocr_pdf or settings.ocr_derived_root / OCR_NAME
    for label, path, expected_sha in (
        ("original", original_pdf, review["original_pdf_sha256"]),
        ("OCR", ocr_pdf, review["current_ocr_pdf_sha256"]),
    ):
        if not path.is_file() or _sha256(path) != expected_sha:
            raise ValueError(f"{label} auxiliary PDF identity changed: {path}")
    with pymupdf.open(ocr_pdf) as document:
        page_texts = {page: document[page - 1].get_text() for page in PAGES}
    for item in review["required_current_ocr_fragments"]:
        page = int(item["physical_page"])
        if _compact(item["text"]) not in _compact(page_texts[page]):
            raise ValueError(f"current OCR fragment missing on p{page}: {item['text']}")
    for page_text, codes in review["expected_local_question_codes"].items():
        page = int(page_text)
        for code in codes:
            if page_texts[page].count(code) != 1:
                raise ValueError(f"current OCR question code missing or repeated on p{page}: {code}")
    return page_texts


def reviewed_group_specs(
    review: dict[str, Any] | None = None,
    *,
    verify_pdfs: bool = True,
) -> list[dict[str, Any]]:
    """Return corrected group boundaries; cross-page groups remain pending."""
    review = review or load_review()
    if verify_pdfs:
        validate_current_pdfs(review)
    structure = json.loads(STRUCTURE_PATH.read_text(encoding="utf-8"))
    source_review = json.loads(SOURCE_REVIEW_PATH.read_text(encoding="utf-8"))
    if structure["original_pdf_sha256"] != review["original_pdf_sha256"]:
        raise ValueError("source structure belongs to a different original PDF")
    replacements = review["question_code_replacements"]
    answers = {
        replacements.get(item["question_id"], item["question_id"]): item
        for item in source_review["marked_questions"]
        if item["physical_page"] in PAGES
    }
    answer_starts = {
        item["question_id"]: item
        for item in source_review["question_answer_groups"]
        if item["physical_page"] in PAGES
    }
    pending = set(review["pending_cross_page_group_ids"])
    groups: list[dict[str, Any]] = []
    for old in structure["groups"]:
        page = old["physical_pages"][0]
        if page not in PAGES and not (old["group_id"] == "aux-p64-Lb4A3231"):
            continue
        if page in PAGES or old["group_id"] in pending:
            item = dict(old)
            for field in ("group_id", "start_at", "end_before"):
                if field in item:
                    for old_code, new_code in replacements.items():
                        item[field] = item[field].replace(old_code, new_code)
            question_code = item.get("start_at")
            if question_code in answers:
                item["reviewed_answer_option"] = answers[question_code]["answer_option"]
                item["reviewed_answer_text"] = answers[question_code]["answer_text"]
            if question_code in answer_starts:
                item["reviewed_answer_starts"] = answer_starts[question_code]["answer_starts"]
            item["source_binding_status"] = (
                "pending_cross_page_context" if old["group_id"] in pending
                else "original_page_reviewed_pending_evidence_group_binding"
            )
            groups.append(item)
    for page_text, codes in review["expected_local_question_codes"].items():
        page = int(page_text)
        found = [group["start_at"] for group in groups
                 if group["physical_pages"] == [page] and group.get("start_at") in codes]
        if found != codes:
            raise ValueError(f"reviewed group question order differs on p{page}: {found}")
    if {group["group_id"] for group in groups if group["source_binding_status"] == "pending_cross_page_context"} != pending:
        raise ValueError("cross-page boundary group was lost")
    return groups


def audit_bundle_current_text(
    rows: list[dict[str, Any]],
    review: dict[str, Any] | None = None,
) -> list[str]:
    """Report old bundle text before any semantic group is promoted."""
    review = review or load_review()
    by_page = {
        page: "\n".join(
            row["evidence"].get("effective_text") or row["evidence"]["source_text"]
            for row in rows
            if row.get("document_key") == DOCUMENT_KEY
            and row.get("input", {}).get("physical_page") == page
        )
        for page in PAGES
    }
    missing: list[str] = []
    for item in review["required_current_ocr_fragments"]:
        page = int(item["physical_page"])
        if _compact(item["text"]) not in _compact(by_page[page]):
            missing.append(f"p{page}:{item['text']}")
    return missing


def repair_early_page_reading_order(
    ir,
    pdf,
    document_key: str,
    *,
    review: dict[str, Any] | None = None,
) -> tuple[Any, list[dict[str, Any]]]:
    """Partition one reviewed page into source-bound semantic units.

    The OCR parser's block order can put a late line after the last rule, join
    question stems to adjacent options, or join vertical navigation to body
    text.  Four reviewed PDF regions replace the only spans that themselves
    contain two independent regions.  Every remaining body span is assigned
    exactly once by its original-page y coordinate; page numbers and the
    verified outer-margin question-bank strip are excluded.
    """
    review = review or load_review()
    if document_key != DOCUMENT_KEY or len(ir.pages) != 1:
        raise ValueError("early auxiliary reading-order adapter requires one auxiliary page")
    page = ir.pages[0].display_page_number
    if page not in PAGES:
        raise ValueError(f"page {page} is outside reviewed auxiliary early pages")
    if "+current-reviewed-regions-v1" in ir.parsing_run.parser_version:
        raise ValueError("reviewed early-page regions already appended")
    windows = review["group_windows_pdf_pt"][str(page)]
    replacements = [item for item in review["replaced_existing_spans"]
                    if item["physical_page"] == page]
    matched_replacements: set[int] = set()
    original_ids = {span.source_span_id for span in ir.source_spans}
    boxes = [item for item in review["replacement_source_boxes"]
             if item["physical_page"] == page]
    adapted = ir
    if boxes:
        adapted, links = append_pdf_regions(
            ir, pdf, [{"physical_page": page, "source_boxes": [
                {"box_id": item["box_id"], "bbox_pdf_pt": item["bbox_pdf_pt"], "text": item["text"]}
                for item in boxes
            ]}],
        )
        if set(links) != {item["box_id"] for item in boxes}:
            raise ValueError("replacement source boxes were not bound")
    groups: dict[str, list] = {window[0]: [] for window in windows}
    if len(groups) != len(windows):
        raise ValueError(f"duplicate reviewed group window on p{page}")
    included: set[str] = set()
    for span in adapted.source_spans:
        if span.bbox is None or not span.quote.strip():
            continue
        if span.source_span_id in original_ids:
            discarded = False
            for index, item in enumerate(replacements):
                if (span.quote.startswith(item["text_starts"])
                        and all(abs(actual - expected) <= 0.7 for actual, expected in
                                zip(span.bbox.as_list(), item["bbox_pdf_pt"]))):
                    if index in matched_replacements:
                        raise ValueError(f"duplicate replaced SourceSpan on p{page}")
                    matched_replacements.add(index)
                    discarded = True
                    break
            if discarded:
                continue
        # The vertical exam-bank label is outside the body column.  The
        # merged p64 spans have already been replaced above.
        if page in (32, 64, 120, 180) and span.bbox.x0 >= 355:
            if span.quote.strip() not in {"选", "择", "题", "鉴", "定", "试", "库", "简", "答"}:
                raise ValueError(f"unreviewed auxiliary sidebar text on p{page}: {span.quote}")
            continue
        if span.bbox.y0 >= 535 and span.quote.strip().isdigit():
            continue
        center = (span.bbox.y0 + span.bbox.y1) / 2
        matches = [window for window in windows if window[1] <= center < window[2]]
        if len(matches) != 1:
            raise ValueError(f"auxiliary body SourceSpan has no unique reviewed window on p{page}: {span.quote}")
        groups[matches[0][0]].append(span)
        included.add(span.source_span_id)
    if matched_replacements != set(range(len(replacements))):
        raise ValueError(f"reviewed replacement SourceSpan is stale on p{page}")
    if any(span.source_span_id not in included for span in adapted.source_spans if span.source_span_id not in original_ids):
        raise ValueError(f"appended reviewed region was not assigned on p{page}")
    result: list[dict[str, Any]] = []
    pending = set(review["pending_cross_page_group_ids"])
    expected_codes = review["expected_local_question_codes"].get(str(page), [])
    for group_id, _top, _bottom, prefix in windows:
        # Two OCR spans on one printed line can differ slightly in y0 (for
        # example the La5A1005 code and its stem).  Restore left-to-right
        # order within the same five-point line band.
        members = sorted(groups[group_id], key=lambda span: (int(span.bbox.y0 / 5), span.bbox.x0, span.bbox.y0))
        quote = "\n".join(span.quote for span in members)
        if not members or not _compact(quote).startswith(_compact(prefix)):
            raise ValueError(f"reviewed auxiliary group starts incorrectly on p{page}: {group_id}")
        present_codes = [code for code in expected_codes if code in quote]
        expected_code = next((code for code in expected_codes if group_id.endswith(code)), None)
        if present_codes != ([expected_code] if expected_code else []):
            raise ValueError(f"adjacent auxiliary questions are mixed on p{page}: {group_id}")
        if group_id in pending and page not in (12, 64):
            raise ValueError("unexpected cross-page boundary status")
        entry = {
            "group_id": group_id,
            "physical_page": page,
            "source_span_ids": tuple(span.source_span_id for span in members),
            "source_text": quote,
            "source_binding_status": (
                "pending_cross_page_context" if group_id in pending else "reviewed_local_source"
            ),
        }
        if page in (32, 120) and expected_code:
            count = review[f"p{page}_question_stem_span_counts"].get(expected_code)
            if (not isinstance(count, int) or count < 1 or count >= len(members)
                    or not members[count].quote.startswith(("（A）", "(A)"))):
                raise ValueError(f"p{page} reviewed stem/options boundary changed: {group_id}")
            entry["stem_source_span_ids"] = tuple(span.source_span_id for span in members[:count])
            entry["options_source_span_ids"] = tuple(span.source_span_id for span in members[count:])
        result.append(entry)
    # Evidence requires its SourceSpans to follow the IR's reading order.
    # Reorder the reviewed body blocks accordingly; the discarded merged
    # blocks and sidebar remain in the IR for audit but trail the body.
    spans_by_id = {span.source_span_id: span for span in adapted.source_spans}
    selected_blocks = list(dict.fromkeys(
        block_id
        for group in result
        for span_id in group["source_span_ids"]
        for block_id in spans_by_id[span_id].block_version_ids
    ))
    remaining_blocks = [block.block_version_id for block in adapted.blocks
                        if block.block_version_id not in set(selected_blocks)]
    order = {block_id: index for index, block_id in
             enumerate((*selected_blocks, *remaining_blocks))}
    blocks = tuple(replace(block, reading_order=order[block.block_version_id])
                   for block in adapted.blocks)
    material = {
        "blocks": [record_value(block) for block in blocks],
        "source_spans": [record_value(span) for span in adapted.source_spans],
        "parent_output_fingerprint": adapted.parsing_run.output_fingerprint,
    }
    fingerprint = hashlib.sha256(json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    adapted = replace(
        adapted, blocks=blocks,
        parsing_run=replace(
            adapted.parsing_run,
            parser_version=adapted.parsing_run.parser_version + "+aux-early-reviewed-order-v1",
            output_fingerprint=fingerprint,
        ),
    )
    return validate_document_ir(adapted), result

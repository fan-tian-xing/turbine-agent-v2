"""Read-only, original-page reviewed supplements for the auxiliary book sample.

The returned records are proposals for the Stage 6 builder.  This module never
changes canonical Evidence, review overrides, or an audit artifact.  A caller
must revalidate the source fingerprint and resolve pending alignment before
promoting a record to canonical Evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import Any

from turbine_kg.documents.ids import block_version_id, source_span_id, stable_id, table_cell_id, table_id
from turbine_kg.documents.models import BBox, BlockVersion, DocumentIR, ManualCorrection, SourceSpan, Table, TableCell, record_value
from turbine_kg.evidence.models import EVIDENCE_ROLES, TableContext
from turbine_kg.documents.validation import validate_document_ir
from turbine_kg.evidence import build_evidence
from turbine_kg.settings import Settings
from scripts.stage6_auxiliary_early_page_supplements import load_review as load_early_page_review


ROOT = Path(__file__).resolve().parents[1]
SUPPLEMENT_PATH = ROOT / "data/stage6/stage6_auxiliary_source_supplements.json"
BUNDLE_PATH = ROOT / "data/stage6/stage6_evidence_bundle.jsonl"
DOCUMENT_KEY = "auxiliary_installation_book"
SAMPLE_PAGES = (1, 12, 31, 32, 64, 120, 180, 244, 300, 360, 468, 480)
REVIEWER = "Codex_original_pdf_page_review"
REVIEWED_AT = "2026-09-26T00:00:00+08:00"


def load_supplements(path: Path = SUPPLEMENT_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("artifact_kind") != "stage6_auxiliary_source_supplements":
        raise ValueError("unsupported auxiliary supplement manifest")
    if data.get("document_key") != DOCUMENT_KEY or tuple(data.get("sample_physical_pages", [])) != SAMPLE_PAGES:
        raise ValueError("auxiliary supplement scope changed")
    size = data["page_size_pdf_points"]
    units = [unit for page in SAMPLE_PAGES for unit in source_units_for_page(data, DOCUMENT_KEY, page)]
    unit_ids = [unit["unit_id"] for unit in units]
    if len(unit_ids) != len(set(unit_ids)) or any(not unit["source_text"].strip() for unit in units):
        raise ValueError("duplicate or blank auxiliary source unit")
    for item in [*units, *data["source_span_exclusions"], *data["ocr_corrections"], *data["formula_and_figure_regions"]]:
        _check_bbox(item["bbox"], size, item.get("unit_id") or item.get("region_id") or item.get("source_span_id"))
    reviewed_regions = data.get("reviewed_region_units", [])
    if (len(reviewed_regions) != 15 or any(item.get("physical_page") != 300
            or item.get("stage12_extractability") != "context_only"
            or not item.get("ocr_reference_text") for item in reviewed_regions)):
        raise ValueError("auxiliary p300 region review is incomplete")
    body_regions = data.get("reviewed_body_regions", [])
    if (len(body_regions) != 22
            or {item.get("physical_page") for item in body_regions} != {244, 360}
            or any(item.get("status") != "confirmed_original_page_body_region"
                   or item.get("stage12_extractability") not in {"context_only", "extractable"}
                   or item.get("source_role", "background") not in EVIDENCE_ROLES
                   or item.get("ocr_reference_text") != item.get("source_text")
                   for item in body_regions)):
        raise ValueError("auxiliary p244/p360 body-region review is incomplete")
    by_id = {item["unit_id"]: item for item in body_regions}
    groups = data.get("reviewed_extractability_groups", [])
    if len({group.get("group_key") for group in groups}) != len(groups):
        raise ValueError("duplicate reviewed extractability group")
    approved_unit_ids: set[str] = set()
    for group in groups:
        context_ids = group.get("context_unit_ids", [])
        direct_ids = group.get("extractable_unit_ids", [])
        contexts = [by_id.get(unit_id) for unit_id in context_ids]
        direct_units = [by_id.get(unit_id) for unit_id in direct_ids]
        reviewers = group.get("reviewers", [])
        if not (
            group.get("status") == "complete_original_page_review"
            and group.get("document_key") == data["document_key"]
            and group.get("user_decision") == "approved_for_extraction"
            and group.get("authority_kind")
            and group.get("source_group_kind")
            and group.get("decision_reason") and group.get("next_source_boundary")
            and isinstance(reviewers, list) and len(reviewers) >= 3
            and len(set(reviewers)) == len(reviewers)
            and context_ids and len(context_ids) == len(set(context_ids))
            and direct_ids and len(direct_ids) == len(set(direct_ids))
            and not (set(context_ids) & set(direct_ids))
            and all(unit and unit.get("stage12_extractability") == "context_only" for unit in contexts)
            and all(unit and unit.get("stage12_extractability") == "extractable"
                    and unit.get("source_role", "background") != "background"
                    and unit.get("extractability_reason") for unit in direct_units)
            and {unit["physical_page"] for unit in [*contexts, *direct_units]}
                <= set(group.get("original_pages_checked", []))
        ):
            raise ValueError("incomplete reviewed source extractability group")
        approved_unit_ids.update(direct_ids)
    if {item["unit_id"] for item in body_regions if item["stage12_extractability"] == "extractable"} != approved_unit_ids:
        raise ValueError("extractable reviewed region lacks a complete source group")
    if any(item.get("answer_option") is not None for item in data["unmarked_questions"]):
        raise ValueError("unmarked sample question has an asserted answer")
    _check_table_groups(data)
    return data


def load_bundle(path: Path = BUNDLE_PATH) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _compact(value: str) -> str:
    """Normalize OCR typography for matching, never for stored quotations."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).replace("～", "~")


def _page_rows(rows: list[dict[str, Any]], page: int) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if row.get("document_key") == DOCUMENT_KEY
        and int(row.get("input", {}).get("physical_page", -1)) == page
    ]


def _question_window(rows: list[dict[str, Any]], question: dict[str, Any], next_id: str | None) -> tuple[str, list[dict[str, Any]]]:
    """Return bounded OCR fragments and exact slices of overlapping Evidence."""
    pieces = [str(row["evidence"].get("effective_text") or row["evidence"].get("source_text") or "") for row in rows]
    offsets: list[tuple[int, int]] = []
    position = 0
    for piece in pieces:
        offsets.append((position, position + len(piece)))
        position += len(piece) + 1
    joined = "\n".join(pieces)
    start = joined.find(question["question_id"])
    if start < 0:
        raise ValueError(f"question code absent from canonical Evidence: {question['question_id']}")
    end = joined.find(next_id, start + len(question["question_id"])) if next_id else -1
    if next_id and end < 0:
        raise ValueError(f"following question code absent from canonical Evidence: {next_id}")
    if end < 0:
        end = len(joined)
    members = []
    for row, piece, (left, right) in zip(rows, pieces, offsets):
        if left >= end or right <= start:
            continue
        slice_start = max(start - left, 0)
        slice_end = min(end - left, len(piece))
        members.append({
            "evidence_id": row["evidence"]["evidence_id"],
            "source_span_ids_context_only": row["evidence"].get("source_span_ids", []),
            "source_quote": piece[slice_start:slice_end],
            "partial_member": slice_start > 0 or slice_end < len(piece),
        })
    return joined[start:end], members


def _answer_is_marked(window: str, option: str, answer_text: str) -> bool:
    text = _compact(window)
    value = _compact(answer_text)
    # The answer letter in the stem and the corresponding labelled option
    # must both be present.  This avoids treating a different option's text
    # or an adjacent question as the marked answer.
    option_a = [match.start() for match in re.finditer(r"\(A\)", text)]
    stem_end = (option_a[1] if len(option_a) >= 2 else len(text)) if option == "A" else (option_a[0] if option_a else len(text))
    stem = text[:stem_end]
    return f"({option})" in stem and f"({option}){value}" in text


def _reviewed_p120_question_window(
    rows: list[dict[str, Any]], question: dict[str, Any], current_code: str,
) -> tuple[str, list[dict[str, Any]], bool]:
    """Bind the printed mark and its option to separate current Evidence.

    The original review manifest predates the corrected ``Jf`` code in four
    questions.  The current canonical page has one reviewed stem and one
    reviewed options row per question; free-text window search would permit
    accidental pairing with a neighbouring question.
    """
    group_id = f"aux-p120-{current_code}"
    group_rows = [row for row in rows if row.get("source_supplement_group_id") == group_id]
    by_part = {part: [row for row in group_rows if row.get("source_supplement_part") == part]
               for part in ("stem", "options")}
    if len(group_rows) != 2 or any(len(part_rows) != 1 for part_rows in by_part.values()):
        raise ValueError(f"current p120 stem/options Evidence missing or duplicated: {current_code}")
    stem, options = by_part["stem"][0], by_part["options"][0]
    stem_evidence, options_evidence = stem["evidence"], options["evidence"]
    for evidence in (stem_evidence, options_evidence):
        if (not evidence.get("source_span_ids") or not evidence.get("locations")
                or any(location.get("physical_page") != 120
                       or location.get("original_asset_id") != evidence.get("authority_asset_id")
                       or not location.get("bbox") for location in evidence["locations"])):
            raise ValueError(f"current p120 original-page SourceSpan binding changed: {current_code}")
    if (stem_evidence["evidence_id"] == options_evidence["evidence_id"]
            or stem_evidence["authority_asset_id"] != options_evidence["authority_asset_id"]
            or stem_evidence["revision_id"] != options_evidence["revision_id"]):
        raise ValueError(f"current p120 stem/options source identity changed: {current_code}")
    stem_text = str(stem_evidence.get("effective_text") or stem_evidence["source_text"])
    options_text = str(options_evidence.get("effective_text") or options_evidence["source_text"])
    option = question["answer_option"]
    answer = _compact(question["answer_text"])
    normalized_stem, normalized_options = _compact(stem_text), _compact(options_text)
    marked = (
        current_code in stem_text
        and f"({option})" in normalized_stem
        and f"({option}){answer}" in normalized_options
        and "鉴定试题库" not in stem_text + options_text
    )
    members = [
        {
            "evidence_id": evidence["evidence_id"],
            "source_span_ids_context_only": evidence["source_span_ids"],
            "source_quote": text,
            "partial_member": False,
            "source_part": part,
        }
        for part, evidence, text in (
            ("stem", stem_evidence, stem_text),
            ("options", options_evidence, options_text),
        )
    ]
    return stem_text + "\n" + options_text, members, marked


def source_units_for_page(data: dict[str, Any], document_key: str, physical_page: int) -> list[dict[str, Any]]:
    """Return only visually reviewed, nonempty printed text on one sample page.

    Page 300 contributes only six visually verified, linearized equation lines
    and the printed figure caption/legend. Diagram geometry stays visual-only.
    Table cells carry their own row/column and PDF-point box.
    """
    if document_key != DOCUMENT_KEY or physical_page not in SAMPLE_PAGES:
        raise ValueError("page is outside the auxiliary Stage 6 sample")
    units = [
        {**row, "content_kind": row.get("content_kind", "heading" if row["unit_id"].endswith("heading") else "paragraph"), "text_origin": "manual_correction"}
        for row in data.get("supplemental_prose", [])
        if row["physical_page"] == physical_page and row["status"] == "confirmed_original_page"
    ]
    units.extend(
        {**row, "text_origin": "manual_correction"}
        for row in data.get("reviewed_region_units", [])
        if row["physical_page"] == physical_page and row["status"].startswith("confirmed_original_page")
    )
    units.extend(
        {**row, "text_origin": "manual_correction"}
        for row in data.get("reviewed_body_regions", [])
        if row["physical_page"] == physical_page and row["status"] == "confirmed_original_page_body_region"
    )
    table = data.get("table_cells", {})
    if table.get("physical_page") == physical_page:
        units.extend(
            {**row, "text_origin": "manual_correction"}
            for row in data["table_header_units"]
        )
        x_grid, y_grid = table["x_grid"], table["y_grid"]
        for row_index, row in enumerate(table["rows"]):
            for column_index, value in enumerate(row):
                if not value or not str(value).strip():
                    continue
                units.append({
                    "unit_id": f"{table['table_id']}-r{row_index}-c{column_index}",
                    "physical_page": physical_page,
                    "bbox": [x_grid[column_index], y_grid[row_index], x_grid[column_index + 1], y_grid[row_index + 1]],
                    "source_text": value,
                    "content_kind": "table",
                    "text_origin": "manual_correction",
                    "table_key": table["table_id"],
                    "row_index": row_index + 2,
                    "column_index": column_index,
                    "status": "confirmed_original_page",
                })
    return sorted(units, key=lambda unit: (unit["bbox"][1], unit["bbox"][0]))


def _check_table_groups(data: dict[str, Any]) -> None:
    """Keep the five printed data rows distinct from the uncited header."""
    table = data["table_cells"]
    columns = len(table["column_labels"])
    if columns != 5 or len(table["printed_header"]) != 7:
        raise ValueError("auxiliary table header structure changed")
    if len(table["x_grid"]) != columns + 1 or len(table["y_grid"]) != len(table["rows"]) + 1:
        raise ValueError("auxiliary table grid differs from reviewed rows")
    headers = data.get("table_header_units", [])
    if (len(headers) != 7 or [item["source_text"] for item in headers]
            != [item["text"] for item in table["printed_header"]]
            or [(item["row_index"], item["column_index"]) for item in headers]
            != [(0, 0), (0, 1), (0, 3), (1, 1), (1, 2), (1, 3), (1, 4)]):
        raise ValueError("auxiliary two-tier table header differs from original")
    groups = data["evidence_groups"]
    if len(groups) != len(table["rows"]):
        raise ValueError("auxiliary table data-row group count changed")
    for row_index, (row, group) in enumerate(zip(table["rows"], groups)):
        expected_ids = [f"{table['table_id']}-r{row_index}-c{column_index}"
                        for column_index in range(columns)]
        if (len(row) != columns or any(not value for value in row)
                or group["physical_page"] != table["physical_page"]
                or group["row_index"] != row_index + 2
                or group["source_unit_ids"] != expected_ids):
            raise ValueError("auxiliary table group does not match printed data row")


def evidence_groups_for_page(data: dict[str, Any], document_key: str, physical_page: int) -> list[dict[str, Any]]:
    """Return row-level p480 groups with the reviewed two-tier header."""
    if document_key != DOCUMENT_KEY or physical_page not in SAMPLE_PAGES:
        raise ValueError("page is outside the auxiliary Stage 6 sample")
    _check_table_groups(data)
    table = data["table_cells"]
    if physical_page != table["physical_page"]:
        return []
    return [{
        **group,
        "table_key": table["table_id"],
        "table_caption": table["caption"],
        "printed_header": table["printed_header"],
        "column_labels": table["column_labels"],
        "table_context_status": "source_bound_two_tier_header",
    } for group in data["evidence_groups"]]


def build_reviewed_group_evidence(
    ir: DocumentIR,
    group: dict[str, Any],
    links: dict[str, dict[str, str]],
    *,
    manifest: dict[str, Any] | None = None,
):
    """Build one source-bound Evidence with row, column and header cells."""
    data = load_supplements() if manifest is None else manifest
    if len(ir.pages) != 1 or ir.pages[0].display_page_number != 480:
        raise ValueError("auxiliary table group requires the reviewed physical page 480")
    groups = evidence_groups_for_page(data, DOCUMENT_KEY, 480)
    selected_group = next((item for item in groups if item["group_id"] == group.get("group_id")), None)
    if selected_group is None or group != selected_group:
        raise ValueError("auxiliary table group is not the reviewed manifest group")
    table = data["table_cells"]
    unit_ids = selected_group["source_unit_ids"]
    if any(unit_id not in links or not {"source_span_id", "correction_id", "table_cell_id"} <= links[unit_id].keys()
           for unit_id in unit_ids):
        raise ValueError("auxiliary table group has missing reviewed source links")
    table_ids = {links[unit_id].get("table_id") for unit_id in unit_ids}
    if len(table_ids) != 1 or None in table_ids:
        raise ValueError("auxiliary table group spans multiple Document IR tables")
    table_id_value = next(iter(table_ids))
    if table_id_value not in {table.table_id for table in ir.tables}:
        raise ValueError("auxiliary table group table is absent from Document IR")
    expected_units = {unit["unit_id"]: unit for unit in source_units_for_page(data, DOCUMENT_KEY, 480)}
    spans_by_id = {span.source_span_id: span for span in ir.source_spans}
    cells_by_id = {cell.cell_id: cell for cell in ir.table_cells}
    for column_index, unit_id in enumerate(unit_ids):
        unit, link = expected_units[unit_id], links[unit_id]
        span = spans_by_id.get(link["source_span_id"])
        cell = cells_by_id.get(link["table_cell_id"])
        if (span is None or cell is None or span.quote != unit["source_text"]
                or span.table_id != table_id_value or span.row_index != group["row_index"]
                or span.column_index != column_index or cell.table_id != table_id_value
                or cell.row_index != group["row_index"] or cell.column_index != column_index):
            raise ValueError("auxiliary table group source link differs from printed cell")
    header_unit_ids = [unit["unit_id"] for unit in data["table_header_units"]]
    if any(unit_id not in links or "table_cell_id" not in links[unit_id] for unit_id in header_unit_ids):
        raise ValueError("auxiliary table group lacks reviewed two-tier header cells")
    table_context = (TableContext(
        table_id=table_id_value,
        row_indices=(group["row_index"],),
        column_indices=tuple(range(len(table["column_labels"]))),
        header_cell_ids=tuple(links[unit_id]["table_cell_id"] for unit_id in header_unit_ids),
        value_cell_ids=tuple(links[unit_id]["table_cell_id"] for unit_id in unit_ids),
        table_label=table["caption"],
        review_scope="cell", leaf_column_count=len(table["column_labels"]),
        header_hierarchy=tuple(item["source_text"] for item in data["table_header_units"]),
        partition_note=(
            "Original row prints 1题×5分/题 plus 2题×10分/题 next to 配分15; preserve all printed values."
            if group["row_index"] == 5 else "Five-column data row under the original two-tier header."
        ),
    ),)
    return build_evidence(
        ir, tuple(links[unit_id]["source_span_id"] for unit_id in unit_ids),
        evidence_role="requirement_source", disposition="region_scoped",
        review_status="accepted", reviewer=REVIEWER, reviewed_at=REVIEWED_AT,
        review_reason="Original PDF data row and two-tier column header verified in separate local SourceSpans.",
        effective_text="\n".join(expected_units[unit_id]["source_text"] for unit_id in unit_ids),
        effective_text_origin="manual_correction",
        correction_ids=tuple(links[unit_id]["correction_id"] for unit_id in unit_ids),
        table_context=table_context,
    )


def build_reviewed_header_evidence(ir: DocumentIR, links: dict[str, dict[str, str]],
                                   *, manifest: dict[str, Any] | None = None):
    """Cite the seven printed p480 header cells before interpreting data rows."""
    data = load_supplements() if manifest is None else manifest
    header_ids = [unit["unit_id"] for unit in data["table_header_units"]]
    if len(ir.pages) != 1 or ir.pages[0].display_page_number != 480 or any(
            unit_id not in links or "table_cell_id" not in links[unit_id] for unit_id in header_ids):
        raise ValueError("reviewed auxiliary table header SourceSpans are missing")
    table_ids = {links[unit_id]["table_id"] for unit_id in header_ids}
    if len(table_ids) != 1:
        raise ValueError("reviewed auxiliary header spans refer to different tables")
    table = data["table_cells"]
    return build_evidence(
        ir, tuple(links[unit_id]["source_span_id"] for unit_id in header_ids),
        evidence_role="background", disposition="region_scoped",
        review_status="accepted", reviewer=REVIEWER, reviewed_at="2026-09-29T00:00:00+08:00",
        review_reason="Seven visible cells in the original PDF two-tier table header were checked by region.",
        effective_text="\n".join(unit["source_text"] for unit in data["table_header_units"]),
        effective_text_origin="manual_correction",
        correction_ids=tuple(links[unit_id]["correction_id"] for unit_id in header_ids),
        table_context=(TableContext(
            table_id=next(iter(table_ids)), row_indices=(), column_indices=(),
            header_cell_ids=tuple(links[unit_id]["table_cell_id"] for unit_id in header_ids),
            table_label=table["caption"], review_scope="table_region",
            leaf_column_count=len(table["column_labels"]),
            header_hierarchy=tuple(unit["source_text"] for unit in data["table_header_units"]),
            partition_note="Original two-tier header; no data value is claimed by this Evidence.",
        ),),
    )


def _check_bbox(value: list[float], size: list[float], label: str) -> None:
    if len(value) != 4 or not (0 <= value[0] < value[2] <= size[0] and 0 <= value[1] < value[3] <= size[1]):
        raise ValueError(f"invalid original-page PDF-point bbox: {label}")


def append_reviewed_source_spans(
    ir: DocumentIR,
    document_key: str,
    *,
    manifest: dict[str, Any] | None = None,
) -> tuple[DocumentIR, dict[str, dict[str, str]]]:
    """Append confirmed p300 text regions and p480 prose/table cells.

    Diagram geometry, cross-page fragments, sidebar text and unmarked p468
    answer slots are absent. IDs preserve manual-correction lineage.
    """
    validate_document_ir(ir)
    if document_key != DOCUMENT_KEY or len(ir.pages) != 1:
        raise ValueError("auxiliary adapter requires one page of the reviewed book")
    data = load_supplements() if manifest is None else manifest
    source_pdf = Settings.from_environment().source_root / data["source_pdf_relative_path"]
    if not source_pdf.is_file() or hashlib.sha256(source_pdf.read_bytes()).hexdigest() != data["source_pdf_sha256"]:
        raise ValueError("reviewed auxiliary original PDF fingerprint changed")
    page = ir.pages[0]
    if abs(page.width_pt - data["page_size_pdf_points"][0]) > 0.5 or abs(page.height_pt - data["page_size_pdf_points"][1]) > 0.5:
        raise ValueError("DocumentIR page geometry differs from reviewed original")
    if "+stage6-original-auxiliary-supplements-v1" in ir.parsing_run.parser_version:
        raise ValueError("auxiliary source supplements already appended")
    units = source_units_for_page(data, DOCUMENT_KEY, page.display_page_number)
    if not units:
        return ir, {}
    blocks = list(ir.blocks)
    spans = list(ir.source_spans)
    tables = list(ir.tables)
    cells = list(ir.table_cells)
    corrections = list(ir.manual_corrections)
    next_ordinal = max((block.block_ordinal for block in blocks), default=-1) + 1
    next_order = max((block.reading_order for block in blocks), default=-1) + 1
    result: dict[str, dict[str, str]] = {}
    table = data["table_cells"]
    table_units = [unit for unit in units if unit["content_kind"] == "table"]
    table_bid = None
    tid = None
    if table_units:
        table_bid = block_version_id(ir.parsing_run.parsing_run_id, page.page_id, next_ordinal)
        blocks.append(BlockVersion(table_bid, page.page_id, ir.parsing_run.parsing_run_id,
                                   next_ordinal, "table", "", BBox(*table["bbox"]), next_order,
                                   text_origin="manual_correction"))
        tid = table_id(table_bid)
        next_ordinal += 1
        next_order += 1
    for unit in units:
        bid = block_version_id(ir.parsing_run.parsing_run_id, page.page_id, next_ordinal)
        text_value = unit["source_text"]
        kind = unit["content_kind"]
        blocks.append(BlockVersion(bid, page.page_id, ir.parsing_run.parsing_run_id,
                                   next_ordinal, "table" if kind == "table" else kind,
                                   text_value, BBox(*unit["bbox"]), next_order,
                                   parent_block_version_id=table_bid if kind == "table" else None,
                                   text_origin="manual_correction"))
        sid = source_span_id(bid, 0, text_value)
        spans.append(SourceSpan(sid, page.page_id, (bid,), text_value, kind,
                                0, len(text_value), BBox(*unit["bbox"]), "manual_correction",
                                tid if kind == "table" else None,
                                unit.get("row_index"), unit.get("column_index")))
        correction_id = stable_id("correction", "stage6-auxiliary-supplement", unit["unit_id"], text_value)
        reason = ("Original PDF visual formula linearization; fraction geometry remains on the page"
                  if page.display_page_number == 300 and unit["unit_id"].startswith("aux-p300-")
                  else "Original PDF visual transcription of printed text")
        corrections.append(ManualCorrection(correction_id, bid, text_value, text_value,
                                            reason, "Codex_original_pdf_page_review",
                                            "2026-09-29" if page.display_page_number in {244, 300, 360, 480} else REVIEWED_AT))
        item = {"source_span_id": sid, "block_version_id": bid, "correction_id": correction_id}
        if kind == "table":
            assert tid is not None
            cid = table_cell_id(tid, unit["row_index"], unit["column_index"])
            cells.append(TableCell(cid, tid, bid, unit["row_index"], unit["column_index"]))
            item.update(table_id=tid, table_cell_id=cid)
        result[unit["unit_id"]] = item
        next_ordinal += 1
        next_order += 1
    if tid is not None:
        assert table_bid is not None
        tables.append(Table(tid, table_bid, None, len(table["rows"]) + 2, len(table["column_labels"]),
                            tuple(cell.cell_id for cell in cells if cell.table_id == tid)))
    material = {
        "blocks": [record_value(item) for item in blocks],
        "source_spans": [record_value(item) for item in spans],
        "tables": [record_value(item) for item in tables],
        "table_cells": [record_value(item) for item in cells],
        "manual_corrections": [record_value(item) for item in corrections],
    }
    fingerprint = hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
    adapted = replace(ir, blocks=tuple(blocks), source_spans=tuple(spans),
                      tables=tuple(tables), table_cells=tuple(cells), manual_corrections=tuple(corrections),
                      parsing_run=replace(ir.parsing_run,
                                          parser_version=ir.parsing_run.parser_version + "+stage6-original-auxiliary-supplements-v1",
                                          output_fingerprint=fingerprint))
    return validate_document_ir(adapted), result


def audit_supplements(
    data: dict[str, Any] | None = None,
    rows: list[dict[str, Any]] | None = None,
    *,
    source_pdf: Path | None = None,
) -> dict[str, Any]:
    """Check source identity and current Evidence alignment without writes."""
    data = data if data is not None else load_supplements()
    rows = rows if rows is not None else load_bundle()
    source_pdf = source_pdf or Settings.from_environment().source_root / data["source_pdf_relative_path"]
    errors: list[str] = []
    if not source_pdf.is_file() or hashlib.sha256(source_pdf.read_bytes()).hexdigest() != data.get("source_pdf_sha256"):
        errors.append("original_pdf_changed_or_missing")
    if data.get("document_key") != DOCUMENT_KEY or tuple(data.get("sample_physical_pages", [])) != SAMPLE_PAGES:
        errors.append("sample_scope_changed")
    if any(item.get("answer_option") for item in data.get("unmarked_questions", [])):
        errors.append("unmarked_question_has_answer")
    question_ids = [item["question_id"] for item in data.get("marked_questions", [])]
    if len(question_ids) != len(set(question_ids)):
        errors.append("duplicate_question_id")
    early_review = load_early_page_review()
    replacements = early_review["question_code_replacements"]
    current_p120_codes = [replacements.get(item["question_id"], item["question_id"])
                          for item in data.get("marked_questions", []) if item["physical_page"] == 120]
    if (early_review["original_pdf_sha256"] != data.get("source_pdf_sha256")
            or current_p120_codes != early_review["expected_local_question_codes"]["120"]):
        errors.append("p120_original_page_question_review_changed")
    question_results = []
    by_page: dict[int, list[dict[str, Any]]] = {}
    for page in SAMPLE_PAGES:
        by_page[page] = _page_rows(rows, page)
    current_spans = {
        span for row in rows if row.get("document_key") == DOCUMENT_KEY
        for span in row.get("evidence", {}).get("source_span_ids", [])
    }
    pending_sidebar = any(
        item["source_span_id"] in current_spans for item in data.get("source_span_exclusions", [])
    )
    for index, question in enumerate(data.get("marked_questions", [])):
        page = int(question["physical_page"])
        same_page_next = next(
            (item["question_id"] for item in data["marked_questions"][index + 1:] if item["physical_page"] == page),
            question.get("next_question_id"),
        )
        try:
            current_code = replacements.get(question["question_id"], question["question_id"])
            if page == 120:
                window, members, marked = _reviewed_p120_question_window(
                    by_page[page], question, current_code,
                )
            else:
                window, members = _question_window(by_page[page], question, same_page_next)
                marked = _answer_is_marked(window, question["answer_option"], question["answer_text"])
            if not marked:
                errors.append(f"answer_not_currently_bound:{question['question_id']}")
            needs_split = any(member["partial_member"] for member in members)
            question_results.append({
                "question_id": question["question_id"],
                "current_question_id": current_code,
                "physical_page": page,
                "answer_original_page_confirmed": question["review_status"] == "confirmed_original_page",
                "current_evidence_answer_bound": marked,
                "evidence_support_slices": members,
                "integration_status": "pending_canonical_rebuild" if page == 64 and pending_sidebar else (
                    "pending_span_split" if needs_split else "ready_for_group_integration"
                ),
            })
        except (KeyError, ValueError) as error:
            errors.append(str(error))
    return {
        "status": "current" if not errors else "needs_reconciliation",
        "errors": errors,
        "question_results": question_results,
        "confirmed_question_count": sum(row["answer_original_page_confirmed"] and row["current_evidence_answer_bound"] for row in question_results),
        "sidebar_exclusion_pending": pending_sidebar,
        "pending_fragment_count": len(data.get("cross_page_fragments", [])),
        "pending_formula_figure_count": sum(
            item["status"].startswith("pending") or "pending" in item["status"]
            for item in data.get("formula_and_figure_regions", [])
        ),
    }


def build_stage6_auxiliary_supplements(
    rows: list[dict[str, Any]] | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Integration interface: proposals plus a fresh, read-only alignment audit."""
    data = data if data is not None else load_supplements()
    rows = rows if rows is not None else load_bundle()
    audit = audit_supplements(data, rows)
    return {
        "document_key": DOCUMENT_KEY,
        "source_pdf_sha256": data["source_pdf_sha256"],
        "sample_physical_pages": data["sample_physical_pages"],
        "source_span_exclusions": data["source_span_exclusions"],
        "ocr_corrections": data["ocr_corrections"],
        "marked_questions": data["marked_questions"],
        "question_bindings": audit["question_results"],
        "question_answer_groups": data["question_answer_groups"],
        "classification_lists": data["classification_lists"],
        "same_page_fragments": data["same_page_fragments"],
        "cross_page_fragments": data["cross_page_fragments"],
        "formula_and_figure_regions": data["formula_and_figure_regions"],
        "supplemental_prose": data["supplemental_prose"],
        "table_cells": data["table_cells"],
        "evidence_groups": evidence_groups_for_page(data, DOCUMENT_KEY, 480),
        "unmarked_questions": data["unmarked_questions"],
        "audit": audit,
    }


if __name__ == "__main__":
    print(json.dumps(audit_supplements(), ensure_ascii=False, indent=2))

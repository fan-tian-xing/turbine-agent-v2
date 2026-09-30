"""Reviewed, original-page source supplements for two Stage 6 standards.

This module is deliberately separate from the canonical Stage 6 builders.  It
records printed text and form structure, then offers an adapter that appends
reviewed SourceSpans to a one-page DocumentIR.  Empty form cells never become
SourceSpans or Evidence.  Image regions become Figure blocks, without turning
an interpretation of a drawing into fictitious PDF text.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from turbine_kg.documents.ids import (
    block_version_id,
    figure_id,
    source_span_id,
    stable_id,
    table_cell_id,
    table_id,
)
from turbine_kg.documents.models import (
    BBox,
    BlockVersion,
    DocumentIR,
    Figure,
    ManualCorrection,
    SourceSpan,
    Table,
    TableCell,
    record_value,
)
from turbine_kg.documents.validation import validate_document_ir
from turbine_kg.evidence import build_evidence
from turbine_kg.evidence.models import TableContext


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "data/stage6/stage6_standard_source_supplements.json"
REVIEWER = "Codex_original_pdf_page_review"
REVIEWED_AT = "2026-09-26T00:00:00+08:00"
PDF_SHA256 = {
    "DL5190.3": "36d1156e1cce17a3302f65912a7aadff5572451203dc03b320c875aa79c58813",
    "DLT863": "f70d8165dea9b6ba705ef9631fd5abee94f464cd6a393519e592ab4337e55fdb",
}
PAGE_SIZE = {"DL5190.3": (595.22, 842.0), "DLT863": (595.32, 841.92)}


def load_supplements(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    """Load and validate the standalone, reviewed source manifest."""

    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or data.get("artifact_kind") != "stage6_standard_source_supplements":
        raise ValueError("unsupported standard supplement manifest")
    if data.get("original_pdf_sha256") != PDF_SHA256:
        raise ValueError("supplement original-PDF fingerprints differ from reviewed originals")
    tables = {row["table_key"]: row for row in data["tables"]}
    if len(tables) != len(data["tables"]):
        raise ValueError("duplicate supplement table keys")
    unit_ids: set[str] = set()
    for unit in data["source_units"]:
        uid = unit["unit_id"]
        if uid in unit_ids:
            raise ValueError(f"duplicate supplement unit: {uid}")
        unit_ids.add(uid)
        document_key = unit["document_key"]
        if document_key not in PAGE_SIZE or not isinstance(unit["physical_page"], int) or unit["physical_page"] < 1:
            raise ValueError(f"invalid source page for {uid}")
        if not unit["source_text"].strip() or unit["evidence_role"] != "requirement_source":
            raise ValueError(f"source unit lacks visible text or has an observation role: {uid}")
        if document_key == "DLT863" and unit.get("semantic_use") != "form_template_context_only":
            raise ValueError(f"scanned form unit must stay context-only: {uid}")
        _check_bbox(unit["bbox"], PAGE_SIZE[document_key], uid)
        if unit.get("table_key"):
            table = tables[unit["table_key"]]
            if (table["document_key"], table["physical_page"]) != (document_key, unit["physical_page"]):
                raise ValueError(f"table page mismatch: {uid}")
            if not (0 <= unit["row_index"] < table["row_count"] and 0 <= unit["column_index"] < table["column_count"]):
                raise ValueError(f"table coordinate outside grid: {uid}")
    for form in data["form_schema"]:
        if form.get("value", "not-null") is not None or form.get("is_observation") is not False:
            raise ValueError(f"blank form field was promoted to an observation: {form.get('field_id')}")
        if form["document_key"] not in PAGE_SIZE:
            raise ValueError("unknown form source document")
        if form.get("bbox") is not None:
            _check_bbox(form["bbox"], PAGE_SIZE[form["document_key"]], form["field_id"])
    blank_cells = blank_cell_coordinates(data)
    printed_cells = {(u["table_key"], u["row_index"], u["column_index"])
                     for u in data["source_units"] if u.get("table_key")}
    if blank_cells & printed_cells:
        raise ValueError("blank-cell rule overlaps printed source text")
    for table in data["tables"]:
        if table["document_key"] != "DLT863":
            continue
        covered = {(m["row_index"]+dr, m["column_index"]+dc)
                   for m in table.get("merged_cells", [])
                   for dr in range(m["row_span"]) for dc in range(m["column_span"])
                   if dr or dc}
        expected = {(table["table_key"], row, col)
                    for row in range(1, table["row_count"])
                    for col in range(table["column_count"])
                    if (row, col) not in covered}
        if expected != ((printed_cells | blank_cells) & expected):
            raise ValueError(f"form data grid has unexplained cells: {table['table_key']}")
    region_ids: set[str] = set()
    for region in data["visual_regions"]:
        if region["region_id"] in region_ids:
            raise ValueError("duplicate visual region")
        region_ids.add(region["region_id"])
        _check_bbox(region["bbox"], PAGE_SIZE[region["document_key"]], region["region_id"])
        if region["review_basis"] != "original_pdf_visual_review":
            raise ValueError("image interpretation lacks original-page review")
        if region.get("cross_page_caption") and region["cross_page_caption"]["physical_page"] == region["physical_page"]:
            raise ValueError("cross-page caption points to the image page")
    units_by_id = {unit["unit_id"]: unit for unit in data["source_units"]}
    group_ids: set[str] = set()
    assigned: set[str] = set()
    for group in data["evidence_groups"]:
        gid = group["group_id"]
        if gid in group_ids:
            raise ValueError(f"duplicate supplement Evidence group: {gid}")
        group_ids.add(gid)
        members = group["source_unit_ids"]
        if not members or len(members) != len(set(members)):
            raise ValueError(f"empty or repeated group members: {gid}")
        if any(uid not in units_by_id for uid in members):
            raise ValueError(f"Evidence group references an unknown source unit: {gid}")
        if assigned.intersection(members):
            raise ValueError(f"source unit belongs to multiple Evidence groups: {gid}")
        assigned.update(members)
        if any((units_by_id[uid]["document_key"], units_by_id[uid]["physical_page"],
                units_by_id[uid]["semantic_use"])
               != (group["document_key"], group["physical_page"], group["semantic_use"])
               for uid in members):
            raise ValueError(f"Evidence group mixes pages or semantic uses: {gid}")
        table_keys = {units_by_id[uid].get("table_key") for uid in members}
        if len(table_keys) != 1:
            raise ValueError(f"Evidence group mixes table and non-table sources: {gid}")
    if assigned != unit_ids:
        raise ValueError(f"Evidence groups omit source units: {sorted(unit_ids - assigned)}")
    return data


def _check_bbox(box: list[float], size: tuple[float, float], label: str) -> None:
    if len(box) != 4 or not (0 <= box[0] < box[2] <= size[0] + 0.2 and 0 <= box[1] < box[3] <= size[1] + 0.2):
        raise ValueError(f"invalid original-PDF bbox: {label}")


def source_units_for_page(data: dict[str, Any], document_key: str, physical_page: int) -> list[dict[str, Any]]:
    return [row for row in data["source_units"] if (row["document_key"], row["physical_page"]) == (document_key, physical_page)]


def evidence_groups_for_page(data: dict[str, Any], document_key: str, physical_page: int) -> list[dict[str, Any]]:
    return [row for row in data["evidence_groups"]
            if (row["document_key"], row["physical_page"]) == (document_key, physical_page)]


def form_schema_for_page(data: dict[str, Any], document_key: str, physical_page: int) -> list[dict[str, Any]]:
    return [row for row in data["form_schema"] if (row["document_key"], row["physical_page"]) == (document_key, physical_page)]


def blank_cell_coordinates(data: dict[str, Any], document_key: str | None = None,
                           physical_page: int | None = None) -> set[tuple[str, int, int]]:
    """Expand compact null rules only in memory; the manifest stores no blank-cell rows."""

    tables = {row["table_key"]: row for row in data["tables"]}
    result: set[tuple[str, int, int]] = set()
    for rule in data["blank_cell_rules"]:
        table = tables[rule["table_key"]]
        if document_key is not None and table["document_key"] != document_key:
            continue
        if physical_page is not None and table["physical_page"] != physical_page:
            continue
        if rule.get("value", "not-null") is not None or rule.get("is_observation") is not False:
            raise ValueError("blank-cell rule cannot contain an observation")
        rows = rule.get("rows", list(range(rule["row_range"][0], rule["row_range"][1] + 1))
                   if "row_range" in rule else [])
        for row in rows:
            for col in rule["columns"]:
                if not (1 <= row < table["row_count"] and 0 <= col < table["column_count"]):
                    raise ValueError("blank-cell rule outside printed table grid")
                value = (rule["table_key"], row, col)
                if value in result:
                    raise ValueError("overlapping blank-cell rules")
                result.add(value)
    return result


def append_reviewed_source_spans(
    ir: DocumentIR,
    document_key: str,
    *,
    manifest: dict[str, Any] | None = None,
) -> tuple[DocumentIR, dict[str, dict[str, str]]]:
    """Append reviewed text cells and image blocks to one page of DocumentIR.

    Return an index of source unit -> SourceSpan/correction/table IDs.  For a
    manual transcription, build_evidence must receive the returned correction
    ID, `effective_text_origin="manual_correction"`, and the same visible text.
    The caller decides Evidence disposition; source text has no Observation role.
    """

    validate_document_ir(ir)
    if len(ir.pages) != 1 or document_key not in PAGE_SIZE:
        raise ValueError("standard supplement adapter requires one known source page")
    manifest = load_supplements() if manifest is None else manifest
    page = ir.pages[0]
    number = page.display_page_number
    authority_assets = [asset for asset in ir.assets if asset.asset_kind == "original"]
    if len(authority_assets) != 1 or authority_assets[0].sha256 != PDF_SHA256[document_key]:
        raise ValueError("DocumentIR authority asset differs from reviewed original PDF")
    if abs(page.width_pt - PAGE_SIZE[document_key][0]) > 0.5 or abs(page.height_pt - PAGE_SIZE[document_key][1]) > 0.5:
        raise ValueError("DocumentIR page geometry differs from original PDF")
    units = source_units_for_page(manifest, document_key, number)
    regions = [r for r in manifest["visual_regions"] if (r["document_key"], r["physical_page"]) == (document_key, number)]
    definitions = [t for t in manifest["tables"] if (t["document_key"], t["physical_page"]) == (document_key, number)]
    blocks = list(ir.blocks)
    spans = list(ir.source_spans)
    tables = list(ir.tables)
    cells = list(ir.table_cells)
    figures = list(ir.figures)
    corrections = list(ir.manual_corrections)
    next_ordinal = max((b.block_ordinal for b in blocks), default=-1) + 1
    next_order = max((b.reading_order for b in blocks), default=-1) + 1
    linked_tables: dict[str, tuple[str, str]] = {}
    result: dict[str, dict[str, str]] = {}

    for definition in definitions:
        bid = block_version_id(ir.parsing_run.parsing_run_id, page.page_id, next_ordinal)
        blocks.append(BlockVersion(bid, page.page_id, ir.parsing_run.parsing_run_id, next_ordinal,
                                   "table", definition["title"], BBox(*definition["bbox"]), next_order,
                                   text_origin=definition["text_origin"]))
        tid = table_id(bid)
        merges = {(m["row_index"], m["column_index"]): (m["row_span"], m["column_span"])
                  for m in definition.get("merged_cells", [])}
        covered = {
            (r + dr, c + dc)
            for (r, c), (rs, cs) in merges.items()
            for dr in range(rs) for dc in range(cs) if dr or dc
        }
        table_cells = tuple(
            TableCell(table_cell_id(tid, row, col), tid, bid, row, col,
                      *merges.get((row, col), (1, 1)))
            for row in range(definition["row_count"])
            for col in range(definition["column_count"])
            if (row, col) not in covered
        )
        tables.append(Table(tid, bid, None, definition["row_count"], definition["column_count"],
                            tuple(c.cell_id for c in table_cells)))
        cells.extend(table_cells)
        linked_tables[definition["table_key"]] = (tid, bid)
        next_ordinal += 1
        next_order += 1

    for unit in units:
        if unit.get("reuse_existing"):
            normalized = "".join(unit["source_text"].split())
            matches = [
                span for span in spans
                if "".join(span.quote.split()) == normalized
                and span.bbox is not None
                and all(abs(a - b) <= 2.0 for a, b in zip(span.bbox.as_list(), unit["bbox"]))
            ]
            if len(matches) != 1:
                raise ValueError(f"reviewed native-text span must match exactly once: {unit['unit_id']}")
            result[unit["unit_id"]] = {"source_span_id": matches[0].source_span_id,
                                       "block_version_id": matches[0].block_version_ids[0]}
            continue
        bid = block_version_id(ir.parsing_run.parsing_run_id, page.page_id, next_ordinal)
        tid = linked_tables[unit["table_key"]][0] if unit.get("table_key") else None
        text = unit["source_text"]
        block_type = "table" if tid else unit["content_kind"]
        blocks.append(BlockVersion(bid, page.page_id, ir.parsing_run.parsing_run_id, next_ordinal,
                                   block_type, text, BBox(*unit["bbox"]), next_order,
                                   text_origin=unit["text_origin"]))
        sid = source_span_id(bid, 0, text)
        spans.append(SourceSpan(sid, page.page_id, (bid,), text,
                                "table" if tid else unit["content_kind"], 0, len(text),
                                BBox(*unit["bbox"]), unit["text_origin"], tid,
                                unit.get("row_index"), unit.get("column_index")))
        item = {"source_span_id": sid, "block_version_id": bid}
        if tid:
            item["table_id"] = tid
            item["table_cell_id"] = table_cell_id(tid, unit["row_index"], unit["column_index"])
        if unit["text_origin"] == "manual_correction":
            cid = stable_id("correction", "stage6-standard-supplement", unit["unit_id"], text)
            corrections.append(ManualCorrection(cid, bid, text, text,
                                                "Original PDF visual transcription of the printed cell or label",
                                                REVIEWER, REVIEWED_AT))
            item["correction_id"] = cid
        result[unit["unit_id"]] = item
        next_ordinal += 1
        next_order += 1

    for region in regions:
        matching_images = [
            block for block in blocks
            if block.block_type == "image" and block.bbox is not None
            and all(abs(a - b) <= 0.5 for a, b in zip(block.bbox.as_list(), region["bbox"]))
        ]
        if len(matching_images) > 1:
            raise ValueError(f"ambiguous existing image region: {region['region_id']}")
        if matching_images:
            existing = matching_images[0]
            matching_figures = [figure for figure in figures if figure.block_version_id == existing.block_version_id]
            if len(matching_figures) != 1:
                raise ValueError(f"existing image has no unique Figure: {region['region_id']}")
            result[region["region_id"]] = {"figure_id": matching_figures[0].figure_id,
                                            "block_version_id": existing.block_version_id}
            continue
        bid = block_version_id(ir.parsing_run.parsing_run_id, page.page_id, next_ordinal)
        blocks.append(BlockVersion(bid, page.page_id, ir.parsing_run.parsing_run_id, next_ordinal,
                                   "image", "", BBox(*region["bbox"]), next_order,
                                   text_origin="native_text" if document_key == "DL5190.3" else "ocr_text"))
        fid = figure_id(bid)
        figures.append(Figure(fid, bid, None, region.get("figure_label")))
        result[region["region_id"]] = {"figure_id": fid, "block_version_id": bid}
        next_ordinal += 1
        next_order += 1

    payload = {
        "pages": [record_value(value) for value in ir.pages],
        "blocks": [record_value(value) for value in blocks],
        "tables": [record_value(value) for value in tables],
        "table_cells": [record_value(value) for value in cells],
        "figures": [record_value(value) for value in figures],
        "source_spans": [record_value(value) for value in spans],
        "manual_corrections": [record_value(value) for value in corrections],
    }
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
    adapted = replace(ir, blocks=tuple(blocks), source_spans=tuple(spans), tables=tuple(tables),
                      table_cells=tuple(cells), figures=tuple(figures), manual_corrections=tuple(corrections),
                      parsing_run=replace(ir.parsing_run,
                                          parser_version=ir.parsing_run.parser_version + "+stage6-original-standard-supplements-v1",
                                          output_fingerprint=fingerprint))
    return validate_document_ir(adapted), result


def build_reviewed_unit_evidence(
    ir: DocumentIR,
    unit: dict[str, Any],
    link: dict[str, str],
    *,
    manifest: dict[str, Any] | None = None,
):
    """Build validated, citable Evidence for one printed nonblank source unit.

    The caller must use an IR returned by ``append_reviewed_source_spans``.
    Printed table labels, units and design limits use ``requirement_source``;
    the form's null measurement fields have no source unit and cannot enter here.
    """

    manifest = load_supplements() if manifest is None else manifest
    correction_ids = (link["correction_id"],) if "correction_id" in link else ()
    table_context = ()
    if unit.get("table_key"):
        definition = next(t for t in manifest["tables"] if t["table_key"] == unit["table_key"])
        tid = link["table_id"]
        headers = tuple(c.cell_id for c in ir.table_cells if c.table_id == tid and c.row_index == 0)
        table_context = (TableContext(
            table_id=tid,
            row_indices=(unit["row_index"],),
            column_indices=(unit["column_index"],),
            header_cell_ids=headers,
            value_cell_ids=(link["table_cell_id"],),
            table_label=definition["title"],
            continuation_from_physical_page=definition.get("continuation_from_physical_page"),
            continuation_to_physical_page=definition.get("continuation_to_physical_page"),
            review_scope="cell",
            leaf_column_count=definition["column_count"],
            header_hierarchy=tuple(definition["header_hierarchy"]),
            partition_note="Printed label, unit or fixed criterion; blank template cells are excluded.",
        ),)
    return build_evidence(
        ir,
        (link["source_span_id"],),
        evidence_role="requirement_source",
        disposition="structured" if unit["semantic_use"] == "printed_standard_source" else "region_scoped",
        review_status="accepted",
        reviewer=REVIEWER,
        reviewed_at=REVIEWED_AT,
        review_reason="Original PDF page and printed cell/line verified; null form values are not observations.",
        effective_text=unit["source_text"],
        effective_text_origin=unit["text_origin"],
        correction_ids=correction_ids,
        table_context=table_context,
    )


def build_reviewed_row_evidence(
    ir: DocumentIR,
    units: list[dict[str, Any]],
    links: dict[str, dict[str, str]],
    *,
    manifest: dict[str, Any] | None = None,
):
    """Combine a printed parameter and unit into one form-row Evidence.

    Both cells retain separate original-PDF SourceSpan locations.  This is
    region-scoped template context, not an Observation or standalone Statement.
    """

    manifest = load_supplements() if manifest is None else manifest
    if len(units) != 2 or any(unit.get("semantic_use") != "form_template_context_only" for unit in units):
        raise ValueError("form row requires two reviewed template cells")
    first, second = units
    if not (first.get("table_key") == second.get("table_key") and first.get("row_index") == second.get("row_index")
            and second.get("column_index") == first.get("column_index") + 1):
        raise ValueError("form row cells must be adjacent on one table row")
    definition = next(t for t in manifest["tables"] if t["table_key"] == first["table_key"])
    tid = links[first["unit_id"]]["table_id"]
    headers = tuple(c.cell_id for c in ir.table_cells if c.table_id == tid and c.row_index == 0)
    context = TableContext(
        table_id=tid, row_indices=(first["row_index"],),
        column_indices=(first["column_index"], second["column_index"]),
        header_cell_ids=headers,
        value_cell_ids=tuple(links[unit["unit_id"]]["table_cell_id"] for unit in units),
        table_label=definition["title"],
        continuation_from_physical_page=definition.get("continuation_from_physical_page"),
        continuation_to_physical_page=definition.get("continuation_to_physical_page"),
        review_scope="cell", leaf_column_count=definition["column_count"],
        header_hierarchy=tuple(definition["header_hierarchy"]),
        partition_note="Printed parameter and unit; design/measurement cells remain null template slots.",
    )
    return build_evidence(
        ir, tuple(links[unit["unit_id"]]["source_span_id"] for unit in units),
        evidence_role="requirement_source", disposition="region_scoped",
        review_status="accepted", reviewer=REVIEWER, reviewed_at=REVIEWED_AT,
        review_reason="Original PDF row name and unit verified; blank value slots are not observations.",
        effective_text="\n".join(unit["source_text"] for unit in units),
        effective_text_origin="manual_correction",
        correction_ids=tuple(links[unit["unit_id"]]["correction_id"] for unit in units),
        table_context=(context,),
    )


def build_reviewed_group_evidence(
    ir: DocumentIR,
    group: dict[str, Any],
    links: dict[str, dict[str, str]],
    *,
    manifest: dict[str, Any] | None = None,
):
    """Build one validated Evidence from every reviewed span in a manifest group.

    A grouped form row remains template context.  Each SourceSpan location and
    cell coordinate survives in the Evidence and its TableContext; empty value
    slots have no source unit and cannot enter this function.
    """

    manifest = load_supplements() if manifest is None else manifest
    units_by_id = {unit["unit_id"]: unit for unit in manifest["source_units"]}
    member_ids = group["source_unit_ids"]
    if not member_ids or any(uid not in units_by_id or uid not in links for uid in member_ids):
        raise ValueError("Evidence group has missing source-unit links")
    units = [units_by_id[uid] for uid in member_ids]
    if any((unit["document_key"], unit["physical_page"], unit["semantic_use"])
           != (group["document_key"], group["physical_page"], group["semantic_use"])
           for unit in units):
        raise ValueError("Evidence group mixes pages or semantic uses")
    table_keys = {unit.get("table_key") for unit in units}
    if len(table_keys) != 1:
        raise ValueError("Evidence group mixes table and non-table sources")

    context: tuple[TableContext, ...] = ()
    table_key = next(iter(table_keys))
    if table_key is not None:
        definition = next(table for table in manifest["tables"] if table["table_key"] == table_key)
        tid = links[member_ids[0]]["table_id"]
        if any(links[uid].get("table_id") != tid for uid in member_ids):
            raise ValueError("Evidence group spans multiple Document IR tables")
        headers = tuple(cell.cell_id for cell in ir.table_cells
                        if cell.table_id == tid and cell.row_index == 0)
        value_cells = tuple(dict.fromkeys(links[uid]["table_cell_id"] for uid in member_ids))
        context = (TableContext(
            table_id=tid,
            row_indices=tuple(sorted({unit["row_index"] for unit in units})),
            column_indices=tuple(sorted({unit["column_index"] for unit in units})),
            header_cell_ids=headers,
            value_cell_ids=value_cells,
            table_label=definition["title"],
            continuation_from_physical_page=definition.get("continuation_from_physical_page"),
            continuation_to_physical_page=definition.get("continuation_to_physical_page"),
            review_scope="cell",
            leaf_column_count=definition["column_count"],
            header_hierarchy=tuple(definition["header_hierarchy"]),
            partition_note="Printed cells only; unfilled design and measured-value slots remain null.",
        ),)

    correction_ids = tuple(links[uid]["correction_id"] for uid in member_ids
                           if "correction_id" in links[uid])
    if correction_ids and len(correction_ids) != len(member_ids):
        raise ValueError("Evidence group mixes manually corrected and native text")
    source_span_ids = tuple(links[uid]["source_span_id"] for uid in member_ids)
    source_text = "\n".join(unit["source_text"] for unit in units)
    return build_evidence(
        ir, source_span_ids,
        evidence_role="requirement_source",
        disposition="structured" if group["semantic_use"] == "printed_standard_source" else "region_scoped",
        review_status="accepted", reviewer=REVIEWER, reviewed_at=REVIEWED_AT,
        review_reason="Original PDF printed source reviewed as one citable group; blank form slots excluded.",
        effective_text=source_text,
        effective_text_origin="manual_correction" if correction_ids else "native_text",
        correction_ids=correction_ids,
        table_context=context,
    )


def build_manifest(original_root: Path = ROOT.parent / "Original materials") -> dict[str, Any]:
    """Construct the checked-in manifest from pinned originals and reviewed cells."""

    import pymupdf
    from turbine_kg.documents.catalog import load_identity_catalog
    from turbine_kg.documents.pdf import parse_registered_pdf

    paths = {
        "DL5190.3": original_root / "标准法规/DL5190.3—2019电力建设施工技术规范第3部分：汽轮发电机组.pdf",
        "DLT863": original_root / "标准法规/DLT 863-2016汽轮机启动调试导则.pdf",
    }
    for key, path in paths.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != PDF_SHA256[key]:
            raise ValueError(f"original PDF changed: {key}")

    units: list[dict[str, Any]] = []
    forms: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    visuals: list[dict[str, Any]] = []

    def add_unit(key: str, page: int, uid: str, text: str, bbox: list[float],
                 kind: str = "paragraph", *, table_key: str | None = None,
                 row: int | None = None, col: int | None = None,
                 reuse: bool = False) -> None:
        unit = {
            "unit_id": uid, "document_key": key, "physical_page": page,
            "logical_page": {("DL5190.3", 113): "102", ("DLT863", 27): "38", ("DLT863", 28): "39"}.get((key, page)),
            "content_kind": kind, "source_text": text.strip(), "bbox": [round(float(v), 2) for v in bbox],
            "text_origin": "native_text" if reuse else "manual_correction",
            "review_basis": "original_pdf_visual_review", "evidence_role": "requirement_source",
            "reuse_existing": reuse,
            "semantic_use": (
                "form_template_context_only" if key == "DLT863"
                else "document_context_only" if kind in {"caption", "heading"}
                else "printed_standard_source"
            ),
        }
        if table_key:
            unit.update(table_key=table_key, row_index=row, column_index=col)
        units.append(unit)

    def blank(key: str, page: int, field_id: str, bbox: list[float] | None,
              *, table_key: str | None = None, row: int | None = None,
              col: int | None = None, role: str = "unfilled_template_cell") -> None:
        if table_key:
            return  # Encoded once by the compact table-level null rules below.
        field = {"field_id": field_id, "document_key": key, "physical_page": page,
                 "template_role": role, "value": None, "is_observation": False,
                 "bbox": [round(float(v), 2) for v in bbox] if bbox else None}
        if table_key:
            field.update(table_key=table_key, row_index=row, column_index=col)
        forms.append(field)

    # Reuse native spans except where a text span's oversized box also encloses
    # figure labels in the current processing PDF. That line gets a tight,
    # independently checked source region instead.
    catalog = load_identity_catalog(ROOT / "data/registry/source_assets.jsonl",
                                    ROOT / "config/revision_identity.tsv",
                                    ROOT / "config/derived_asset_links.tsv")
    registered = "标准法规/DL5190.3—2019电力建设施工技术规范第3部分：汽轮发电机组.pdf"
    ir = parse_registered_pdf(paths["DL5190.3"], registered, catalog,
                              title="DL/T 5190.3—2019 电力建设施工技术规范 第3部分：汽轮发电机组",
                              page_indices=(112,), parser_version="stage6-standard-supplement-review-v1")
    native_y = (60.6, 115.3, 183.6, 195.8, 222.6, 239.2, 301.6, 469.6, 497.2, 685.7, 700.0)
    for span in ir.source_spans:
        if span.bbox and any(abs(span.bbox.y0 - value) <= 0.3 for value in native_y):
            kind = "caption" if any(abs(span.bbox.y0 - value) <= 0.3 for value in (115.3, 685.7)) else "heading" if abs(span.bbox.y0 - 469.6) <= 0.3 else "paragraph"
            bbox = span.bbox.as_list()
            line_above_figure = abs(span.bbox.y0 - 497.2) <= 0.3
            if line_above_figure:
                bbox[3] = 511.5  # The figure begins at y=511.92; exclude its two C labels.
            add_unit("DL5190.3", 113, f"dl5190-p113-native-{len(units)+1:02d}",
                     span.quote, bbox, kind, reuse=not line_above_figure)

    tables.append({"table_key": "dl5190-12.1.15", "document_key": "DL5190.3", "physical_page": 113,
                   "title": "表12.1.15 起重机静态刚性要求", "bbox": [152, 126, 443, 181],
                   "row_count": 3, "column_count": 2, "text_origin": "native_text",
                   "merged_cells": [{"row_index": 0, "column_index": 0, "row_span": 3, "column_span": 1}],
                   "header_hierarchy": ["左侧三行合并：起重机的静态刚性（引用GB/T 14405-2011通用桥式起重机）",
                                        "右侧低、中、高定位精度各一行"]})
    add_unit("DL5190.3", 113, "dl5190-12.1.15-left", "起重机的静态刚性（引用GB/T14405-2011通用桥式起重机）",
             [153, 128, 294, 180], "table", table_key="dl5190-12.1.15", row=0, col=0)
    units[-1]["semantic_use"] = "document_context_only"
    for n, (text, low, high) in enumerate((("低定位精度要求ƒ≤S/500", 128, 145),
                                           ("中定位精度要求ƒ≤S/750", 145, 162),
                                           ("高定位精度要求ƒ≤S/1000", 162, 180))):
        add_unit("DL5190.3", 113, f"dl5190-12.1.15-limit-{n+1}", text,
                 [295, low, 442, high], "table", table_key="dl5190-12.1.15", row=n, col=1)

    # Only a reviewed image region and literal labels are recorded; geometry is
    # carried by the original page bitmap, not invented prose SourceSpans.
    visuals.extend([
        {"region_id": "dl5190-p25-fig-4.5.8-1", "document_key": "DL5190.3", "physical_page": 25,
         "figure_label": "图4.5.8-1", "bbox": [155, 120, 445, 277], "review_basis": "original_pdf_visual_review",
         "visible_labels": ["A-A", "油入口", "排油", "推力轴承调整机构", "推力轴承中心"],
         "same_page_caption": "图4.5.8-1 推力轴承调整机构详图"},
        {"region_id": "dl5190-p25-fig-4.5.8-2", "document_key": "DL5190.3", "physical_page": 25,
         "figure_label": "图4.5.8-2", "bbox": [205, 344, 405, 500], "review_basis": "original_pdf_visual_review",
         "visible_labels": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11"],
         "same_page_caption": "图4.5.8-2 金斯伯里推力轴承"},
        {"region_id": "dl5190-p25-fig-4.5.8-3", "document_key": "DL5190.3", "physical_page": 25,
         "figure_label": "图4.5.8-3", "bbox": [177, 555, 435, 773], "review_basis": "original_pdf_visual_review",
         "visible_labels": ["轴承箱与瓦套两侧总间隙c", "密封环间隙a", "油封总间隙b",
                            "X", "Y", "推力总间隙（X+Y）", "瓦套", "推力瓦块", "球面座", "推力盘"],
         "cross_page_caption": {"physical_page": 26, "text": "图4.5.8-3 双楔面、小岛型球座式推力轴承"}},
        {"region_id": "dl5190-p86-fig-9.6.2", "document_key": "DL5190.3", "physical_page": 86,
         "figure_label": "图9.6.2", "bbox": [185, 382, 410, 442], "review_basis": "original_pdf_visual_review",
         "visible_labels": ["a"], "same_page_caption": "图9.6.2 密封瓦在瓦座内的轴向间隙示意图"},
        {"region_id": "dl5190-p113-fig-12.2.1", "document_key": "DL5190.3", "physical_page": 113,
         "figure_label": "图12.2.1", "bbox": [104.4, 511.92, 289.44, 679.38],
         "review_basis": "original_pdf_visual_review", "visible_labels": ["C", "C"],
         "same_page_caption": "图12.2.1 车轮轮缘内侧与工字钢轨道下翼缘边缘的间隙"},
    ])

    # D.1 original printed grid: header row 0, 22 data rows.  Boundaries are
    # measured from the original scan; grouped boiler/turbine/generator columns.
    tables.append({"table_key": "dlt863-d1-p27", "document_key": "DLT863", "physical_page": 27,
                   "title": "表D.1 机组额定负荷主要运行参数记录表", "bbox": [52.5, 217, 523.5, 768],
                   "row_count": 23, "column_count": 12, "text_origin": "manual_correction",
                   "header_hierarchy": ["锅炉、汽轮机、发电机三组", "每组项目/单位/设计值/数值四列"],
                   "continuation_to_physical_page": 28})
    add_unit("DLT863", 27, "dlt863-p27-appendix", "附录D（资料性附录）\n整套启动试运记录",
             [213, 123, 371, 169], "heading")
    add_unit("DLT863", 27, "dlt863-p27-d1-intro", "D.1 机组额定负荷时主要运行参数记录见表D.1。",
             [52, 178, 350, 195])
    add_unit("DLT863", 27, "dlt863-p27-d1-caption", "表D.1 机组额定负荷主要运行参数记录表",
             [189, 198, 413, 217], "caption")
    blank("DLT863", 27, "dlt863-p27-project-name", [134, 217, 207, 235], role="project_name")
    blank("DLT863", 27, "dlt863-p27-unit-number", [322, 217, 523.5, 235], role="unit_number")
    add_unit("DLT863", 27, "dlt863-p27-project-name-label", "工程名称", [82, 218, 133, 234])
    add_unit("DLT863", 27, "dlt863-p27-unit-number-label", "机组号", [330, 218, 364, 235])
    group_names = ("锅炉主要运行指标", "汽轮机主要运行指标", "发电机主要运行指标")
    group_x = ((54, 210), (210, 362), (362, 520.3))
    for group, (name, (x0, x1)) in enumerate(zip(group_names, group_x)):
        add_unit("DLT863", 27, f"dlt863-p27-d1-group-{group}", name,
                 [x0, 235, x1, 251], "table", table_key="dlt863-d1-p27", row=0, col=group*4)
    x27 = (54, 126.7, 158.3, 184.3, 210, 282.3, 310, 337.7, 362, 438, 469.7, 495, 520.3)
    for col in range(12):
        add_unit("DLT863", 27, f"dlt863-p27-d1-header-c{col:02d}",
                 ("项目", "单位", "设计值", "数值")[col % 4],
                 [x27[col], 251, x27[col+1], 278.3], "table",
                 table_key="dlt863-d1-p27", row=0, col=col)
    y27 = (278.3, 294.3, 310, 337.7, 365, 380.7, 408.3, 424.3, 451.7, 479,
           506.7, 534, 550, 577.3, 604.7, 632, 659, 686.3, 702, 717.7, 733.7, 749.3, 764.7)
    boiler = [
        ("汽包/分离器压力", "MPa"), ("过热蒸汽压力", "MPa"), ("过热蒸汽温度（左）", "℃"),
        ("过热蒸汽温度（右）", "℃"), ("过热蒸汽流量", "t/h"), ("再热器出口蒸汽压力", "MPa"),
        ("再热器入口汽温", "℃"), ("再热器出口汽温（左）", "℃"), ("再热器出口汽温（右）", "℃"),
        ("给水温度", "℃"), ("给水流量", "t/h"), ("排烟温度", "℃"), ("烟气含氧量", "%"),
        ("总燃料量", "t/h"), ("总一次风量", "t/h"), ("总二次风量", "t/h"), ("脱硫效率", "%"),
        ("脱硝效率", "%"), ("SO₂排放浓度", "mg/m³"), ("NOₓ排放浓度", "mg/m³"),
        ("烟尘排放浓度", "mg/m³"),
    ]
    turbine = [
        ("主蒸汽压力", "MPa"), ("主蒸汽温度", "℃"), ("再热蒸汽压力", "MPa"), ("再热蒸汽温度", "℃"),
        ("高压缸排汽压力", "MPa"), ("高压缸排汽温度", "℃"), ("中压缸排汽压力", "MPa"),
        ("中压缸排汽温度", "℃"), ("凝汽器真空", "kPa"), ("低压缸排汽温度", "℃"),
        ("循环水入口温度", "℃"), ("循环水出口温度", "℃"), ("低压加热器入口温度", "℃"),
        ("低压加热器出口温度", "℃"), ("低压加热器出口流量", "t/h"),
        ("高压加热器入口温度", "℃"), ("高压加热器出口温度", "℃"),
        ("一段抽汽压力", "MPa"), ("二段抽汽压力", "MPa"), ("三段抽汽压力", "MPa"),
        ("四段抽汽压力", "MPa"), ("五段抽汽压力", "MPa"),
    ]
    generator = [
        ("有功功率", "MW"), ("无功功率", "MVAR"), ("定子电压", "kV"),
        ("定子电流（A）", "kA"), ("定子电流（B）", "kA"), ("定子电流（C）", "kA"),
        ("频率", "Hz"), ("定子绕组最高温度", "℃"), ("定子绕组最低温度", "℃"),
        ("定子铁芯最高温度", "℃"), ("定子铁芯最低温度", "℃"),
        ("发电机励磁电压", "V"), ("发电机励磁电流", "A"), ("定子冷却水出口温度", "℃"),
        ("氢冷器出口氢气温度（励端）", "℃"), ("氢冷器出口氢气温度（汽端）", "℃"),
        ("氢纯度", "%"), ("氢压", "MPa"),
    ]
    for group, rows in enumerate((boiler, turbine, generator)):
        for row in range(1, 23):
            y0, y1 = y27[row-1], y27[row]
            for offset in range(4):
                col = group*4+offset
                box = [x27[col], y0, x27[col+1], y1]
                if row <= len(rows) and offset < 2:
                    label = rows[row-1][offset]
                    add_unit("DLT863", 27, f"dlt863-p27-d1-r{row:02d}-c{col:02d}", label,
                             box, "table", table_key="dlt863-d1-p27", row=row, col=col)
                else:
                    blank("DLT863", 27, f"dlt863-p27-d1-r{row:02d}-c{col:02d}", box,
                          table_key="dlt863-d1-p27", row=row, col=col,
                          role="design_value" if offset == 2 else "recorded_value" if offset == 3 else "unassigned_template_cell")

    # D.1 continuation, physical page 28 / printed 39.
    tables.append({"table_key": "dlt863-d1-p28", "document_key": "DLT863", "physical_page": 28,
                   "title": "表D.1（续）", "bbox": [66.5, 119.5, 536.5, 246.5],
                   "row_count": 4, "column_count": 12, "text_origin": "manual_correction",
                   "header_hierarchy": ["继承物理27页锅炉/汽轮机/发电机三组，每组项目/单位/设计值/数值"],
                   "continuation_from_physical_page": 27})
    add_unit("DLT863", 28, "dlt863-p28-d1-caption", "表D.1（续）", [272, 102, 351, 119], "caption")
    x28d1 = (67.3, 139.7, 171.3, 197.3, 223.7, 296, 323.3, 349.5, 375.7,
             451.7, 483, 508.3, 533.7)
    for col in range(12):
        add_unit("DLT863", 28, f"dlt863-p28-d1-header-c{col:02d}",
                 ("项目", "单位", "设计值", "数值")[col % 4],
                 [x28d1[col], 120.7, x28d1[col+1], 148.7], "table",
                 table_key="dlt863-d1-p28", row=0, col=col)
    y28d1 = (148.7, 165, 180.7, 196.3)
    for row in range(1, 4):
        for col in range(12):
            box = [x28d1[col], y28d1[row-1], x28d1[col+1], y28d1[row]]
            if col == 4:
                add_unit("DLT863", 28, f"dlt863-p28-d1-r{row}-c4", f"{('六','七','八')[row-1]}段抽汽压力",
                         box, "table", table_key="dlt863-d1-p28", row=row, col=col)
            elif col == 5:
                add_unit("DLT863", 28, f"dlt863-p28-d1-r{row}-c5", "MPa",
                         box, "table", table_key="dlt863-d1-p28", row=row, col=col)
            else:
                blank("DLT863", 28, f"dlt863-p28-d1-r{row}-c{col}", box,
                      table_key="dlt863-d1-p28", row=row, col=col,
                      role="design_value" if col % 4 == 2 else "recorded_value" if col % 4 == 3 else "unassigned_template_cell")
    for n, label in enumerate(("调试单位", "生产单位", "监理单位")):
        add_unit("DLT863", 28, f"dlt863-p28-d1-sign-label-{n}", f"{label}（签字）：",
                 [77, 196.3+n*15.9, 173, 212.2+n*15.9])
        blank("DLT863", 28, f"dlt863-p28-d1-sign-{n}", [174, 196.3+n*15.9, 534, 212.2+n*15.9],
              role=f"{label}签字")

    # D.2 has its own 13-column grid and four printed operating-state groups.
    tables.append({"table_key": "dlt863-d2-p28", "document_key": "DLT863", "physical_page": 28,
                   "title": "表D.2 机组整套试运汽轮发电机组轴振记录表", "bbox": [66, 290.5, 537, 741],
                   "row_count": 13, "column_count": 13, "text_origin": "manual_correction",
                   "merged_cells": [{"row_index": start, "column_index": 0, "row_span": 3, "column_span": 1}
                                    for start in (1, 4, 7, 10)],
                   "header_hierarchy": ["日期", "运行状态：工况/数值", "位置", "轴振动值 μm：1号至9号"]})
    add_unit("DLT863", 28, "dlt863-p28-d2-intro", "D.2 机组整套试运汽轮发电机组轴振记录见表D.2",
             [67, 252, 457, 271])
    add_unit("DLT863", 28, "dlt863-p28-d2-caption", "表D.2 机组整套试运汽轮发电机组轴振记录表",
             [194, 274, 437, 290], "caption")
    blank("DLT863", 28, "dlt863-p28-d2-project-name", [131, 291, 240, 308], role="project_name")
    blank("DLT863", 28, "dlt863-p28-d2-unit-number", [308, 291, 534, 308], role="unit_number")
    add_unit("DLT863", 28, "dlt863-p28-d2-project-name-label", "工程名称", [74, 292, 114, 307])
    add_unit("DLT863", 28, "dlt863-p28-d2-unit-number-label", "机组号", [251, 292, 284, 307])
    x28d2 = (67.7, 118.7, 191.3, 241, 268, 297.7, 327.3, 357, 386.3,
             416.3, 446, 475.3, 505, 534)
    add_unit("DLT863", 28, "dlt863-p28-d2-header-date", "日期", [67.7, 308, 118.7, 350.7],
             "table", table_key="dlt863-d2-p28", row=0, col=0)
    add_unit("DLT863", 28, "dlt863-p28-d2-header-state", "运行状态", [118.7, 308, 241, 350.7],
             "table", table_key="dlt863-d2-p28", row=0, col=1)
    add_unit("DLT863", 28, "dlt863-p28-d2-header-position", "位置", [241, 308, 268, 350.7],
             "table", table_key="dlt863-d2-p28", row=0, col=3)
    add_unit("DLT863", 28, "dlt863-p28-d2-header-vibration", "轴振动值 μm", [268, 308, 534, 335],
             "table", table_key="dlt863-d2-p28", row=0, col=4)
    for n in range(9):
        add_unit("DLT863", 28, f"dlt863-p28-d2-header-shaft-{n+1}", f"{n+1}号",
                 [x28d2[n+4], 335, x28d2[n+5], 350.7], "table",
                 table_key="dlt863-d2-p28", row=0, col=n+4)
    y28d2 = (350.7, 366.3, 392.7, 420.3, 446, 472.3, 499.7,
             525.7, 552, 579.3, 605.3, 631.7, 659)
    states = (("空负荷", "3000r/min"), ("并网", None), ("首次满负荷", None), ("额定负荷", None))
    for group, (state, printed) in enumerate(states):
        for subrow in range(3):
            row = group*3+subrow+1
            for col in range(13):
                if col == 0 and subrow > 0:  # One blank date field spans each three-row state group.
                    continue
                box = [x28d2[col], y28d2[row-1], x28d2[col+1], y28d2[row]]
                if col == 0:
                    box[3] = y28d2[group*3+3]
                literal = None
                if col == 1:
                    literal = (state if group == 0 else f"{state}\nMW") if subrow == 0 else "主蒸汽压力 MPa" if subrow == 1 else "主蒸汽温度 ℃"
                elif col == 2 and printed and subrow == 0:
                    literal = printed
                elif col == 3 and subrow in (1, 2):
                    literal = "x" if subrow == 1 else "y"
                if literal:
                    add_unit("DLT863", 28, f"dlt863-p28-d2-r{row:02d}-c{col:02d}", literal,
                             box, "table", table_key="dlt863-d2-p28", row=row, col=col)
                else:
                    blank("DLT863", 28, f"dlt863-p28-d2-r{row:02d}-c{col:02d}", box,
                          table_key="dlt863-d2-p28", row=row, col=col,
                          role="shaft_vibration_um" if col >= 4 else "unfilled_template_cell")
    for n, label in enumerate(("施工单位", "调试单位", "生产单位", "监理单位", "建设单位")):
        add_unit("DLT863", 28, f"dlt863-p28-d2-sign-label-{n}", f"{label}（签字）：",
                 [77, 659+n*15.7, 174, 674.7+n*15.7])
        blank("DLT863", 28, f"dlt863-p28-d2-sign-{n}", [175, 659+n*15.7, 534, 674.7+n*15.7],
              role=f"{label}签字")

    blank_rules = [
        {"rule_id": "d1-p27-design-and-measured", "table_key": "dlt863-d1-p27",
         "row_range": [1, 22], "columns": [2, 3, 6, 7, 10, 11], "value": None, "is_observation": False},
        {"rule_id": "d1-p27-boiler-final-unassigned", "table_key": "dlt863-d1-p27",
         "rows": [22], "columns": [0, 1], "value": None, "is_observation": False},
        {"rule_id": "d1-p27-generator-tail-unassigned", "table_key": "dlt863-d1-p27",
         "row_range": [19, 22], "columns": [8, 9], "value": None, "is_observation": False},
        {"rule_id": "d1-p28-continuation-blanks", "table_key": "dlt863-d1-p28",
         "row_range": [1, 3], "columns": [0, 1, 2, 3, 6, 7, 8, 9, 10, 11],
         "value": None, "is_observation": False},
        {"rule_id": "d2-p28-four-date-fields", "table_key": "dlt863-d2-p28",
         "rows": [1, 4, 7, 10], "columns": [0], "merged_row_span": 3,
         "value": None, "is_observation": False},
        {"rule_id": "d2-p28-unfilled-state-values", "table_key": "dlt863-d2-p28",
         "row_range": [2, 12], "columns": [2], "value": None, "is_observation": False},
        {"rule_id": "d2-p28-state-row-position", "table_key": "dlt863-d2-p28",
         "rows": [1, 4, 7, 10], "columns": [3], "value": None, "is_observation": False},
        {"rule_id": "d2-p28-nine-vibration-columns", "table_key": "dlt863-d2-p28",
         "row_range": [1, 12], "columns": list(range(4, 13)), "value": None,
         "is_observation": False},
    ]

    # Partition every nonempty source unit exactly once.  Form row groups keep
    # printed parameter labels with their units or location markers, while
    # retaining one original-PDF bbox per constituent cell.
    units_by_id = {unit["unit_id"]: unit for unit in units}
    evidence_groups: list[dict[str, Any]] = []

    def group(gid: str, member_ids: list[str]) -> None:
        members = [units_by_id[uid] for uid in member_ids]
        evidence_groups.append({
            "group_id": gid,
            "document_key": members[0]["document_key"],
            "physical_page": members[0]["physical_page"],
            "semantic_use": members[0]["semantic_use"],
            "source_unit_ids": member_ids,
        })

    for unit in units:
        if unit["document_key"] == "DL5190.3":
            group(f"group-{unit['unit_id']}", [unit["unit_id"]])

    for uid in ("dlt863-p27-appendix", "dlt863-p27-d1-intro", "dlt863-p27-d1-caption"):
        group(f"group-{uid}", [uid])
    group("group-dlt863-p27-project-identifiers",
          ["dlt863-p27-project-name-label", "dlt863-p27-unit-number-label"])
    group("group-dlt863-p27-d1-headers",
          [f"dlt863-p27-d1-group-{n}" for n in range(3)]
          + [f"dlt863-p27-d1-header-c{col:02d}" for col in range(12)])
    for row in range(1, 23):
        for group_col, section in ((0, "boiler"), (4, "turbine"), (8, "generator")):
            if group_col == 0 and row == 22 or group_col == 8 and row >= 19:
                continue
            group(f"group-dlt863-p27-d1-{section}-r{row:02d}",
                  [f"dlt863-p27-d1-r{row:02d}-c{col:02d}" for col in (group_col, group_col + 1)])

    group("group-dlt863-p28-d1-caption", ["dlt863-p28-d1-caption"])
    group("group-dlt863-p28-d1-headers",
          [f"dlt863-p28-d1-header-c{col:02d}" for col in range(12)])
    for row in range(1, 4):
        group(f"group-dlt863-p28-d1-r{row}",
              [f"dlt863-p28-d1-r{row}-c4", f"dlt863-p28-d1-r{row}-c5"])
    group("group-dlt863-p28-d1-signatures",
          [f"dlt863-p28-d1-sign-label-{n}" for n in range(3)])
    for uid in ("dlt863-p28-d2-intro", "dlt863-p28-d2-caption"):
        group(f"group-{uid}", [uid])
    group("group-dlt863-p28-d2-project-identifiers",
          ["dlt863-p28-d2-project-name-label", "dlt863-p28-d2-unit-number-label"])
    group("group-dlt863-p28-d2-headers",
          ["dlt863-p28-d2-header-date", "dlt863-p28-d2-header-state",
           "dlt863-p28-d2-header-position", "dlt863-p28-d2-header-vibration"]
          + [f"dlt863-p28-d2-header-shaft-{n}" for n in range(1, 10)])
    for row in range(1, 13):
        member_ids = [f"dlt863-p28-d2-r{row:02d}-c01"]
        if row == 1:
            member_ids.append("dlt863-p28-d2-r01-c02")  # Printed 3000r/min, not a measurement.
        elif row % 3 in (2, 0):
            member_ids.append(f"dlt863-p28-d2-r{row:02d}-c03")  # Printed x/y position.
        group(f"group-dlt863-p28-d2-r{row:02d}", member_ids)
    group("group-dlt863-p28-d2-signatures",
          [f"dlt863-p28-d2-sign-label-{n}" for n in range(5)])

    return {
        "schema_version": 1, "artifact_kind": "stage6_standard_source_supplements",
        "authority": "Original materials original PDF; OCR is processing assistance only",
        "original_pdf_sha256": PDF_SHA256,
        "source_units": units, "evidence_groups": evidence_groups,
        "form_schema": forms, "blank_cell_rules": blank_rules,
        "tables": tables, "visual_regions": visuals,
        "source_boundaries": [
            {"document_key": "DL5190.3", "physical_page": 113,
             "preceding_physical_page": 112, "following_physical_page": 114,
             "notes": "12.1.15(5) begins on printed 101; 12.2.3 continues on printed 103."},
            {"document_key": "DLT863", "physical_pages": [14, 15],
             "parent": "5.2.14.1 e) 密封油系统调整（单流环）",
             "child_positions": [{"physical_page": 14, "labels": ["1）"]},
                                 {"physical_page": 15, "labels": ["2）", "3）", "4）", "5）", "6）"]}],
             "after_child_six": "5.2.14.1 f)、g)、h) are siblings, not children of e)."},
            {"document_key": "DLT863", "physical_page": 15, "printed_page": "11",
             "next_physical_page": 16, "next_printed_page": "13", "missing_printed_pages": [12],
             "boundary": "5.2.15.2 a)–d) visible; no complete list or next-page clause may be inferred."},
        ],
        "source_structure": [
            {"group_id": "dlt863-5.2.14.1-d", "physical_pages": [14], "parent": "5.2.14.1 d)",
             "parent_evidence_id": "evidence-cd2a00746a928ad8fdf8",
             "child_labels": ["1）", "2）", "3）", "4）", "5）", "6）", "7）"],
             "child_evidence_ids": ["evidence-650975a896876ca7c13f", "evidence-ce0c6fca6810fa2b60b7",
                                    "evidence-f4c6cdcf23ebf8865c1a", "evidence-7df2aae933c522d24b63",
                                    "evidence-257184909857bc95884c", "evidence-9cb932403ffa1be5eeb5",
                                    "evidence-fa46378641d9939dee2f"], "status": "visible_complete"},
            {"group_id": "dlt863-5.2.14.1-e", "physical_pages": [14, 15], "parent": "5.2.14.1 e)",
             "parent_evidence_id": "evidence-2e6ceb3a4df913d5bb1a",
             "child_labels": ["1）", "2）", "3）", "4）", "5）", "6）"],
             "child_evidence_ids": ["evidence-51e3bcecc735117e1fd4", "evidence-9621e1540ab96385c2c1",
                                    "evidence-98c9dd569ee66f0de55f", "evidence-51a3af3750db9355ea4e",
                                    "evidence-4f79a7760a62a42b87a4", "evidence-4931f3d3bc2090ffb4e2"],
             "status": "visible_complete", "continuation_from_physical_page": 14,
             "continuation_to_physical_page": 15},
            {"group_id": "dlt863-5.2.15.1-h", "physical_pages": [15], "parent": "5.2.15.1 h)",
             "parent_evidence_id": "evidence-52d40466085559dda91c",
             "child_labels": ["1）", "2）", "3）"],
             "child_evidence_ids": ["evidence-1b57903c1f463f08b7a6", "evidence-9506072e7ddcf593b0b2",
                                    "evidence-5eaa1072bd44a58bb50c"], "status": "visible_complete"},
            {"group_id": "dlt863-5.2.15.1-j", "physical_pages": [15], "parent": "5.2.15.1 j)",
             "parent_evidence_id": "evidence-fb504c1ff8aca79680ca",
             "child_labels": ["1）", "2）"],
             "child_evidence_ids": ["evidence-c61e5ba3a5464dd6d668", "evidence-cc36c8d0adb6bd5d0bc9"],
             "status": "visible_complete"},
            {"group_id": "dlt863-5.2.15.2", "physical_pages": [15], "parent": "5.2.15.2 调试注意事项",
             "parent_evidence_id": "evidence-404b6f10d05f944ac3ff", "child_labels": ["a)", "b)", "c)", "d)"],
             "child_evidence_ids": ["evidence-c1e7b707ec66dc30081f", "evidence-730d85e280a4e926afaa",
                                    "evidence-30833eb2f3915b8ebf28", "evidence-cc64a6dc901e388c8f5b"],
             "status": "source_incomplete_after_printed_11", "missing_next_printed_page": 12},
        ],
    }


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--build-manifest":
        DEFAULT_PATH.write_text(json.dumps(build_manifest(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif len(sys.argv) != 1:
        raise SystemExit("usage: stage6_standard_supplements.py [--build-manifest]")
    data = load_supplements()
    print(json.dumps({"source_units": len(data["source_units"]), "standalone_blank_fields": len(data["form_schema"]),
                      "inferred_blank_cells": len(blank_cell_coordinates(data)),
                      "visual_regions": len(data["visual_regions"]), "tables": len(data["tables"])},
                     ensure_ascii=False))

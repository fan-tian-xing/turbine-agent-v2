"""Build reviewable Evidence units for every positive Stage 6 Golden Sample page."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

import pymupdf

from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.documents.ids import stable_id
from turbine_kg.documents.page_identity import apply_page_identity
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.documents.models import record_value
from turbine_kg.evidence import build_evidence, validate_evidence_bundle
from turbine_kg.evidence.models import EvidenceBundle
from turbine_kg.settings import Settings

from scripts.stage6_cross_page_supplements import (
    build_cross_page_evidence,
    load_supplements as load_cross_page_supplements,
    unit_for_sample_page,
)
from scripts.stage6_d300n_p50_regions import build_reviewed_evidence as build_d300n_p50_row8_evidence
from scripts.stage6_auxiliary_supplements import (
    append_reviewed_source_spans as append_auxiliary_source_spans,
    build_reviewed_group_evidence as build_auxiliary_group_evidence,
    build_reviewed_header_evidence as build_auxiliary_header_evidence,
    evidence_groups_for_page as auxiliary_groups_for_page,
    load_supplements as load_auxiliary_supplements,
    source_units_for_page as auxiliary_units_for_page,
)
from scripts.stage6_auxiliary_early_page_supplements import (
    PAGES as AUXILIARY_EARLY_PAGES,
    load_review as load_auxiliary_early_review,
    repair_early_page_reading_order,
    validate_current_pdfs as validate_auxiliary_early_pdfs,
)
from scripts.stage6_standard_supplements import (
    append_reviewed_source_spans,
    build_reviewed_group_evidence,
    evidence_groups_for_page,
    load_supplements as load_standard_supplements,
    source_units_for_page as standard_units_for_page,
)
from scripts.stage6_reviewed_pdf_regions import verify_region_text
from scripts.stage6_page_evidence_boundaries import (
    apply_reviewed_page_boundaries,
    body_spans_for_sample,
)
from scripts.stage6_visual_regions import (
    build_visual_evidence,
    load_regions as load_visual_regions,
    regions_for_page,
)
from scripts.stage6_page_review_binding import page_review_fingerprint, current_stage5_bindings
from stage5_fingerprint import ocr_fingerprint


ROOT = Path(__file__).resolve().parents[1]
STAGE6 = ROOT / "data" / "stage6"
GOLDEN = STAGE6 / "stage6_evidence_golden_sample.json"
DECISIONS = STAGE6 / "stage6_page_review_decisions.jsonl"
TABLE_DECISIONS = STAGE6 / "stage6_table_review_decisions.jsonl"
OVERRIDES = STAGE6 / "stage6_source_review_overrides.json"
STAGE5_MANIFEST = ROOT / "data/stage5/stage5_sample_manifest.json"
STAGE5_REVIEW = ROOT / "data/registry/ocr_validation_report.json"

DOCUMENTS = {
    "DL5190.3": {
        "registered": "标准法规/DL5190.3—2019电力建设施工技术规范第3部分：汽轮发电机组.pdf",
        "original": "标准法规/DL5190.3—2019电力建设施工技术规范第3部分：汽轮发电机组.pdf",
        "processing": None,
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
    "DLT863": {
        "registered": "OCR/DLT 863-2016汽轮机启动调试导则(OCR).pdf",
        "original": "标准法规/DLT 863-2016汽轮机启动调试导则.pdf",
        "processing": "DLT 863-2016汽轮机启动调试导则(OCR).pdf",
        "title": "DL/T 863—2016 汽轮机启动调试导则",
        "role": "requirement_source",
    },
    "HAF103": {
        "registered": "OCR/HAF103核动力厂调试和运行安全规定-印刷页3-34(OCR).pdf",
        "original": "标准法规/HAF103核动力厂调试和运行安全规定.pdf",
        "processing": "HAF103核动力厂调试和运行安全规定-印刷页3-34(OCR).pdf",
        "title": "HAF103 核动力厂调试和运行安全规定",
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


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _asset_file(asset, settings: Settings) -> Path:
    if asset.source_root_id == "source":
        return settings.source_root / asset.relative_path
    if asset.source_root_id == "ocr_derived" and asset.relative_path.startswith("OCR/"):
        return settings.ocr_derived_root / asset.relative_path[4:]
    raise ValueError(f"unsupported registered PDF root: {asset.asset_id}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reviewed_documents(manifest: dict, review: dict, *,
                        catalog, settings: Settings, definitions: dict) -> dict:
    """Resolve current processing PDFs while retaining originals as authority."""
    entries = {row["document_key"]: row for row in manifest["documents"]}
    reviews = {row["document_key"]: row for row in review["full_corpus_reviews"]}
    if (len(entries) != len(manifest["documents"]) or set(entries) != set(definitions)
            or len(reviews) != len(review["full_corpus_reviews"]) or set(reviews) != set(entries)):
        raise ValueError("current Stage 5 document/review coverage differs")
    result = {}
    for key, entry in entries.items():
        original = catalog.asset_for_id(entry["original_asset_id"])
        processed = catalog.asset_for_id(entry["processing_asset_id"])
        if (original.asset_kind != "original"
                or original.relative_path != entry.get("original_relative_path", entry["processing_relative_path"])
                or processed.relative_path != entry["processing_relative_path"]
                or original.relative_path != definitions[key]["original"]
                or (original.document_logical_id, original.revision_id)
                != (processed.document_logical_id, processed.revision_id)
                or original.document_logical_id != entry["document_logical_id"]):
            raise ValueError(f"current Stage 5 registered source identity differs: {key}")
        if processed.asset_id != original.asset_id and (
                processed.asset_kind != "derived_ocr" or processed.derived_from_asset_id != original.asset_id):
            raise ValueError(f"processing asset is not derived from the authority: {key}")
        record = reviews[key]
        count = int(entry["page_count"])
        ranges = record.get("reviewed_page_ranges", [])
        covered = []
        for interval in ranges:
            if (not isinstance(interval, list) or len(interval) != 2
                    or any(type(value) is not int for value in interval)
                    or not 1 <= interval[0] <= interval[1] <= count):
                raise ValueError(f"invalid current full-page review range: {key}")
            covered.extend(range(interval[0], interval[1] + 1))
        if sorted(covered) != list(range(1, count + 1)):
            raise ValueError(f"current full-page PDF coverage differs: {key}")
        for role, asset in (("original", original), ("processing", processed)):
            expected_sha = asset.sha256
            if (record.get(role + "_sha256") != expected_sha
                    or _file_sha256(_asset_file(asset, settings)) != expected_sha):
                raise ValueError(f"current {role} PDF fingerprint differs from Registry: {key}")
        for asset in (original, processed):
            with pymupdf.open(_asset_file(asset, settings)) as pdf:
                if len(pdf) != count:
                    raise ValueError(f"current PDF physical page count differs: {key}")
                if asset.asset_id == processed.asset_id:
                    blank_pages = set(record.get("blank_pages", []))
                    if any(not page.get_text().strip() and number not in blank_pages
                           for number, page in enumerate(pdf, start=1)):
                        raise ValueError(f"current processing PDF has an unsearchable page: {key}")
        result[key] = {**definitions[key], "original": original.relative_path,
                       "registered": processed.relative_path,
                       "processing": (processed.relative_path[4:]
                                      if processed.source_root_id == "ocr_derived" else None)}
    return result


def _parse_reviewed_page(sample: dict, document: dict, *, catalog, settings: Settings):
    """Read corrected PDF text and canonical boxes without an old OCR-box overlay."""
    original_path = settings.source_root / document["original"]
    authority_identity = catalog.asset_for_path(document["original"])
    if sample.get("original_asset_id", authority_identity.asset_id) != authority_identity.asset_id:
        raise ValueError("Golden Sample authority differs from current registered original")
    processed_path = (settings.ocr_derived_root / document["processing"]
                      if document["processing"] is not None else original_path)
    number = int(sample["physical_page"])
    with pymupdf.open(original_path) as original, pymupdf.open(processed_path) as processed:
        authority, page = original[number - 1], processed[number - 1]
        if (authority.mediabox != page.mediabox or authority.cropbox != page.cropbox
                or authority.rotation != page.rotation):
            raise ValueError("reviewed PDF page geometry differs from its original authority")
        rotation = int(authority.rotation or 0)
    ir = parse_registered_pdf(
        processed_path, document["registered"], catalog, title=document["title"],
        page_indices=(number - 1,), parser_version="stage6-reviewed-pdf-text-v2",
        parsing_run_id=stable_id("run", "stage6-reviewed-pdf-text-v2", document["registered"], number),
    )
    return apply_page_identity(ir, {number: sample["logical_page"]}), rotation


def _decisions() -> dict[str, dict]:
    if not DECISIONS.exists():
        return {}
    rows = [json.loads(line) for line in DECISIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != len({row["review_id"] for row in rows}):
        raise ValueError("duplicate Stage 6 page review decision IDs")
    return {row["review_id"]: row for row in rows}


def _reviewed_table_pages() -> set[tuple[str, int]]:
    if not TABLE_DECISIONS.exists():
        return set()
    rows = [json.loads(line) for line in TABLE_DECISIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(row.get("decision") != "accepted_region_scoped" for row in rows):
        raise ValueError("Stage 6 table decisions contain an unresolved review")
    return {(row["document_key"], int(row["physical_page"])) for row in rows}


def _body_spans(ir):
    page = ir.pages[0]
    blocks = {block.block_version_id: block for block in ir.blocks}
    spans = sorted(
        ir.source_spans,
        key=lambda span: min(blocks[value].reading_order for value in span.block_version_ids),
    )
    return [
        span for span in spans
        if span.quote.strip()
        and span.bbox is not None
        and span.bbox.y0 >= 30
        and span.bbox.y1 <= page.height_pt - 25
    ]


def _exclude_haf103_page_footer(sample: dict, spans, overrides: dict):
    """Remove only the original-page-checked printed folio before grouping.

    The PDF parser currently orders that footer before the first body line.
    Leaving it in the stream joins navigation with a real cross-page clause.
    """
    if sample["document_key"] != "HAF103":
        return spans
    rows = [
        row for row in overrides["source_fragment_exclusions"]
        if (row["document_key"], row["physical_page"], row["kind"])
        == ("HAF103", sample["physical_page"], "navigation_only")
    ]
    expected = f"— {sample['logical_page']} —"
    matches = [
        span for span in spans
        if span.quote == expected and span.bbox is not None
        and 740 <= span.bbox.y0 <= 765 and span.bbox.y1 <= 775
    ]
    if (len(rows) != 1 or len(matches) != 1
            or rows[0]["expected_text_sha256"] != hashlib.sha256(expected.encode("utf-8")).hexdigest()):
        raise ValueError(f"HAF103 printed footer review differs on p{sample['physical_page']}")
    return [span for span in spans if span.source_span_id != matches[0].source_span_id]


def _groups(spans, *, ocr: bool):
    if not ocr:
        return [(span.source_span_id,) for span in spans if len(span.quote.strip()) >= 5]
    groups: list[tuple[str, ...]] = []
    current: list = []
    for span in spans:
        text = span.quote.strip()
        starts_unit = bool(re.match(r"^(?:\d+(?:\.\d+)*|[一二三四五六七八九十]+[、.])", text))
        if current and starts_unit:
            groups.append(tuple(item.source_span_id for item in current))
            current = []
        current.append(span)
        current_chars = sum(len(item.quote.strip()) for item in current)
        if text.endswith(("。", "；", "：", "!", "！", "?", "？")) or current_chars >= 220:
            groups.append(tuple(item.source_span_id for item in current))
            current = []
    if current:
        groups.append(tuple(item.source_span_id for item in current))
    return groups


def _supplemental_table_page_groups(sample: dict, spans, overrides: dict) -> list[tuple[str, ...]] | None:
    """Select independently reviewed text regions on a table page."""

    matches = [
        entry for entry in overrides["supplemental_prose_regions"]
        if (entry["document_key"], entry["physical_page"])
        == (sample["document_key"], sample["physical_page"])
    ]
    if not matches:
        table_matches = [
            entry for entry in overrides["reviewed_table_text_regions"]
            if (entry["document_key"], entry["physical_page"])
            == (sample["document_key"], sample["physical_page"])
        ]
        if not table_matches:
            return None
        if len(table_matches) != 1:
            raise ValueError("duplicate reviewed table text region page")
        groups = []
        used_span_ids: set[str] = set()
        for region in table_matches[0]["regions"]:
            selected = [
                span for span in spans
                if span.bbox is not None and any(
                    rectangle["x0"] <= (span.bbox.x0 + span.bbox.x1) / 2 <= rectangle["x1"]
                    and rectangle["y0"] <= (span.bbox.y0 + span.bbox.y1) / 2 <= rectangle["y1"]
                    for rectangle in region["rectangles"]
                )
            ]
            if not selected or any(span.source_span_id in used_span_ids for span in selected):
                raise ValueError(f"empty or overlapping reviewed table text region: {region['region_id']}")
            digest = hashlib.sha256("\x1f".join(span.quote for span in selected).encode("utf-8")).hexdigest()
            if digest != region["expected_text_sha256"]:
                raise ValueError(f"reviewed table text changed: {region['region_id']}")
            used_span_ids.update(span.source_span_id for span in selected)
            groups.append(tuple(span.source_span_id for span in selected))
        return groups
    if len(matches) != 1:
        raise ValueError("duplicate supplemental prose region review")
    ranges = matches[0]["y_ranges"]
    groups = []
    for lower, upper in ranges:
        ids = tuple(
            span.source_span_id for span in spans
            if span.bbox is not None and lower <= span.bbox.y0 and span.bbox.y1 <= upper
        )
        if not ids:
            raise ValueError(f"missing reviewed supplemental prose in y-range {lower}:{upper}")
        groups.append(ids)
    return groups


def _stage12_extractability(sample: dict, effective_text: str, overrides: dict) -> tuple[str | None, str | None]:
    """Apply source decisions and quarantine unmistakably damaged OCR forms."""

    matches = [
        row for row in overrides["extractability_decisions"]
        if (row["document_key"], row["physical_page"])
        == (sample["document_key"], sample["physical_page"])
        and (row["match"] == "all" or row["match"] == "text_prefix" and effective_text.startswith(row["value"]))
    ]
    if len(matches) > 1:
        raise ValueError("overlapping Stage 6 extractability decisions")
    if not matches:
        if (
            "\ufffd" in effective_text
            or re.search(r"[A-Za-z]9{5,}[A-Za-z]", effective_text)
            or re.search(r"\d--\d|\b[A-Za-z]_[A-Za-z0-9]", effective_text)
        ):
            return "pending_review", "OCR contains a damaged character or malformed formula token; verify against the original page."
        return None, None
    row = matches[0]
    if row["status"] not in {"pending_review", "context_only"}:
        raise ValueError("invalid Stage 6 extractability status")
    return row["status"], row["reason"]


def _page_fingerprint(items, *, source_input_fingerprint: str) -> str:
    return page_review_fingerprint(items, source_input_fingerprint=source_input_fingerprint)


def _exclude_reviewed_fragments(sample: dict, span_groups, drafts, overrides: dict,
                                cross_page_manifest: dict):
    """Withhold exact source fragments lacking their adjacent-page context."""

    exclusions = [
        row for row in overrides["source_fragment_exclusions"]
        if (row["document_key"], row["physical_page"])
        == (sample["document_key"], sample["physical_page"])
        and not (sample["document_key"] == "HAF103" and row["kind"] == "navigation_only")
    ]
    excluded_indices: set[int] = set()
    for row in exclusions:
        matches = [
            index for index, draft in enumerate(drafts)
            if hashlib.sha256(draft.effective_text.encode("utf-8")).hexdigest()
            == row["expected_text_sha256"]
        ]
        if (
            len(matches) != 1 or matches[0] in excluded_indices or not row.get("reason")
            or row.get("kind") not in {"navigation_only", "excluded_incomplete_source"}
        ):
            raise ValueError(f"stale or duplicate reviewed fragment exclusion: {sample['document_key']} p{sample['physical_page']}")
        if sample["document_key"] == "HAF103" and row["kind"] == "excluded_incomplete_source":
            unit = unit_for_sample_page(cross_page_manifest, "HAF103", sample["physical_page"])
            if unit is None:
                raise ValueError("HAF103 cross-page exclusion has no reviewed source unit")
            fragment = next(part for part in unit["fragments"]
                            if part["physical_page"] == sample["physical_page"])
            draft = drafts[matches[0]]
            boxes = fragment["source_boxes"]
            if (re.sub(r"\s+", "", draft.source_text) != re.sub(r"\s+", "", fragment["source_text"])
                    or len(draft.locations) != len(boxes)
                    or any(location.physical_page != sample["physical_page"]
                           or any(abs(a-b) > 0.6 for a, b in zip(location.bbox.as_list(), box["bbox_pdf_pt"]))
                           for location, box in zip(draft.locations, boxes))):
                raise ValueError("HAF103 excluded fragment differs from reviewed cross-page source")
        excluded_indices.add(matches[0])
    return (
        [group for index, group in enumerate(span_groups) if index not in excluded_indices],
        [draft for index, draft in enumerate(drafts) if index not in excluded_indices],
    )


def _reviewed_source_supplements_for_sample(
    sample: dict,
    *,
    catalog,
    settings: Settings,
    standard_manifest: dict,
    cross_page_manifest: dict,
    auxiliary_manifest: dict,
    visual_manifest: dict | None = None,
    documents: dict | None = None,
) -> list[tuple[object, object, dict]]:
    """Return (Evidence, its actual DocumentIR, annotation metadata).

    New reviewed source adapters attach here without changing page-review
    decisions or claiming that an old fingerprint covers new Evidence.
    """

    key, number = sample["document_key"], int(sample["physical_page"])
    if documents is None:
        documents = _reviewed_documents(
            json.loads(STAGE5_MANIFEST.read_text(encoding="utf-8")),
            json.loads(STAGE5_REVIEW.read_text(encoding="utf-8")),
            catalog=catalog, settings=settings, definitions=DOCUMENTS,
        )
    result: list[tuple[object, object, dict]] = []
    if visual_manifest is None:
        visual_manifest = (
            load_visual_regions(settings=settings)
            if (key, number) in {("DL5190.3", 25), ("DL5190.3", 86),
                                 ("DL5190.3", 113), ("D300N", 38),
                                 ("auxiliary_installation_book", 300)}
            else {"regions": []}
        )
    groups = evidence_groups_for_page(standard_manifest, key, number)
    if groups:
        document = documents[key]
        ir, _ = _parse_reviewed_page(sample, document, catalog=catalog, settings=settings)
        processing_path = _asset_file(catalog.asset_for_path(document["registered"]), settings)
        with pymupdf.open(processing_path) as pdf:
            for unit in standard_units_for_page(standard_manifest, key, number):
                verify_region_text(pdf[number - 1], unit["bbox"], unit["source_text"], unit["unit_id"])
        ir, links = append_reviewed_source_spans(ir, key, manifest=standard_manifest)
        result.extend(
            (build_reviewed_group_evidence(ir, group, links, manifest=standard_manifest), ir,
             {"source_supplement_kind": "standard_reviewed", "source_supplement_group_id": group["group_id"]})
            for group in groups
        )
    cross_unit = unit_for_sample_page(cross_page_manifest, key, number)
    if cross_unit is not None:
        item, ir = build_cross_page_evidence(
            cross_unit, catalog=catalog, documents=documents, settings=settings,
        )
        result.append((item, ir, {
            "source_supplement_kind": "cross_page_reviewed",
            "cross_page_source_unit_id": cross_unit["source_unit_id"],
        }))
    if key == "D300N" and number == 50:
        document = documents[key]
        ir, _ = _parse_reviewed_page(sample, document, catalog=catalog, settings=settings)
        adapted, rows = build_d300n_p50_row8_evidence(ir, settings=settings)
        result.extend((item, adapted, metadata) for item, metadata in rows)
    if key == "auxiliary_installation_book" and number in {244, 300, 360}:
        document = documents[key]
        ir, _ = _parse_reviewed_page(sample, document, catalog=catalog, settings=settings)
        processing_path = _asset_file(catalog.asset_for_path(document["registered"]), settings)
        units = auxiliary_units_for_page(auxiliary_manifest, key, number)
        with pymupdf.open(processing_path) as pdf:
            for unit in units:
                verify_region_text(pdf[number - 1], unit["bbox"],
                                   unit["ocr_reference_text"], unit["unit_id"])
        ir, links = append_auxiliary_source_spans(ir, key, manifest=auxiliary_manifest)
        for unit in units:
            link = links[unit["unit_id"]]
            item = build_evidence(
                ir, (link["source_span_id"],), evidence_role=unit.get("source_role", "background"),
                disposition="region_scoped", effective_text=unit["source_text"],
                effective_text_origin="manual_correction",
                correction_ids=(link["correction_id"],),
            )
            result.append((item, ir, {
                "source_supplement_kind": "auxiliary_original_page_region",
                "source_supplement_group_id": unit["unit_id"],
                "source_context_group_key": unit.get("source_question_key"),
                "source_group_kind": unit.get("group_kind"),
                "stage12_extractability": unit["stage12_extractability"],
                "extractability_reason": unit.get("extractability_reason") or (
                    "Printed formula or figure text is citable only in this original-page region; "
                    "diagram geometry and engineering claims require separate review."
                    if number == 300 else
                    "Original-page body region excludes exam-bank sidebar text; question/answer "
                    "grouping and cross-page continuation must be checked before extraction."
                ),
            }))
    if key == "auxiliary_installation_book" and number == 480:
        document = documents[key]
        ir, _ = _parse_reviewed_page(sample, document, catalog=catalog, settings=settings)
        processing_path = _asset_file(catalog.asset_for_path(document["registered"]), settings)
        with pymupdf.open(processing_path) as pdf:
            for unit in auxiliary_units_for_page(auxiliary_manifest, key, number):
                verify_region_text(pdf[number - 1], unit["bbox"], unit["source_text"], unit["unit_id"])
        ir, links = append_auxiliary_source_spans(ir, key, manifest=auxiliary_manifest)
        for unit in auxiliary_units_for_page(auxiliary_manifest, key, number):
            if unit["content_kind"] == "table":
                continue
            link = links[unit["unit_id"]]
            item = build_evidence(
                ir, (link["source_span_id"],), evidence_role="background",
                disposition="structured" if unit["content_kind"] == "paragraph" else "region_scoped",
                review_status="accepted", reviewer="Codex_original_pdf_page_review",
                reviewed_at="2026-09-26T00:00:00+08:00",
                review_reason="Original PDF printed prose and local bbox visually reviewed.",
                effective_text=unit["source_text"], effective_text_origin="manual_correction",
                correction_ids=(link["correction_id"],),
            )
            result.append((item, ir, {"source_supplement_kind": "auxiliary_reviewed",
                                      "source_supplement_group_id": unit["unit_id"]}))
        header = build_auxiliary_header_evidence(ir, links, manifest=auxiliary_manifest)
        result.append((header, ir, {
            "source_supplement_kind": "auxiliary_reviewed_header",
            "source_supplement_group_id": "aux-p480-two-tier-table-header",
        }))
        for group in auxiliary_groups_for_page(auxiliary_manifest, key, number):
            item = build_auxiliary_group_evidence(ir, group, links, manifest=auxiliary_manifest)
            metadata = {"source_supplement_kind": "auxiliary_reviewed",
                        "source_supplement_group_id": group["group_id"]}
            if group["row_index"] == 5:
                metadata.update({
                    "stage12_extractability": "context_only",
                    "extractability_reason": "Printed drawing/discussion question counts and score 15 disagree; preserve the original cells without arithmetic repair.",
                })
            result.append((item, ir, metadata))
    for region in regions_for_page(visual_manifest, key, number):
        result.append(build_visual_evidence(
            region, catalog=catalog, documents=documents, settings=settings,
        ))
    return result


def _decision_for_fingerprint(review_id: str, fingerprint: str, decisions: dict[str, dict]) -> tuple[str, dict | None, bool]:
    """Never carry an accepted review over to a changed Evidence page."""

    decision = decisions.get(review_id)
    stale = bool(decision and decision.get("expected_page_fingerprint") != fingerprint)
    value = "needs_review" if stale or decision is None else decision.get("decision")
    if value not in {"accepted", "rejected", "needs_review"}:
        raise ValueError(f"unsupported page review decision: {review_id}")
    return value, decision, stale


def _apply_page_review(item, ir, decision_value: str, decision: dict | None):
    if decision_value == "needs_review":
        reviewed = replace(item, review_status="needs_review", reviewer=None,
                           reviewed_at=None, review_reason=None)
    else:
        if decision is None:
            raise ValueError("accepted or rejected Evidence requires a page decision")
        reviewed = replace(item, review_status=decision_value,
                           reviewer=decision["reviewer"], reviewed_at=decision["reviewed_at"],
                           review_reason=decision["reason"])
    validate_evidence_bundle(
        EvidenceBundle(1, ir.revision.revision_id, (reviewed,), ir.parsing_run.output_fingerprint), ir,
    )
    return reviewed


def main(*, prepare_review: bool = False) -> None:
    settings = Settings.from_environment()
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    documents = _reviewed_documents(
        json.loads(STAGE5_MANIFEST.read_text(encoding="utf-8")),
        json.loads(STAGE5_REVIEW.read_text(encoding="utf-8")),
        catalog=catalog, settings=settings, definitions=DOCUMENTS,
    )
    source_input_fingerprint, _ = ocr_fingerprint()
    current_inputs = current_stage5_bindings(ROOT, source_input_fingerprint)
    if any(golden.get("inputs", {}).get(field) != digest for field, digest in current_inputs.items()):
        raise ValueError("Stage 6 Golden Sample is not bound to current reviewed Stage 5 inputs")
    overrides = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    if overrides.get("schema_version") != 1:
        raise ValueError("unsupported Stage 6 source review overrides")
    reviewed_documents = {
        row["document_key"]
        for collection in (
            "supplemental_prose_regions", "reviewed_table_text_regions",
            "source_fragment_exclusions", "omitted_incomplete_source_regions", "ocr_box_edits",
            "ocr_box_exclusions", "ocr_margin_exclusions", "extractability_decisions",
        )
        for row in overrides[collection]
    }
    if set(overrides.get("original_pdf_sha256", {})) != reviewed_documents:
        raise ValueError("Stage 6 source review PDF fingerprint coverage mismatch")
    for document_key in reviewed_documents:
        original = settings.source_root / DOCUMENTS[document_key]["original"]
        if hashlib.sha256(original.read_bytes()).hexdigest() != overrides["original_pdf_sha256"][document_key]:
            raise ValueError(f"Stage 6 reviewed source PDF changed: {document_key}")
    supplemental_pages = {
        (row["document_key"], row["physical_page"])
        for row in overrides["supplemental_prose_regions"]
    } | {
        (row["document_key"], row["physical_page"])
        for row in overrides["reviewed_table_text_regions"]
    }
    standard_manifest = load_standard_supplements()
    cross_page_manifest = load_cross_page_supplements()
    auxiliary_manifest = load_auxiliary_supplements()
    auxiliary_early_review = load_auxiliary_early_review()
    validate_auxiliary_early_pdfs(auxiliary_early_review)
    visual_manifest = load_visual_regions(settings=settings)
    standard_pages = {
        (group["document_key"], group["physical_page"])
        for group in standard_manifest["evidence_groups"]
    }
    decisions = _decisions()
    reviewed_table_pages = _reviewed_table_pages()
    annotations: list[dict] = []
    review_queue: list[dict] = []
    page_results: list[dict] = []

    for sample in golden["records"]:
        eligibility = sample["evidence_eligibility"]
        page_key = (sample["document_key"], int(sample["physical_page"]))
        has_standard_source = page_key in standard_pages
        has_cross_page_source = unit_for_sample_page(
            cross_page_manifest, sample["document_key"], int(sample["physical_page"]),
        ) is not None
        has_auxiliary_source = page_key in {
            ("auxiliary_installation_book", 244),
            ("auxiliary_installation_book", 300),
            ("auxiliary_installation_book", 360),
            ("auxiliary_installation_book", 480),
        }
        has_auxiliary_early_source = (
            page_key[0] == "auxiliary_installation_book"
            and page_key[1] in AUXILIARY_EARLY_PAGES
        )
        has_visual_source = bool(regions_for_page(
            visual_manifest, sample["document_key"], int(sample["physical_page"]),
        ))
        supplemental_table_prose = (sample["document_key"], sample["physical_page"]) in supplemental_pages
        if (eligibility not in {"structured_candidate", "region_scoped"}
                and not supplemental_table_prose and not has_standard_source
                and not has_cross_page_source and not has_auxiliary_source
                and not has_auxiliary_early_source
                and not has_visual_source):
            if eligibility == "quarantined":
                table_page = (sample["document_key"], int(sample["physical_page"]))
                if table_page not in reviewed_table_pages:
                    review_queue.append({
                    "review_id": f"stage6-{sample['document_key']}-p{sample['physical_page']}-table",
                    "decision": "quarantined",
                    "document_key": sample["document_key"],
                    "physical_page": sample["physical_page"],
                    "logical_page": sample["logical_page"],
                    "authority_asset_id": sample["original_asset_id"],
                    "reason": sample["review_boundary"],
                    "table_context": sample["table_context"],
                    })
            continue

        document = documents[sample["document_key"]]
        processing = document["processing"]
        source_rows: list[tuple[object, object, dict]] = []
        # Reviewed original-page supplements replace the old OCR/table-page
        # selection on these pages.  All other pages retain the existing path.
        if has_auxiliary_early_source:
            ir, authority_rotation_deg = _parse_reviewed_page(
                sample, document, catalog=catalog, settings=settings,
            )
            processing_path = settings.ocr_derived_root / processing
            with pymupdf.open(processing_path) as pdf:
                ir, groups = repair_early_page_reading_order(
                    ir, pdf, sample["document_key"], review=auxiliary_early_review,
                )
            for group in groups:
                pending = group["source_binding_status"] == "pending_cross_page_context"
                parts = (
                    (("stem", group["stem_source_span_ids"]),
                     ("options", group["options_source_span_ids"]))
                    if "stem_source_span_ids" in group else ((None, group["source_span_ids"]),)
                )
                for part, span_ids in parts:
                    item = build_evidence(
                        ir, span_ids, evidence_role=document["role"],
                        disposition=("region_scoped" if pending or eligibility == "region_scoped"
                                     else "structured"),
                        authority_rotation_deg=authority_rotation_deg,
                    )
                    metadata = {
                        "source_supplement_kind": "auxiliary_early_page_reviewed",
                        "source_supplement_group_id": group["group_id"],
                    }
                    if part is not None:
                        metadata["source_supplement_part"] = part
                    if pending:
                        metadata.update({
                            "stage12_extractability": "context_only",
                            "extractability_reason": (
                                "This printed unit continues across a physical-page boundary; "
                                "the adjacent page is required before extracting a complete claim."
                            ),
                        })
                    source_rows.append((item, ir, metadata))
        elif not has_standard_source and not has_auxiliary_source:
            ir, authority_rotation_deg = _parse_reviewed_page(
                sample, document, catalog=catalog, settings=settings,
            )
            body_spans = body_spans_for_sample(
                sample, _exclude_haf103_page_footer(sample, _body_spans(ir), overrides), overrides,
            )
            span_groups = (
                _supplemental_table_page_groups(sample, body_spans, overrides)
                if supplemental_table_prose else _groups(body_spans, ocr=processing is not None)
            )
            if span_groups is None:
                raise AssertionError("supplemental prose grouping unexpectedly absent")
            disposition = "structured" if eligibility == "structured_candidate" else "region_scoped"
            coordinate_transform = (
                "document_ir_canonical_pdf_points_v1"
                if authority_rotation_deg == ir.pages[0].rotation_deg
                else "aligned_display_pdf_points_with_explicit_authority_rotation_v1"
            )
            drafts = [
                build_evidence(
                    ir, group, evidence_role=document["role"], disposition=disposition,
                    authority_rotation_deg=authority_rotation_deg,
                    coordinate_transform=coordinate_transform,
                )
                for group in span_groups
            ]
            _, drafts = _exclude_reviewed_fragments(sample, span_groups, drafts, overrides,
                                                    cross_page_manifest)
            if page_key == ("D300N", 50):
                mixed = [item for item in drafts if item.effective_text.startswith("8\n轴承箱上半装配：")]
                orphan = [item for item in drafts if item.effective_text.startswith("低压后轴承箱挡油环")]
                if len(mixed) != 1 or len(orphan) != 1:
                    raise ValueError("D300N p50 mixed row-8 columns changed; source review required")
                drafts = [item for item in drafts if item not in mixed and item not in orphan]
            source_rows.extend((item, ir, {}) for item in drafts)

        source_rows.extend(_reviewed_source_supplements_for_sample(
            sample, catalog=catalog, settings=settings,
            standard_manifest=standard_manifest, cross_page_manifest=cross_page_manifest,
            auxiliary_manifest=auxiliary_manifest, visual_manifest=visual_manifest,
            documents=documents,
        ))
        source_rows = apply_reviewed_page_boundaries(
            sample, source_rows, overrides,
            processing_path=(settings.ocr_derived_root / processing
                             if processing is not None else settings.source_root / document["original"]),
            original_path=settings.source_root / document["original"],
        )
        if not source_rows:
            raise RuntimeError(f"no citable Evidence units for {sample['document_key']} p{sample['physical_page']}")
        page_fingerprint = _page_fingerprint([item for item, _, _ in source_rows],
                                             source_input_fingerprint=source_input_fingerprint)
        review_id = f"stage6-{sample['document_key']}-p{sample['physical_page']}-page"
        decision_value, decision, stale = _decision_for_fingerprint(review_id, page_fingerprint, decisions)
        items = [(_apply_page_review(item, item_ir, decision_value, decision), item_ir, metadata)
                 for item, item_ir, metadata in source_rows]
        for item, item_ir, metadata in items:
            annotation = {
                "sample_id": sample["sample_id"],
                "document_key": sample["document_key"],
                "page_review_id": review_id,
                "maximum_eligibility": eligibility,
                "evidence": record_value(item),
                "input": {
                    "original_relative_path": document["original"],
                    "processing_relative_path": processing if item.processing_asset_id is not None else None,
                    "physical_page": sample["physical_page"],
                    "logical_page": sample["logical_page"],
                    "authority_asset_id": item.authority_asset_id,
                    "processing_asset_id": item.processing_asset_id,
                    "document_ir_output_fingerprint": item_ir.parsing_run.output_fingerprint,
                    "authority_rotation_deg": item.locations[0].authority_rotation_deg,
                    "coordinate_transform": item.locations[0].coordinate_transform,
                },
            }
            annotation.update(metadata)
            extractability, reason = _stage12_extractability(sample, item.effective_text, overrides)
            if extractability and "stage12_extractability" not in annotation:
                annotation["stage12_extractability"] = extractability
                annotation["extractability_reason"] = reason
            annotations.append(annotation)
        page_result = {
            "review_id": review_id,
            "decision": decision_value,
            "document_key": sample["document_key"],
            "physical_page": sample["physical_page"],
            "logical_page": sample["logical_page"],
            "maximum_eligibility": eligibility,
            "generated_disposition": (
                next(iter({item.disposition for item, _, _ in items}))
                if len({item.disposition for item, _, _ in items}) == 1 else "mixed"
            ),
            "authority_asset_id": sample["original_asset_id"],
            "original_relative_path": document["original"],
            "processing_relative_path": processing if any(item.processing_asset_id for item, _, _ in items) else None,
            "expected_page_fingerprint": page_fingerprint,
            "prior_review_fingerprint": decision.get("expected_page_fingerprint") if stale else None,
            "evidence_count": len(items),
            "bbox_count": sum(len(item.locations) for item, _, _ in items),
            "reason": (
                "Existing page review fingerprint is stale after reviewed source supplementation; verify the new Evidence page."
                if stale else decision.get("reason") if decision
                else "compare every bbox and text line against the Original materials page"
            ),
        }
        page_results.append(page_result)
        if decision_value == "needs_review":
            review_queue.append(page_result)

    if prepare_review:
        print(json.dumps({
            "mode": "prepare_review", "writes": False,
            "needs_review": review_queue,
            "page_fingerprints": [
                {"review_id": row["review_id"], "expected_page_fingerprint": row["expected_page_fingerprint"],
                 "decision": row["decision"], "prior_review_fingerprint": row["prior_review_fingerprint"]}
                for row in page_results
            ],
        }, ensure_ascii=False, indent=2))
        return

    _jsonl(STAGE6 / "stage6_evidence_annotations.jsonl", annotations)
    _jsonl(STAGE6 / "stage6_evidence_page_review_queue.jsonl", review_queue)
    audit = {
        "schema_version": 1,
        "stage": "6",
        "artifact_kind": "stage6_evidence_build_audit",
        "status": "complete" if page_results and all(row["decision"] == "accepted" for row in page_results) else "review_required",
        "formal_release": False,
        "producer": "scripts/build_stage6_golden_evidence.py",
        "inputs": {**current_inputs, "stage6_evidence_golden_sample_sha256": _file_sha256(GOLDEN)},
        "ocr_review_inputs": {
            name: _file_sha256(STAGE6 / name)
            for name in ("ocr_review_standards.json", "ocr_review_d300n_haf103.json", "ocr_review_auxiliary.json")
            if (STAGE6 / name).is_file()
        },
        "golden_sample_page_count": golden["sample_page_count"],
        "positive_page_count": len(page_results),
        "accepted_page_count": sum(row["decision"] == "accepted" for row in page_results),
        "evidence_count": len(annotations),
        "accepted_evidence_count": sum(row["evidence"]["review_status"] == "accepted" for row in annotations),
        "stage5_quarantined_table_page_count": sum(row["evidence_eligibility"] == "quarantined" for row in golden["records"]),
        "reviewed_table_page_count": len(reviewed_table_pages),
        "remaining_quarantined_table_page_count": len(review_queue),
        "negative_gate_page_count": sum(row["evidence_eligibility"] in {"metadata_only", "navigation_only", "boundary_only"} for row in golden["records"]),
        "authority": "Original materials original assets only; OCR is processing assistance",
        "annotations": "data/stage6/stage6_evidence_annotations.jsonl",
        "review_queue": "data/stage6/stage6_evidence_page_review_queue.jsonl",
        "review_decisions": "data/stage6/stage6_page_review_decisions.jsonl",
        "page_results": page_results,
    }
    (STAGE6 / "stage6_evidence_build_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"pages": len(page_results), "evidence": len(annotations), "status": audit["status"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-review", action="store_true",
                        help="print new page fingerprints and needs_review without writing files")
    main(prepare_review=parser.parse_args().prepare_review)

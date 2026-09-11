"""Build reviewable Evidence units for every positive Stage 6 Golden Sample page."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pymupdf

from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.documents.ids import stable_id
from turbine_kg.documents.ocr_review import apply_reviewed_ocr_boxes
from turbine_kg.documents.page_identity import apply_page_identity
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.documents.models import record_value
from turbine_kg.evidence import build_evidence
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
STAGE6 = ROOT / "data" / "stage6"
GOLDEN = STAGE6 / "stage6_evidence_golden_sample.json"
DECISIONS = STAGE6 / "stage6_page_review_decisions.jsonl"
TABLE_DECISIONS = STAGE6 / "stage6_table_review_decisions.jsonl"

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


def _page_fingerprint(items) -> str:
    payload = "\x1f".join(item.evidence_id for item in items).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def main() -> None:
    settings = Settings.from_environment()
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    rapidocr_path = ROOT / golden["inputs"]["stage5_rapidocr_benchmark"]
    rapidocr_sha256 = hashlib.sha256(rapidocr_path.read_bytes()).hexdigest()
    if rapidocr_sha256 != golden["inputs"]["stage5_rapidocr_benchmark_sha256"]:
        raise ValueError("frozen Stage 5 RapidOCR benchmark fingerprint changed")
    rapidocr = json.loads(rapidocr_path.read_text(encoding="utf-8"))
    rapidocr_by_page = {(row["document_key"], row["physical_page"]): row for row in rapidocr["records"]}
    decisions = _decisions()
    reviewed_table_pages = _reviewed_table_pages()
    annotations: list[dict] = []
    review_queue: list[dict] = []
    page_results: list[dict] = []

    for sample in golden["records"]:
        eligibility = sample["evidence_eligibility"]
        if eligibility not in {"structured_candidate", "region_scoped"}:
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

        document = DOCUMENTS[sample["document_key"]]
        processing = document["processing"]
        pdf_path = settings.source_root / document["original"] if processing is None else settings.ocr_derived_root / processing
        original_path = settings.source_root / document["original"]
        with pymupdf.open(original_path) as original_pdf:
            original_page = original_pdf[sample["physical_page"] - 1]
            authority_rotation_deg = int(original_page.rotation or 0)
            authority_width_pt = float(original_page.rect.width)
            authority_height_pt = float(original_page.rect.height)
        run_id = stable_id("run", "stage6-golden-evidence-v1", document["registered"], sample["physical_page"])
        ir = parse_registered_pdf(
            pdf_path,
            document["registered"],
            catalog,
            title=document["title"],
            page_indices=(sample["physical_page"] - 1,),
            parser_version="stage6-golden-evidence-document-ir-v1",
            parsing_run_id=run_id,
        )
        if processing is None:
            ir = apply_page_identity(ir, {sample["physical_page"]: sample["logical_page"]})
        else:
            ocr_record = rapidocr_by_page[(sample["document_key"], sample["physical_page"])]
            ir = apply_reviewed_ocr_boxes(
                ir,
                ocr_record["fresh_rapidocr"]["boxes"],
                dpi=int(rapidocr["engine"]["dpi"]),
                logical_page=sample["logical_page"],
                authority_width_pt=authority_width_pt,
                authority_height_pt=authority_height_pt,
                authority_rotation_deg=authority_rotation_deg,
            )

        span_groups = _groups(_body_spans(ir), ocr=processing is not None)
        disposition = "structured" if eligibility == "structured_candidate" else "region_scoped"
        coordinate_transform = (
            "document_ir_canonical_pdf_points_v1"
            if authority_rotation_deg == ir.pages[0].rotation_deg
            else "aligned_display_pdf_points_with_explicit_authority_rotation_v1"
        )
        drafts = [
            build_evidence(
                ir,
                group,
                evidence_role=document["role"],
                disposition=disposition,
                authority_rotation_deg=authority_rotation_deg,
                coordinate_transform=coordinate_transform,
            )
            for group in span_groups
        ]
        if not drafts:
            raise RuntimeError(f"no citable Evidence units for {sample['document_key']} p{sample['physical_page']}")
        page_fingerprint = _page_fingerprint(drafts)
        review_id = f"stage6-{sample['document_key']}-p{sample['physical_page']}-page"
        decision = decisions.get(review_id)
        if decision and decision.get("expected_page_fingerprint") != page_fingerprint:
            raise ValueError(f"page review decision is stale: {review_id}")
        decision_value = decision.get("decision") if decision else "needs_review"
        if decision_value not in {"accepted", "rejected", "needs_review"}:
            raise ValueError(f"unsupported page review decision: {review_id}")
        accepted = decision_value == "accepted"
        items = drafts
        if decision_value in {"accepted", "rejected"}:
            items = [
                build_evidence(
                    ir,
                    group,
                    evidence_role=document["role"],
                    disposition=disposition,
                    review_status=decision_value,
                    reviewer=decision["reviewer"],
                    reviewed_at=decision["reviewed_at"],
                    review_reason=decision["reason"],
                    authority_rotation_deg=authority_rotation_deg,
                    coordinate_transform=coordinate_transform,
                )
                for group in span_groups
            ]
        for item in items:
            annotations.append({
                "sample_id": sample["sample_id"],
                "document_key": sample["document_key"],
                "page_review_id": review_id,
                "maximum_eligibility": eligibility,
                "evidence": record_value(item),
                "input": {
                    "original_relative_path": document["original"],
                    "processing_relative_path": processing,
                    "physical_page": sample["physical_page"],
                    "logical_page": sample["logical_page"],
                    "authority_asset_id": item.authority_asset_id,
                    "processing_asset_id": item.processing_asset_id,
                    "document_ir_output_fingerprint": ir.parsing_run.output_fingerprint,
                    "authority_rotation_deg": authority_rotation_deg,
                    "coordinate_transform": coordinate_transform,
                },
            })
        page_result = {
            "review_id": review_id,
            "decision": decision_value,
            "document_key": sample["document_key"],
            "physical_page": sample["physical_page"],
            "logical_page": sample["logical_page"],
            "maximum_eligibility": eligibility,
            "generated_disposition": disposition,
            "authority_asset_id": sample["original_asset_id"],
            "original_relative_path": document["original"],
            "processing_relative_path": processing,
            "expected_page_fingerprint": page_fingerprint,
            "evidence_count": len(items),
            "bbox_count": sum(len(item.locations) for item in items),
            "reason": decision.get("reason") if decision else "compare every bbox and text line against the Original materials page",
        }
        page_results.append(page_result)
        if decision_value == "needs_review":
            review_queue.append(page_result)

    _jsonl(STAGE6 / "stage6_evidence_annotations.jsonl", annotations)
    _jsonl(STAGE6 / "stage6_evidence_page_review_queue.jsonl", review_queue)
    audit = {
        "schema_version": 1,
        "stage": "6",
        "artifact_kind": "stage6_evidence_build_audit",
        "status": "complete" if page_results and all(row["decision"] == "accepted" for row in page_results) else "review_required",
        "formal_release": False,
        "producer": "scripts/build_stage6_golden_evidence.py",
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
    main()

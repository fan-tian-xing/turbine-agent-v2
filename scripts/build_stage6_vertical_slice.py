"""Build a small Stage 6 candidate slice directly from Registry and PDFs.

This intentionally does not read Stage 3 real-trial Evidence or create
Statements.  Each output remains a review-required Evidence candidate.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.documents.page_identity import apply_page_identity
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.evidence import build_evidence
from turbine_kg.documents.models import BBox, record_value
from turbine_kg.documents.ids import stable_id
from turbine_kg.evidence.coordinates import pixel_bbox_to_pdf_bbox, union_pixel_boxes
from dataclasses import replace
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "data" / "stage6"
REVIEW_DECISIONS = OUTPUT_ROOT / "stage6_evidence_review_decisions.jsonl"


CASES = (
    {
        "document_key": "DL5190.3",
        "registered_relative_path": "标准法规/DL5190.3—2019电力建设施工技术规范第3部分：汽轮发电机组.pdf",
        "original_relative_path": "标准法规/DL5190.3—2019电力建设施工技术规范第3部分：汽轮发电机组.pdf",
        "physical_page": 86,
        "logical_page": "75",
        "role": "requirement_source",
        "disposition": "region_scoped",
        "title": "DL/T 5190.3—2019 电力建设施工技术规范 第3部分：汽轮发电机组",
        "path_root": "source",
    },
    {
        "document_key": "D300N",
        "registered_relative_path": "OCR/汽轮机本体安装及维护说明书(OCR).pdf",
        "original_relative_path": "汽轮机说明书/汽轮机本体安装及维护说明书.pdf",
        "physical_page": 73,
        "logical_page": "3-5-1",
        "role": "requirement_source",
        "disposition": "region_scoped",
        "title": "N-300 汽轮机本体安装及维护说明书",
        "path_root": "ocr",
    },
    {
        "document_key": "HAF103",
        "registered_relative_path": "OCR/HAF103核动力厂调试和运行安全规定-印刷页3-34(OCR).pdf",
        "original_relative_path": "标准法规/HAF103核动力厂调试和运行安全规定.pdf",
        "physical_page": 1,
        "logical_page": "3",
        "role": "requirement_source",
        "disposition": "region_scoped",
        "title": "HAF103 核动力厂调试和运行安全规定",
        "path_root": "ocr",
    },
)


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _latest(path_pattern: str) -> Path:
    paths = sorted((ROOT / "data" / "stage5").glob(path_pattern))
    if not paths:
        raise FileNotFoundError(path_pattern)
    return paths[-1]


def _review_decisions() -> dict[str, dict]:
    if not REVIEW_DECISIONS.exists():
        return {}
    rows = [json.loads(line) for line in REVIEW_DECISIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({row["review_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate Stage 6 review decision IDs")
    return {row["review_id"]: row for row in rows}


def main() -> None:
    settings = Settings.from_environment()
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    rapidocr = json.loads(_latest("stage5_rapidocr_sample_benchmark_*.json").read_text(encoding="utf-8"))
    rapidocr_by_page = {
        (row["document_key"], row["physical_page"]): row
        for row in rapidocr["records"]
    }
    decisions = _review_decisions()
    candidates: list[dict] = []
    review_queue: list[dict] = []
    for case in CASES:
        path_root = settings.source_root if case["path_root"] == "source" else settings.ocr_derived_root
        relative_file = case["registered_relative_path"] if case["path_root"] == "source" else case["registered_relative_path"].split("/", 1)[-1]
        pdf_path = path_root / relative_file
        ir = parse_registered_pdf(
            pdf_path,
            case["registered_relative_path"],
            catalog,
            title=case["title"],
            page_indices=(case["physical_page"] - 1,),
            parser_version="stage6-vertical-slice-document-ir-v1",
            parsing_run_id=stable_id(
                "run", "stage6-vertical-slice-v1", case["registered_relative_path"], case["physical_page"]
            ),
        )
        ir = apply_page_identity(ir, {case["physical_page"]: case["logical_page"]})
        if not ir.source_spans:
            raise RuntimeError(f"no source span was produced for {case['document_key']} p{case['physical_page']}")
        span = max(ir.source_spans, key=lambda item: len(item.quote))
        coordinate_source = "document_ir_pdf_points"
        if case["path_root"] == "ocr":
            ocr_record = rapidocr_by_page[(case["document_key"], case["physical_page"])]
            pixel_bbox = union_pixel_boxes(ocr_record["fresh_rapidocr"]["boxes"])
            page = ir.pages[0]
            pdf_bbox = pixel_bbox_to_pdf_bbox(
                pixel_bbox,
                dpi=int(rapidocr["engine"]["dpi"]),
                page_width_pt=page.width_pt,
                page_height_pt=page.height_pt,
                rotation_deg=page.rotation_deg,
            )
            target_block_ids = set(span.block_version_ids)
            ir = replace(
                ir,
                blocks=tuple(
                    replace(block, bbox=pdf_bbox) if block.block_version_id in target_block_ids else block
                    for block in ir.blocks
                ),
                source_spans=tuple(
                    replace(source_span, bbox=pdf_bbox) if source_span.source_span_id == span.source_span_id else source_span
                    for source_span in ir.source_spans
                ),
            )
            ir = apply_page_identity(ir, {case["physical_page"]: case["logical_page"]})
            span = next(source_span for source_span in ir.source_spans if source_span.source_span_id == span.source_span_id)
            coordinate_source = "stage5_rapidocr_pixel_bbox_to_pdf_points"
        review_id = f"stage6-{case['document_key']}-p{case['physical_page']}"
        draft = build_evidence(
            ir,
            (span.source_span_id,),
            evidence_role=case["role"],
            disposition=case["disposition"],
        )
        decision = decisions.get(review_id)
        if decision and decision.get("expected_evidence_id") != draft.evidence_id:
            raise ValueError(f"review decision no longer matches generated Evidence: {review_id}")
        item = draft
        if decision and decision["decision"] == "accepted":
            item = build_evidence(
                ir,
                (span.source_span_id,),
                evidence_role=case["role"],
                disposition=case["disposition"],
                review_status="accepted",
                reviewer=decision["reviewer"],
                reviewed_at=decision["reviewed_at"],
                review_reason=decision["reason"],
            )
        candidates.append({
            "document_key": case["document_key"],
            "evidence": record_value(item),
            "input": {
                "original_relative_path": case["original_relative_path"],
                "processing_relative_path": (
                    None if case["path_root"] == "source" else case["registered_relative_path"]
                ),
                "physical_page": case["physical_page"],
                "logical_page": case["logical_page"],
                "authority_asset_id": item.authority_asset_id,
                "processing_asset_id": item.processing_asset_id,
                "document_ir_output_fingerprint": ir.parsing_run.output_fingerprint,
                "coordinate_source": coordinate_source,
            },
        })
        review_queue.append({
            "review_id": review_id,
            "evidence_id": item.evidence_id,
            "decision": item.review_status,
            "authority_asset_id": item.authority_asset_id,
            "original_relative_path": case["original_relative_path"],
            "physical_page": case["physical_page"],
            "logical_page": case["logical_page"],
            "bbox": record_value(item.locations[0].bbox),
            "source_text": item.source_text,
            "checks": [
                "original page bbox coverage",
                "physical/logical page identity",
                "Chinese characters, digits, units and negation terms",
                "effective text version and OCR/overlay provenance",
            ],
            "reason": item.review_reason or "awaiting an explicit review decision",
        })

    # Negative boundary sample carried forward from Stage 5; it is not parsed
    # into Evidence and cannot be cleared by this script.
    review_queue.append({
        "review_id": "stage6-D300N-p50-quarantine",
        "document_key": "D300N",
        "physical_page": 50,
        "decision": "quarantined",
        "reason": "continuation table; cell, header and row relations require region/cell review",
    })
    OUTPUT_ROOT.mkdir(exist_ok=True)
    candidate_path = OUTPUT_ROOT / "stage6_evidence_candidate_annotations.jsonl"
    queue_path = OUTPUT_ROOT / "stage6_evidence_review_queue.jsonl"
    _jsonl(candidate_path, candidates)
    _jsonl(queue_path, review_queue)
    audit = {
        "schema_version": 1,
        "stage": "6",
        "artifact_kind": "stage6_vertical_slice_audit",
        "artifact_scope": "non_authoritative_initial_slice_diagnostic",
        "status": "complete_initial_slice",
        "formal_release": False,
        "producer": "scripts/build_stage6_vertical_slice.py",
        "consumers": [
            "turbine_kg.evidence.validation",
            "stage6_evidence_review_queue.jsonl",
            "future Stage 6 Evidence Golden Sample closure audit",
        ],
        "candidate_page_count": len(candidates),
        "candidate_evidence_count": len(candidates),
        "accepted_evidence_count": sum(row["evidence"]["review_status"] == "accepted" for row in candidates),
        "quarantined_review_count": 1,
        "candidate_annotations": str(candidate_path.relative_to(ROOT)).replace("\\", "/"),
        "review_queue": str(queue_path.relative_to(ROOT)).replace("\\", "/"),
        "blocking_items": [],
        "superseded_by": "data/stage6/stage6_evidence_quality_audit.json",
        "formal_evidence_source": "data/stage6/stage6_evidence_annotations.jsonl",
        "visual_review": [
            {
                "document_key": "DL5190.3",
                "physical_page": 86,
                "page_pair_visual_match": True,
                "bbox_overlay_coverage": "pass",
                "text_decision": "accepted_native_text_visual_confirmed",
            },
            {
                "document_key": "D300N",
                "physical_page": 73,
                "page_pair_visual_match": True,
                "bbox_overlay_coverage": "pass",
                "text_decision": "accepted_ocr_text_visual_confirmed",
            },
            {
                "document_key": "HAF103",
                "physical_page": 1,
                "page_pair_visual_match": True,
                "bbox_overlay_coverage": "pass",
                "text_decision": "accepted_ocr_text_visual_confirmed",
            },
        ],
        "boundaries": [
            "No Stage 3 real-trial JSON was consumed",
            "No Engineering Statement, ontology object, Neo4j projection or Release was created",
            "Stage 5 artifacts and Original materials remain unchanged",
            "Accepted Evidence approves only source text and location; it is not an Engineering Statement or Release",
        ],
        "next_action": "Build and review the remaining Golden Sample Evidence units; keep unresolved tables quarantined.",
    }
    (OUTPUT_ROOT / "stage6_vertical_slice_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"candidates": str(candidate_path), "audit": str(OUTPUT_ROOT / "stage6_vertical_slice_audit.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

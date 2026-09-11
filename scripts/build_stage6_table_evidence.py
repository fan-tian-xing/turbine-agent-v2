"""Build region-scoped Evidence from reviewed Stage 6 table regions."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pymupdf

from turbine_kg.documents.catalog import load_identity_catalog
from turbine_kg.documents.ids import stable_id
from turbine_kg.documents.models import record_value
from turbine_kg.documents.page_identity import apply_page_identity
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.documents.table_review import apply_reviewed_table_regions
from turbine_kg.evidence import TableContext, build_evidence
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
STAGE6 = ROOT / "data" / "stage6"
DECISIONS = STAGE6 / "stage6_table_review_decisions.jsonl"
ANNOTATIONS = STAGE6 / "stage6_table_evidence_annotations.jsonl"
AUDIT = STAGE6 / "stage6_table_evidence_audit.json"

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
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    settings = Settings.from_environment()
    catalog = load_identity_catalog(
        ROOT / "data/registry/source_assets.jsonl",
        ROOT / "config/revision_identity.tsv",
        ROOT / "config/derived_asset_links.tsv",
    )
    decisions = [
        json.loads(line) for line in DECISIONS.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if len(decisions) != len({row["review_id"] for row in decisions}):
        raise ValueError("duplicate Stage 6 table review IDs")
    if any(row["decision"] != "accepted_region_scoped" for row in decisions):
        raise ValueError("unaccepted table review decision cannot build Evidence")

    by_page: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in decisions:
        by_page[(row["document_key"], int(row["physical_page"]))].append(row)

    annotations: list[dict] = []
    page_results = []
    for (document_key, physical_page), regions in sorted(by_page.items()):
        document = DOCUMENTS[document_key]
        processing = document["processing"]
        pdf_path = (
            settings.source_root / document["original"]
            if processing is None
            else settings.ocr_derived_root / processing
        )
        ir = parse_registered_pdf(
            pdf_path,
            document["registered"],
            catalog,
            title=document["title"],
            page_indices=(physical_page - 1,),
            parser_version="stage6-reviewed-table-region-document-ir-v1",
            parsing_run_id=stable_id("run", "stage6-reviewed-table-regions-v1", document["registered"], physical_page),
        )
        ir = apply_page_identity(ir, {physical_page: regions[0]["logical_page"]})
        original_path = settings.source_root / document["original"]
        with pymupdf.open(original_path) as original_pdf:
            original_page = original_pdf[physical_page - 1]
            original_width = float(original_page.rect.width)
            original_height = float(original_page.rect.height)
            original_rotation = int(original_page.rotation or 0)
        if abs(original_width - ir.pages[0].width_pt) > 0.5 or abs(original_height - ir.pages[0].height_pt) > 0.5:
            raise ValueError(f"processing/original displayed dimensions are not aligned: {document_key} p{physical_page}")
        if any(int(row["authority_rotation_deg"]) != original_rotation for row in regions):
            raise ValueError(f"reviewed authority rotation differs from Original materials: {document_key} p{physical_page}")
        for row in regions:
            bbox = row["bbox"]
            if not (
                0 <= float(bbox["x0"]) <= float(bbox["x1"]) <= original_width
                and 0 <= float(bbox["y0"]) <= float(bbox["y1"]) <= original_height
            ):
                raise ValueError(f"reviewed table bbox falls outside Original materials: {row['review_id']}")
        ir = apply_reviewed_table_regions(ir, regions)
        new_tables = ir.tables[-len(regions):]
        new_spans = ir.source_spans[-len(regions):]

        for decision, table, span in zip(regions, new_tables, new_spans):
            transform = (
                "document_ir_canonical_pdf_points_v1"
                if int(decision["authority_rotation_deg"]) == ir.pages[0].rotation_deg
                else "aligned_display_pdf_points_with_explicit_authority_rotation_v1"
            )
            context = TableContext(
                table_id=table.table_id,
                row_indices=(),
                column_indices=(),
                header_cell_ids=table.cell_ids,
                value_cell_ids=(),
                table_label=decision["table_label"],
                continuation_from_physical_page=decision["continuation_from_physical_page"],
                continuation_to_physical_page=decision["continuation_to_physical_page"],
                inherited_header_text=tuple(decision["inherited_header_text"]),
                review_scope="table_region",
                leaf_column_count=len(decision["leaf_headers"]),
                header_hierarchy=tuple(decision["header_hierarchy"]),
                partition_note=decision["partition_note"],
            )
            evidence = build_evidence(
                ir,
                (span.source_span_id,),
                evidence_role=document["role"],
                disposition="region_scoped",
                review_status="accepted",
                reviewer=decision["reviewer"],
                reviewed_at=decision["reviewed_at"],
                review_reason=decision["reason"],
                authority_rotation_deg=int(decision["authority_rotation_deg"]),
                coordinate_transform=transform,
                table_context=(context,),
            )
            annotations.append({
                "review_id": decision["review_id"],
                "document_key": document_key,
                "evidence": record_value(evidence),
                "input": {
                    "original_relative_path": document["original"],
                    "processing_relative_path": processing,
                    "physical_page": physical_page,
                    "logical_page": decision["logical_page"],
                    "authority_asset_id": evidence.authority_asset_id,
                    "processing_asset_id": evidence.processing_asset_id,
                    "authority_rotation_deg": decision["authority_rotation_deg"],
                    "coordinate_transform": transform,
                    "document_ir_output_fingerprint": ir.parsing_run.output_fingerprint,
                },
            })
        page_results.append({
            "document_key": document_key,
            "physical_page": physical_page,
            "logical_page": regions[0]["logical_page"],
            "decision": "accepted_region_scoped",
            "table_region_count": len(regions),
            "review_ids": [row["review_id"] for row in regions],
        })

    _jsonl(ANNOTATIONS, annotations)
    audit = {
        "schema_version": 1,
        "stage": "6",
        "artifact_kind": "stage6_table_evidence_audit",
        "status": "complete",
        "formal_release": False,
        "authority": "Original materials original PDF pages; OCR is processing assistance only",
        "reviewed_page_count": len(page_results),
        "accepted_page_count": len(page_results),
        "table_region_count": len(annotations),
        "accepted_evidence_count": len(annotations),
        "remaining_quarantined_page_count": 0,
        "structured_data_cell_count": 0,
        "disposition": "All reviewed tables are accepted only as region-scoped Evidence; unreviewed data cells remain unavailable for structured claims.",
        "annotations": str(ANNOTATIONS.relative_to(ROOT)).replace("\\", "/"),
        "review_decisions": str(DECISIONS.relative_to(ROOT)).replace("\\", "/"),
        "page_results": page_results,
    }
    AUDIT.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pages": len(page_results), "regions": len(annotations), "status": "complete"}))


if __name__ == "__main__":
    main()

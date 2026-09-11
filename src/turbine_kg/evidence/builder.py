"""Deterministic construction of Stage 6 Evidence from validated Document IR."""

from __future__ import annotations

import hashlib

from turbine_kg.documents.models import DocumentIR
from turbine_kg.documents.validation import validate_document_ir
from turbine_kg.documents.ids import evidence_id, evidence_version_id

from .models import Evidence, EvidenceBundle, EvidenceLocation, FigureContext, TableContext
from .validation import validate_evidence_bundle


def build_evidence(
    ir: DocumentIR,
    source_span_ids: tuple[str, ...],
    *,
    evidence_role: str,
    disposition: str = "structured",
    review_status: str = "needs_review",
    reviewer: str | None = None,
    reviewed_at: str | None = None,
    review_reason: str | None = None,
    effective_text: str | None = None,
    effective_text_origin: str | None = None,
    authority_basis: str = "original_pdf_visual_review",
    authority_rotation_deg: int | None = None,
    coordinate_transform: str = "document_ir_canonical_pdf_points_v1",
    correction_ids: tuple[str, ...] = (),
    table_context: tuple[TableContext, ...] = (),
    figure_context: tuple[FigureContext, ...] = (),
) -> Evidence:
    """Build one Evidence item while keeping SourceSpan as text authority."""

    validate_document_ir(ir)
    spans = {span.source_span_id: span for span in ir.source_spans}
    if not source_span_ids or any(span_id not in spans for span_id in source_span_ids):
        raise ValueError("Evidence requires existing SourceSpan IDs")
    selected = [spans[span_id] for span_id in source_span_ids]
    pages = {page.page_id: page for page in ir.pages}
    source_text = "\n".join(span.quote for span in selected)
    content_kinds = {span.content_kind for span in selected}
    text_origins = {span.text_origin for span in selected}
    asset_by_id = {asset.asset_id: asset for asset in ir.assets}
    input_assets = set(ir.parsing_run.input_asset_ids)
    location_rows = []
    for span in selected:
        page = pages[span.page_id]
        processing_ref = next(
            (ref for ref in page.asset_page_refs if ref.asset_id in input_assets),
            page.asset_page_refs[0],
        )
        original_ref = next(
            ref for ref in page.asset_page_refs if asset_by_id[ref.asset_id].asset_kind == "original"
        )
        location_rows.append(EvidenceLocation(
            source_span_id=span.source_span_id,
            processing_asset_id=processing_ref.asset_id,
            original_asset_id=original_ref.asset_id,
            physical_page=page.display_page_number,
            logical_page=page.printed_page_label,
            bbox=span.bbox,
            rotation_deg=page.rotation_deg,
            authority_rotation_deg=page.rotation_deg if authority_rotation_deg is None else authority_rotation_deg,
            coordinate_transform=coordinate_transform,
        ))
    locations = tuple(location_rows)
    original_asset_ids = {location.original_asset_id for location in locations}
    processing_asset_ids = {location.processing_asset_id for location in locations}
    if len(original_asset_ids) != 1 or len(processing_asset_ids) != 1:
        raise ValueError("one Evidence item cannot cross original or processing assets")
    physical_pages = tuple(location.physical_page for location in locations)
    bboxes = tuple(location.bbox for location in locations)
    output_fingerprint = ir.parsing_run.output_fingerprint or ""
    item = Evidence(
        evidence_id=evidence_id(ir.revision.revision_id, physical_pages, bboxes, source_text, evidence_role),
        evidence_version_id=evidence_version_id(output_fingerprint, source_span_ids),
        document_logical_id=ir.document.document_logical_id,
        revision_id=ir.revision.revision_id,
        parsing_run_id=ir.parsing_run.parsing_run_id,
        authority_asset_id=locations[0].original_asset_id,
        processing_asset_id=(
            locations[0].processing_asset_id
            if locations[0].processing_asset_id != locations[0].original_asset_id else None
        ),
        authority_basis=authority_basis,
        source_span_ids=source_span_ids,
        source_text=source_text,
        source_text_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        evidence_role=evidence_role,
        content_kind=next(iter(content_kinds)) if len(content_kinds) == 1 else "mixed",
        text_origin=next(iter(text_origins)) if len(text_origins) == 1 else "mixed",
        effective_text=effective_text or source_text,
        effective_text_origin=effective_text_origin or (next(iter(text_origins)) if len(text_origins) == 1 else "mixed"),
        locations=locations,
        disposition=disposition,
        review_status=review_status,
        reviewer=reviewer,
        reviewed_at=reviewed_at,
        review_reason=review_reason,
        correction_ids=correction_ids,
        table_context=table_context,
        figure_context=figure_context,
    )
    bundle = EvidenceBundle(
        schema_version=1,
        revision_id=ir.revision.revision_id,
        evidence=(item,),
        source_ir_output_fingerprint=output_fingerprint,
    )
    validate_evidence_bundle(bundle, ir)
    return item

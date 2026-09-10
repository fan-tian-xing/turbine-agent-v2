"""Explicit structural compatibility from Stage 4 Document IR to Stage 3.

This adapter exposes only the old Page/SourceSpan shape.  It deliberately
does not manufacture Stage 3 LogicalDocument scope, Evidence, or statements;
those remain semantic inputs and must be supplied and validated separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from turbine_kg.documents.catalog import IdentityCatalog
from turbine_kg.documents.models import DocumentIR
from turbine_kg.documents.pdf import parse_registered_pdf
from turbine_kg.documents.profiles import LayoutProfile
from turbine_kg.documents.validation import validate_document_ir

from .models import Page, Revision, SourceSpan


@dataclass(frozen=True, slots=True)
class Stage3StructuralView:
    revision: Revision
    pages: tuple[Page, ...]
    spans: tuple[SourceSpan, ...]


def project_document_ir(ir: DocumentIR) -> Stage3StructuralView:
    """Project validated IR structure into the stable Stage 3 page contract."""

    validate_document_ir(ir)
    blocks_by_page: dict[str, list] = {}
    for block in ir.blocks:
        blocks_by_page.setdefault(block.page_id, []).append(block)
    for blocks in blocks_by_page.values():
        blocks.sort(key=lambda block: (block.reading_order, block.block_ordinal))

    pages = tuple(
        Page(
            page_id=page.page_id,
            revision_id=page.revision_id,
            page_number=page.display_page_number,
            text="\n".join(block.text for block in blocks_by_page.get(page.page_id, []) if block.text),
            logical_page=page.printed_page_label,
        )
        for page in sorted(ir.pages, key=lambda item: item.pdf_page_index)
    )
    spans = tuple(
        SourceSpan(
            span_id=span.source_span_id,
            page_id=span.page_id,
            quote=span.quote,
            # Stage 3 accepts only its legacy text/table distinction.  Keep
            # richer IR block kinds in Document IR rather than leaking them
            # into the older semantic fixture contract.
            content_kind="table" if span.content_kind == "table" else "text",
            table_metadata=(
                {"table_id": span.table_id, "row_index": span.row_index, "column_index": span.column_index}
                if span.table_id is not None
                else None
            ),
        )
        for span in ir.source_spans
    )
    return Stage3StructuralView(
        revision=Revision(
            revision_id=ir.revision.revision_id,
            document_logical_id=ir.revision.document_logical_id,
            label=ir.revision.revision_label,
        ),
        pages=pages,
        spans=spans,
    )


def project_registered_pdf(
    path: Path,
    relative_path: str,
    catalog: IdentityCatalog,
    *,
    title: str,
    profile: LayoutProfile = LayoutProfile(),
    page_indices: tuple[int, ...] | None = None,
) -> Stage3StructuralView:
    """Run the read-only Registry → PDF → IR → Stage 3 structure bridge."""

    return project_document_ir(
        parse_registered_pdf(
            path,
            relative_path,
            catalog,
            title=title,
            profile=profile,
            page_indices=page_indices,
        )
    )

"""Apply an explicit, reviewed printed-page mapping to Document IR."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from .models import DocumentIR, record_value
from .validation import validate_document_ir


def apply_page_identity(ir: DocumentIR, labels_by_physical_page: dict[int, str | None]) -> DocumentIR:
    """Add only supplied logical labels; never infer them from physical pages."""

    validate_document_ir(ir)
    pages = tuple(
        replace(page, printed_page_label=labels_by_physical_page[page.display_page_number])
        if page.display_page_number in labels_by_physical_page else page
        for page in ir.pages
    )
    payload = {
        "pages": [record_value(page) for page in pages],
        "blocks": [record_value(block) for block in ir.blocks],
        "source_spans": [record_value(span) for span in ir.source_spans],
        "figures": [record_value(figure) for figure in ir.figures],
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return validate_document_ir(
        replace(ir, pages=pages, parsing_run=replace(ir.parsing_run, output_fingerprint=fingerprint))
    )

"""Small page-level parser facade used by the Stage 4 IR contract tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from .ids import new_parsing_run_id
from .models import AssetRef, Document, DocumentIR, DocumentRevision, Page, ParsingRun, record_value
from .profiles import LayoutProfile, PageInput, adapter_for_mode, choose_page_mode, inspect_page
from .validation import validate_document_ir


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_page_inputs(
    document: Document,
    revision: DocumentRevision,
    assets: tuple[AssetRef, ...],
    page_inputs: tuple[PageInput, ...],
    *,
    profile: LayoutProfile = LayoutProfile(),
    parser_version: str = "document-ir-parser-v1",
    config_fingerprint: str = "layout-profile-adaptive-pdf-v1",
    parsing_run_id: str | None = None,
) -> DocumentIR:
    """Normalize page inputs from native, scan or mixed sources into one IR.

    This is deliberately not an OCR engine.  Scan-only pages enter the IR as
    ``ocr_required`` and produce no text blocks until Stage 5 selects and
    validates an OCR pipeline.
    """
    if not page_inputs:
        raise ValueError("at least one page input is required")
    run_id = parsing_run_id or new_parsing_run_id()
    asset_ids = tuple(asset.asset_id for asset in assets)
    revision_ids = (revision.revision_id,)
    run = ParsingRun(
        parsing_run_id=run_id,
        input_asset_ids=asset_ids,
        input_revision_ids=revision_ids,
        parser_profile_id=profile.profile_id,
        parser_version=parser_version,
        config_fingerprint=config_fingerprint,
        input_fingerprint=_fingerprint([record_value(item) for item in page_inputs]),
    )
    pages: list[Page] = []
    blocks = []
    spans = []
    for page_input in page_inputs:
        if page_input.revision_id != revision.revision_id:
            raise ValueError("page input revision does not match the requested IR revision")
        mode = choose_page_mode(inspect_page(page_input), profile)
        page, page_blocks, page_spans = adapter_for_mode(mode).parse(page_input, run_id, mode)
        pages.append(page)
        blocks.extend(page_blocks)
        spans.extend(page_spans)
    ir = DocumentIR(
        schema_version=1,
        document=document,
        revision=revision,
        assets=assets,
        parsing_run=run,
        pages=tuple(pages),
        blocks=tuple(blocks),
        source_spans=tuple(spans),
    )
    output_fingerprint = _fingerprint({
        "pages": [record_value(page) for page in pages],
        "blocks": [record_value(block) for block in blocks],
        "source_spans": [record_value(span) for span in spans],
    })
    return validate_document_ir(replace(ir, parsing_run=replace(run, output_fingerprint=output_fingerprint)))

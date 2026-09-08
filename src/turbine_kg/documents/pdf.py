"""PDF-to-Document-IR entry point without committing to an OCR engine."""

from __future__ import annotations

from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover - compatibility with older PyMuPDF imports
    import fitz as pymupdf

from .models import AssetRef, Document, DocumentIR, DocumentRevision, BBox
from .parser import parse_page_inputs
from .profiles import LayoutProfile, PageInput, RawTextBlock


def _image_coverage(page) -> float:
    page_area = max(float(page.rect.width * page.rect.height), 1.0)
    try:
        image_info = page.get_image_info(xrefs=True)
    except AttributeError:
        image_info = []
    covered = 0.0
    for item in image_info:
        bbox = item.get("bbox")
        if bbox:
            covered += max(0.0, float(bbox[2] - bbox[0])) * max(0.0, float(bbox[3] - bbox[1]))
    return min(1.0, covered / page_area)


def _native_blocks(page) -> tuple[RawTextBlock, ...]:
    blocks: list[RawTextBlock] = []
    for raw in page.get_text("blocks"):
        if len(raw) < 5 or not str(raw[4]).strip():
            continue
        blocks.append(RawTextBlock(
            text=str(raw[4]).strip(),
            bbox=BBox(float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])),
            block_type="paragraph",
            reading_order=len(blocks),
        ))
    return tuple(blocks)


def parse_pdf(
    path: Path,
    document: Document,
    revision: DocumentRevision,
    asset: AssetRef,
    *,
    profile: LayoutProfile = LayoutProfile(),
    parser_version: str = "pymupdf-page-inspector-v1",
    page_indices: tuple[int, ...] | None = None,
) -> DocumentIR:
    """Inspect and normalize PDF pages.

    Native text is preserved when available.  Scan-only pages are represented
    as ``ocr_required`` without inventing text; OCR selection belongs to Stage 5.
    """
    if not path.is_file():
        raise FileNotFoundError(path)
    inputs: list[PageInput] = []
    with pymupdf.open(path) as pdf:
        selected = range(len(pdf)) if page_indices is None else page_indices
        for index in selected:
            page = pdf[index]
            text = page.get_text("text").strip()
            images = _image_coverage(page)
            inputs.append(PageInput(
                asset_id=asset.asset_id,
                revision_id=revision.revision_id,
                pdf_page_index=index,
                width_pt=float(page.rect.width),
                height_pt=float(page.rect.height),
                rotation_deg=int(page.rotation or 0),
                text=text,
                text_layer_status="native" if text else "scan_only",
                image_coverage=images,
                text_blocks=_native_blocks(page),
            ))
    return parse_page_inputs(
        document,
        revision,
        (asset,),
        tuple(inputs),
        profile=profile,
        parser_version=parser_version,
    )

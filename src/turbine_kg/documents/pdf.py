"""PDF-to-Document-IR entry point without committing to an OCR engine."""

from __future__ import annotations

from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover - compatibility with older PyMuPDF imports
    import fitz as pymupdf

from .catalog import IdentityCatalog
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


def _clip_bbox(raw_bbox, page) -> BBox:
    width = float(page.rect.width)
    height = float(page.rect.height)
    return BBox(
        max(0.0, min(width, float(raw_bbox[0]))),
        max(0.0, min(height, float(raw_bbox[1]))),
        max(0.0, min(width, float(raw_bbox[2]))),
        max(0.0, min(height, float(raw_bbox[3]))),
    )


def _native_blocks(page) -> tuple[RawTextBlock, ...]:
    blocks: list[RawTextBlock] = []
    for raw in page.get_text("blocks"):
        if len(raw) < 5 or not str(raw[4]).strip():
            continue
        blocks.append(RawTextBlock(
            text=str(raw[4]).strip(),
            bbox=_clip_bbox(raw[:4], page),
            block_type="paragraph",
            reading_order=len(blocks),
        ))
    return tuple(blocks)


def _image_boxes(page) -> tuple[BBox, ...]:
    try:
        image_info = page.get_image_info(xrefs=True)
    except AttributeError:
        return ()
    return tuple(
        _clip_bbox(item["bbox"], page)
        for item in image_info
        if item.get("bbox")
    )


def parse_pdf(
    path: Path,
    document: Document,
    revision: DocumentRevision,
    asset: AssetRef,
    *,
    profile: LayoutProfile = LayoutProfile(),
    parser_version: str = "pymupdf-page-inspector-v1",
    page_indices: tuple[int, ...] | None = None,
    additional_assets: tuple[AssetRef, ...] = (),
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
                text_layer_status=("ocr" if asset.asset_kind == "derived_ocr" else "native") if text else "scan_only",
                image_coverage=images,
                text_blocks=_native_blocks(page),
                image_boxes=_image_boxes(page),
            ))
    return parse_page_inputs(
        document,
        revision,
        (asset, *additional_assets),
        tuple(inputs),
        profile=profile,
        parser_version=parser_version,
    )


def parse_registered_pdf(
    path: Path,
    relative_path: str,
    catalog: IdentityCatalog,
    *,
    title: str,
    profile: LayoutProfile = LayoutProfile(),
    parser_version: str = "pymupdf-page-inspector-v1",
    page_indices: tuple[int, ...] | None = None,
) -> DocumentIR:
    """Parse one Registry-registered PDF without re-inferring its identity.

    The caller must provide the Registry relative path and an explicit title;
    the path is used only to look up a stable Asset identity, never to create
    a new document or Revision.  This is the Stage 4 bridge used by future
    batch entry points and by the Stage 3 structural adapter.
    """

    asset_identity = catalog.asset_for_path(relative_path)
    revision_record = catalog.revision_for_id(asset_identity.revision_id)
    def to_asset_ref(identity):
        return AssetRef(
            asset_id=identity.asset_id,
            document_logical_id=identity.document_logical_id,
            revision_id=identity.revision_id,
            asset_kind=identity.asset_kind,
            relative_path=identity.relative_path,
            sha256=identity.sha256,
            source_root_id=identity.source_root_id,
            derived_from_asset_id=identity.derived_from_asset_id,
            derivation_type=identity.derivation_type,
        )

    asset = to_asset_ref(asset_identity)
    additional_assets = ()
    if asset_identity.derived_from_asset_id:
        additional_assets = (to_asset_ref(catalog.asset_for_id(asset_identity.derived_from_asset_id)),)
    document = Document(
        document_logical_id=asset_identity.document_logical_id,
        title=title,
        registry_document_ref=asset_identity.document_logical_id,
    )
    revision = DocumentRevision(
        revision_id=revision_record.revision_id,
        document_logical_id=revision_record.document_logical_id,
        revision_label=revision_record.revision_label,
        revision_basis=revision_record.revision_basis,
        status=revision_record.revision_status,
    )
    return parse_pdf(
        path,
        document,
        revision,
        asset,
        profile=profile,
        parser_version=parser_version,
        page_indices=page_indices,
        additional_assets=additional_assets,
    )

"""Page-capability routing and input adapters for Stage 4."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Protocol

from .ids import block_version_id, figure_id, page_id, source_span_id
from .models import AssetPageRef, BBox, BlockVersion, Figure, Page, SourceSpan


@dataclass(frozen=True, slots=True)
class RawTextBlock:
    text: str
    bbox: BBox | None = None
    block_type: str = "paragraph"
    reading_order: int = 0


@dataclass(frozen=True, slots=True)
class PageInput:
    asset_id: str
    revision_id: str
    pdf_page_index: int
    width_pt: float
    height_pt: float
    rotation_deg: int
    text: str
    text_layer_status: str
    image_coverage: float
    printed_page_label: str | None = None
    visual_fingerprint: str | None = None
    text_blocks: tuple[RawTextBlock, ...] = ()
    image_boxes: tuple[BBox, ...] = ()


@dataclass(frozen=True, slots=True)
class PageCapability:
    has_extractable_text: bool
    text_character_count: int
    image_coverage: float
    rotation_deg: int
    text_block_count: int


@dataclass(frozen=True, slots=True)
class LayoutProfile:
    profile_id: str = "adaptive_pdf_v1"
    native_min_characters: int = 30
    scan_max_characters: int = 5
    scan_min_image_coverage: float = 0.65


def load_layout_profile(path: Path, profile_id: str = "adaptive_pdf_v1") -> LayoutProfile:
    payload = json.loads(path.read_text(encoding="utf-8"))
    item = payload.get("profiles", {}).get(profile_id)
    if not isinstance(item, dict):
        raise ValueError(f"layout profile is missing: {profile_id}")
    return LayoutProfile(
        profile_id=profile_id,
        native_min_characters=int(item["native_min_characters"]),
        scan_max_characters=int(item["scan_max_characters"]),
        scan_min_image_coverage=float(item["scan_min_image_coverage"]),
    )


class PageAdapter(Protocol):
    adapter_id: str

    def parse(self, page_input: PageInput, parsing_run_id: str, mode: str) -> tuple[Page, tuple[BlockVersion, ...], tuple[SourceSpan, ...], tuple[Figure, ...]]:
        ...


def inspect_page(page_input: PageInput) -> PageCapability:
    return PageCapability(
        has_extractable_text=bool(page_input.text.strip()),
        text_character_count=len(page_input.text.strip()),
        image_coverage=page_input.image_coverage,
        rotation_deg=page_input.rotation_deg,
        text_block_count=len(page_input.text_blocks),
    )


def choose_page_mode(capability: PageCapability, profile: LayoutProfile = LayoutProfile()) -> str:
    if capability.text_character_count >= profile.native_min_characters and capability.image_coverage < profile.scan_min_image_coverage:
        return "native_text"
    if capability.text_character_count <= profile.scan_max_characters and capability.image_coverage >= profile.scan_min_image_coverage:
        return "scan_only"
    if capability.has_extractable_text and capability.image_coverage >= profile.scan_min_image_coverage:
        return "mixed"
    return "review_required"


def _page(page_input: PageInput, mode: str) -> Page:
    return Page(
        page_id=page_id(page_input.revision_id, page_input.pdf_page_index),
        revision_id=page_input.revision_id,
        pdf_page_index=page_input.pdf_page_index,
        display_page_number=page_input.pdf_page_index + 1,
        printed_page_label=page_input.printed_page_label,
        width_pt=page_input.width_pt,
        height_pt=page_input.height_pt,
        rotation_deg=page_input.rotation_deg,
        page_mode=mode,
        text_layer_status=page_input.text_layer_status,
        visual_fingerprint=page_input.visual_fingerprint,
        asset_page_refs=(AssetPageRef(page_input.asset_id, page_input.pdf_page_index),),
    )


def _text_outputs(page_input: PageInput, parsing_run_id: str, page: Page, mode: str) -> tuple[tuple[BlockVersion, ...], tuple[SourceSpan, ...], tuple[Figure, ...]]:
    if not page_input.text.strip() or mode == "scan_only":
        return (), (), ()
    raw_blocks = page_input.text_blocks or (RawTextBlock(page_input.text, BBox(0, 0, page.width_pt, page.height_pt)),)
    blocks: list[BlockVersion] = []
    spans: list[SourceSpan] = []
    figures: list[Figure] = []
    origin = "ocr_text" if page_input.text_layer_status == "ocr" else "native_text"
    for ordinal, raw in enumerate(raw_blocks):
        block_id = block_version_id(parsing_run_id, page.page_id, ordinal)
        blocks.append(BlockVersion(
            block_version_id=block_id,
            page_id=page.page_id,
            parsing_run_id=parsing_run_id,
            block_ordinal=ordinal,
            block_type=raw.block_type,
            text=raw.text,
            bbox=raw.bbox,
            reading_order=raw.reading_order or ordinal,
            text_origin=origin,
        ))
        spans.append(SourceSpan(
            source_span_id=source_span_id(block_id, 0, raw.text),
            page_id=page.page_id,
            block_version_ids=(block_id,),
            quote=raw.text,
            content_kind=raw.block_type,
            char_start=0,
            char_end=len(raw.text),
            bbox=raw.bbox,
            text_origin=origin,
        ))
    for bbox in page_input.image_boxes:
        ordinal = len(blocks)
        block_id = block_version_id(parsing_run_id, page.page_id, ordinal)
        blocks.append(BlockVersion(
            block_version_id=block_id,
            page_id=page.page_id,
            parsing_run_id=parsing_run_id,
            block_ordinal=ordinal,
            block_type="image",
            text="",
            bbox=bbox,
            reading_order=ordinal,
            text_origin=origin,
        ))
        figures.append(Figure(
            figure_id=figure_id(block_id),
            block_version_id=block_id,
            caption_block_version_id=None,
            figure_label=None,
        ))
    return tuple(blocks), tuple(spans), tuple(figures)


class NativeTextAdapter:
    adapter_id = "native_text_v1"

    def parse(self, page_input: PageInput, parsing_run_id: str, mode: str):
        page = _page(page_input, mode)
        blocks, spans, figures = _text_outputs(page_input, parsing_run_id, page, mode)
        return page, blocks, spans, figures


class ScanPendingAdapter:
    adapter_id = "scan_pending_v1"

    def parse(self, page_input: PageInput, parsing_run_id: str, mode: str):
        return _page(page_input, "scan_only"), (), (), ()


class MixedTextAdapter:
    adapter_id = "mixed_text_v1"

    def parse(self, page_input: PageInput, parsing_run_id: str, mode: str):
        page = _page(page_input, "mixed")
        blocks, spans, figures = _text_outputs(page_input, parsing_run_id, page, mode)
        return page, blocks, spans, figures


class ReviewRequiredAdapter:
    adapter_id = "review_required_v1"

    def parse(self, page_input: PageInput, parsing_run_id: str, mode: str):
        return _page(page_input, "review_required"), (), (), ()


def adapter_for_mode(mode: str) -> PageAdapter:
    if mode == "native_text":
        return NativeTextAdapter()
    if mode == "scan_only":
        return ScanPendingAdapter()
    if mode == "mixed":
        return MixedTextAdapter()
    if mode == "review_required":
        return ReviewRequiredAdapter()
    raise ValueError(f"no adapter is available for page mode {mode}")

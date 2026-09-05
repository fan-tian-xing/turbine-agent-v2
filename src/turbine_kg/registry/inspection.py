"""Read-only PDF inspection and low-level fingerprints."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any

import pymupdf

from .identity import digest
from .source_inputs import sha256_file


IDENTIFIER_PATTERN = re.compile(
    r"\b(?:DL\s*[／/]\s*T|DLT|NB\s*[／/]\s*T|NBT|HAF|HAD)\s*[A-Z0-9０-９./／—–\-]*",
    flags=re.IGNORECASE,
)
YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", value).strip()


def page_fingerprint(page: pymupdf.Page) -> dict[str, Any]:
    text = page.get_text("text")
    normalized = normalize_text(text)
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(0.18, 0.18),
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )
    visual_hash = hashlib.sha256(pixmap.samples).hexdigest()
    return {
        "character_count": len(text),
        "text_fingerprint": digest(normalized),
        "visual_fingerprint": visual_hash,
        "rotation": page.rotation,
    }


def inspect_pdf(
    path: Path,
    relative_path: str,
    expected_size: int,
    expected_sha256: str,
) -> dict[str, Any]:
    actual_size = path.stat().st_size
    actual_sha256 = sha256_file(path)
    document = pymupdf.open(path)
    first_page_text = ""
    try:
        metadata = document.metadata or {}
        pages = []
        for page_number, page in enumerate(document):
            if page_number < 2:
                first_page_text += page.get_text("text")
            pages.append(page_fingerprint(page))
    finally:
        document.close()

    page_count = len(pages)
    character_counts = [page["character_count"] for page in pages]
    nonempty_pages = sum(count > 0 for count in character_counts)
    total_characters = sum(character_counts)
    average_characters = total_characters / page_count if page_count else 0
    nonempty_ratio = nonempty_pages / page_count if page_count else 0
    if nonempty_pages == 0:
        text_layer_status = "scan_only"
    elif nonempty_ratio < 0.5 or average_characters < 30:
        text_layer_status = "mixed_or_low_quality"
    else:
        text_layer_status = "native_text"

    visual_sequence = "|".join(page["visual_fingerprint"] for page in pages)
    text_sequence = "|".join(page["text_fingerprint"] for page in pages)
    flags: list[str] = []
    if text_layer_status != "native_text":
        flags.append("text_layer_requires_review")
    if any(page["rotation"] for page in pages):
        flags.append("rotated_pages")
    if page_count <= 11:
        flags.append("short_document")
    if page_count and character_counts[0] == 0:
        flags.append("cover_has_no_extractable_text")
    if actual_size != expected_size:
        flags.append("size_mismatch")
    if actual_sha256 != expected_sha256:
        flags.append("sha256_mismatch")

    metadata_title = str(metadata.get("title") or "").strip()
    title_candidate = metadata_title or Path(relative_path).stem
    identifier_candidates = sorted(
        {
            re.sub(r"\s+", " ", candidate).strip(" -")
            for candidate in IDENTIFIER_PATTERN.findall(normalize_text(first_page_text + " " + metadata_title))
            if candidate.strip(" -")
        }
    )
    year_candidates = sorted(set(YEAR_PATTERN.findall(first_page_text + " " + metadata_title)))
    if not first_page_text.strip():
        flags.append("cover_identity_requires_visual_review")

    return {
        "relative_path": relative_path,
        "file_name": path.name,
        "size_bytes": actual_size,
        "sha256": actual_sha256,
        "page_count": page_count,
        "total_text_characters": total_characters,
        "text_layer_status": text_layer_status,
        "rotated_page_count": sum(page["rotation"] != 0 for page in pages),
        "page_visual_fingerprints": [page["visual_fingerprint"] for page in pages],
        "page_text_fingerprints": [page["text_fingerprint"] for page in pages],
        "visual_content_fingerprint": digest(visual_sequence),
        "text_content_fingerprint": None if text_layer_status == "scan_only" else digest(text_sequence),
        "pdf_metadata": {
            key: value
            for key, value in metadata.items()
            if value and key in {"title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate"}
        },
        "title_candidate": title_candidate,
        "title_source": "pdf_metadata" if metadata_title else "file_name_only",
        "identifier_candidates": identifier_candidates,
        "year_candidates": year_candidates,
        "identity_status": "review_required",
        "completeness_status": "review_required",
        "external_processing_status": "not_assessed",
        "review_flags": flags,
    }

"""Stable and run-scoped identifiers for the Document IR."""

from __future__ import annotations

import hashlib
import uuid


def stable_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(payload).hexdigest()[:20]}"


def page_id(revision_id: str, pdf_page_index: int) -> str:
    return stable_id("page", revision_id, pdf_page_index)


def block_version_id(parsing_run_id: str, page_id_value: str, block_ordinal: int) -> str:
    return stable_id("blockv", parsing_run_id, page_id_value, block_ordinal)


def source_span_id(block_version_id_value: str, local_ordinal: int, locator: object) -> str:
    return stable_id("span", block_version_id_value, local_ordinal, locator)


def table_id(block_version_id_value: str) -> str:
    return stable_id("table", block_version_id_value)


def table_cell_id(table_id_value: str, row_index: int, column_index: int) -> str:
    return stable_id("cell", table_id_value, row_index, column_index)


def figure_id(block_version_id_value: str) -> str:
    return stable_id("figure", block_version_id_value)


def correction_id(block_version_id_value: str, reviewer: str, corrected_text: str) -> str:
    return stable_id("correction", block_version_id_value, reviewer, corrected_text)


def evidence_id(
    revision_id_value: str,
    physical_pages: tuple[int, ...],
    bboxes: tuple[object, ...],
    quote: str,
    role: str,
) -> str:
    """Stable content/location identity, independent of a parsing run."""

    return stable_id("evidence", revision_id_value, physical_pages, bboxes, quote, role)


def evidence_version_id(
    parsing_output_fingerprint: str,
    source_span_ids: tuple[str, ...],
) -> str:
    """Run/version identity kept separate from stable Evidence identity."""

    return stable_id("evver", parsing_output_fingerprint, *source_span_ids)


def new_parsing_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:20]}"

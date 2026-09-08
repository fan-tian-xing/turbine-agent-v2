"""Load and validate the public/synthetic Stage 3 fixture corpus."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .applicability import statement_scope_within_source
from .models import (
    ApplicabilityScope,
    Asset,
    Evidence,
    EngineeringStatement,
    FixtureDocument,
    LogicalDocument,
    Page,
    Revision,
    SourceSpan,
)


ID_PATTERNS = {
    "asset": re.compile(r"^asset-[0-9a-f]{20}$"),
    "document": re.compile(r"^doc-[0-9a-f]{20}$"),
    "revision": re.compile(r"^rev-[0-9a-f]{20}$"),
}
SOURCE_ROLES = {"manufacturer_manual", "standard_or_regulation", "training_background"}
STATEMENT_TYPES = {"fact", "conditioned_inference", "candidate_recommendation", "action_authorization"}
CONTENT_KINDS = {"text", "table"}


def _reject_unknown(raw: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown {label} fields: {sorted(unknown)}")


def _require_id(value: Any, kind: str) -> str:
    if not isinstance(value, str) or not ID_PATTERNS[kind].fullmatch(value):
        raise ValueError(f"{kind} ID does not match the Stage 3 contract: {value!r}")
    return value


def _ensure_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label} ID in Stage 3 fixture")


def _load_document(raw: dict[str, Any]) -> FixtureDocument:
    _reject_unknown(raw, {"document_logical_id", "source_role", "title", "asset", "revision", "source_scope", "pages", "spans", "evidence", "statements"}, "document")
    asset_raw = raw["asset"]
    revision_raw = raw["revision"]
    _reject_unknown(asset_raw, {"asset_id", "relative_path", "asset_kind", "source_root_id"}, "asset")
    _reject_unknown(revision_raw, {"revision_id", "document_logical_id", "label"}, "revision")
    document_id = _require_id(raw["document_logical_id"], "document")
    if raw["source_role"] not in SOURCE_ROLES:
        raise ValueError(f"unsupported Stage 3 source role: {raw['source_role']!r}")
    if revision_raw.get("document_logical_id") != document_id:
        raise ValueError(f"revision for {document_id} must explicitly reference its LogicalDocument")
    asset = Asset(
        asset_id=_require_id(asset_raw["asset_id"], "asset"),
        relative_path=asset_raw["relative_path"],
        asset_kind=asset_raw["asset_kind"],
        source_root_id=asset_raw["source_root_id"],
    )
    revision = Revision(
        revision_id=_require_id(revision_raw["revision_id"], "revision"),
        document_logical_id=document_id,
        label=revision_raw["label"],
    )
    for page in raw["pages"]:
        _reject_unknown(page, {"page_id", "revision_id", "page_number", "text"}, "Page")
    pages = tuple(
        Page(page_id=page["page_id"], revision_id=page["revision_id"], page_number=page["page_number"], text=page["text"])
        for page in raw["pages"]
    )
    _ensure_unique([page.page_id for page in pages], "Page")
    if any(page.revision_id != revision.revision_id for page in pages):
        raise ValueError(f"document {document_id} contains a Page for the wrong Revision")
    page_ids = {page.page_id for page in pages}
    for span in raw["spans"]:
        _reject_unknown(span, {"span_id", "page_id", "quote", "content_kind", "table_metadata"}, "SourceSpan")
        if span.get("content_kind", "text") not in CONTENT_KINDS:
            raise ValueError(f"unsupported SourceSpan content kind: {span.get('content_kind')!r}")
    spans = tuple(
        SourceSpan(
            span_id=span["span_id"],
            page_id=span["page_id"],
            quote=span["quote"],
            content_kind=span.get("content_kind", "text"),
            table_metadata=span.get("table_metadata"),
        )
        for span in raw["spans"]
    )
    _ensure_unique([span.span_id for span in spans], "SourceSpan")
    page_by_id = {page.page_id: page for page in pages}
    if any(span.page_id not in page_ids for span in spans):
        raise ValueError(f"document {document_id} contains a SourceSpan for an unknown Page")
    for span in spans:
        if " ".join(span.quote.split()) not in " ".join(page_by_id[span.page_id].text.split()):
            raise ValueError(f"SourceSpan {span.span_id} quote is not present in its Page text")
        if span.content_kind == "table" and not span.table_metadata:
            raise ValueError(f"table SourceSpan {span.span_id} requires table metadata")
    span_ids = {span.span_id for span in spans}
    for item in raw["evidence"]:
        _reject_unknown(item, {"evidence_id", "source_span_ids", "statement_id", "object_id", "text", "value", "unit", "quantities"}, "Evidence")
        if not item["source_span_ids"]:
            raise ValueError("Evidence must reference at least one SourceSpan")
    evidence = tuple(
        Evidence(
            evidence_id=item["evidence_id"],
            source_span_ids=tuple(item["source_span_ids"]),
            statement_id=item["statement_id"],
            object_id=item["object_id"],
            text=item["text"],
            value=item.get("value"),
            unit=item.get("unit"),
            quantities=tuple(
                (quantity.get("min"), quantity.get("max"), quantity["unit"])
                for quantity in item.get("quantities", [])
            ),
        )
        for item in raw["evidence"]
    )
    _ensure_unique([item.evidence_id for item in evidence], "Evidence")
    if any(span_id not in span_ids for item in evidence for span_id in item.source_span_ids):
        raise ValueError(f"document {document_id} contains Evidence for an unknown SourceSpan")
    source_scope = ApplicabilityScope.from_dict(raw["source_scope"])
    logical_document = LogicalDocument(
        document_logical_id=document_id,
        title=raw["title"],
        source_role=raw["source_role"],
        source_scope=source_scope,
    )
    for item in raw["statements"]:
        _reject_unknown(item, {"statement_id", "statement_type", "text", "evidence_ids", "object_id", "scope", "value", "unit", "risk_level", "quantities"}, "EngineeringStatement")
        if item["statement_type"] not in STATEMENT_TYPES:
            raise ValueError(f"unsupported statement type: {item['statement_type']!r}")
        if not item["evidence_ids"]:
            raise ValueError("EngineeringStatement must reference at least one Evidence")
    statements = tuple(
        EngineeringStatement(
            statement_id=item["statement_id"],
            statement_type=item["statement_type"],
            text=item["text"],
            evidence_ids=tuple(item["evidence_ids"]),
            object_id=item["object_id"],
            scope=ApplicabilityScope.from_dict(item["scope"]),
            value=item.get("value"),
            unit=item.get("unit"),
            risk_level=item.get("risk_level", "normal"),
            quantities=tuple(
                (quantity.get("min"), quantity.get("max"), quantity["unit"])
                for quantity in item.get("quantities", [])
            ),
        )
        for item in raw["statements"]
    )
    _ensure_unique([statement.statement_id for statement in statements], "EngineeringStatement")
    statement_ids = {statement.statement_id for statement in statements}
    evidence_ids = {item.evidence_id for item in evidence}
    for item in evidence:
        if item.statement_id not in statement_ids:
            raise ValueError(f"Evidence {item.evidence_id} points to an unknown statement")
    for statement in statements:
        if any(evidence_id not in evidence_ids for evidence_id in statement.evidence_ids):
            raise ValueError(f"statement {statement.statement_id} points to unknown Evidence")
        valid, errors = statement_scope_within_source(source_scope, statement.scope)
        if not valid:
            raise ValueError(f"statement {statement.statement_id} broadens source scope: {errors}")
    if asset.source_root_id != "fixture" or asset.asset_kind != "synthetic_fixture":
        raise ValueError("Stage 3 fixture assets must be explicitly marked synthetic_fixture")
    return FixtureDocument(
        asset=asset,
        logical_document=logical_document,
        revision=revision,
        source_role=raw["source_role"],
        title=raw["title"],
        source_scope=source_scope,
        pages=pages,
        spans=spans,
        evidence=evidence,
        statements=statements,
    )


def load_corpus(path: Path) -> tuple[FixtureDocument, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1 or raw.get("corpus_kind") != "public_synthetic_fixture":
        raise ValueError("not a Stage 3 public/synthetic fixture corpus")
    documents = tuple(_load_document(item) for item in raw["documents"])
    _ensure_unique([item.asset.asset_id for item in documents], "Asset")
    _ensure_unique([item.logical_document.document_logical_id for item in documents], "LogicalDocument")
    _ensure_unique([item.revision.revision_id for item in documents], "Revision")
    _ensure_unique([page.page_id for item in documents for page in item.pages], "Page")
    _ensure_unique([span.span_id for item in documents for span in item.spans], "SourceSpan")
    _ensure_unique([evidence.evidence_id for item in documents for evidence in item.evidence], "Evidence")
    _ensure_unique([statement.statement_id for item in documents for statement in item.statements], "EngineeringStatement")
    if len(documents) < 3:
        raise ValueError("Stage 3 corpus must contain at least three source roles")
    if len({document.source_role for document in documents}) < 3:
        raise ValueError("Stage 3 corpus must contain three distinct source roles")
    return documents


def index_corpus(documents: tuple[FixtureDocument, ...]) -> dict[str, Any]:
    """Build the small JSON-like index used by both retrieval and graph checks."""

    statements = [statement for document in documents for statement in document.statements]
    evidence = [item for document in documents for item in document.evidence]
    return {
        "documents": {document.revision.document_logical_id: document for document in documents},
        "statements": {statement.statement_id: (statement, document) for document in documents for statement in document.statements},
        "evidence": {item.evidence_id: item for document in documents for item in document.evidence},
        "statement_count": len(statements),
        "evidence_count": len(evidence),
    }

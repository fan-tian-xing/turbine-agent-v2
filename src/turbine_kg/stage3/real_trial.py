"""Adapter that makes the user-confirmed real-page slice executable.

The adapter deliberately accepts only the small, reviewed Stage 3 slice.  It
does not turn the 15-page sample into a production corpus or a formal release.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from .applicability import scope_within_parent, statement_scope_within_source
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


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _normalise(value: str) -> str:
    return " ".join(value.split())


def _compact(value: str) -> str:
    return "".join(value.split())


def confirmed_item_sha256(item: dict[str, Any]) -> str:
    """Bind a source review to the complete historical Evidence/statement record."""
    value = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _typographic_quote(value: str) -> str:
    # Only explicit print/encoding variants, never edit distance or word matching.
    fullwidth_ascii = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
    return _compact(value).translate(fullwidth_ascii).translate(str.maketrans({"。": ".", "，": ",", "～": "~"}))


def _current_sources(
    binding_path: Path,
    confirmation_path: Path,
    runtime_path: Path,
    confirmation: dict[str, Any],
    pages: dict[str, Any],
    registry_assets: dict[str, Any],
    registry_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate an explicit source-only migration without creating user approval."""
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    if (
        binding.get("schema_version") != 1
        or binding.get("artifact_kind") != "stage9_current_source_bindings"
        or binding.get("status") != "current_source_reviewed"
        or binding.get("formal_release") is not False
        or binding.get("user_confirmation") is not False
    ):
        raise ValueError("invalid current source binding contract; no new user confirmation is allowed")
    for field, path in (
        ("historical_confirmation_sha256", confirmation_path),
        ("historical_runtime_sha256", runtime_path),
        ("registry_sha256", registry_path),
    ):
        if binding.get(field) != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError(f"current source binding fingerprint mismatch: {field}")
    records = binding.get("groups")
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise ValueError("current source binding groups must be records")
    by_group = {row.get("group_id"): row for row in records}
    historical_groups = confirmation["confirmed_groups"]
    if len(by_group) != len(records) or set(by_group) != {group["group_id"] for group in historical_groups}:
        raise ValueError("current source bindings must cover each historical group exactly once")
    migrated_groups, current_pages = [], {}
    span_ids: set[str] = set()
    for group in historical_groups:
        if group.get("review_status") != "confirmed" or group.get("user_confirmation") is not True:
            raise ValueError("each imported evidence group must be user-confirmed")
        old_page = pages.get(group["page_id"])
        if old_page is None or old_page.get("span_id") != group["span_id"]:
            raise ValueError("historical runtime page/span identity mismatch")
        for field in ("document_logical_id", "revision_id", "asset_id", "pdf_page_number"):
            if old_page.get(field) != group[field]:
                raise ValueError(f"historical runtime identity mismatch: {field}")
        for page_field, group_field in (("relative_path", "relative_path"), ("asset_sha256", "sha256")):
            if old_page.get(page_field) and old_page[page_field] != group[group_field]:
                raise ValueError(f"historical runtime identity mismatch: {page_field}")
        row = by_group[group["group_id"]]
        historical = row.get("historical", {})
        for field in ("page_id", "span_id", "document_logical_id", "revision_id", "asset_id", "relative_path", "sha256", "pdf_page_number", "logical_page"):
            if historical.get(field) != group.get(field):
                raise ValueError(f"historical source binding identity mismatch: {field}")
        current = row.get("current", {})
        asset = registry_assets.get(current.get("asset_id"))
        if asset is None:
            raise ValueError("current source binding asset is absent from Registry")
        for field in ("document_logical_id", "revision_id", "relative_path", "sha256"):
            if current.get(field) != asset.get(field):
                raise ValueError(f"current source binding differs from Registry: {field}")
        for field in ("document_logical_id", "revision_id", "pdf_page_number", "logical_page"):
            if current.get(field) != group.get(field):
                raise ValueError(f"source-only binding cannot change document revision or page: {field}")
        span = current.get("source_span", {})
        text = span.get("text")
        if (
            not isinstance(text, str) or not text.strip() or span.get("complete") is not True
            or not isinstance(current.get("page_id"), str) or not current["page_id"].strip()
            or not isinstance(span.get("span_id"), str) or not span["span_id"].strip()
            or span.get("page_id") != current["page_id"]
            or span.get("text_sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest()
            or current["page_id"] in current_pages or span["span_id"] in span_ids
        ):
            raise ValueError("invalid, incomplete or duplicate current SourceSpan")
        evidence_rows = row.get("evidence")
        if not isinstance(evidence_rows, list) or not all(isinstance(item, dict) for item in evidence_rows):
            raise ValueError("current Evidence bindings must be records")
        by_evidence = {item.get("evidence_id"): item for item in evidence_rows}
        if len(by_evidence) != len(evidence_rows) or set(by_evidence) != {item["evidence_id"] for item in group["evidence"]}:
            raise ValueError("current source bindings must cover each historical Evidence exactly once")
        migrated = deepcopy(group)
        migrated.update({field: current[field] for field in ("page_id", "document_logical_id", "revision_id", "asset_id", "relative_path", "sha256", "pdf_page_number")})
        migrated["span_id"] = span["span_id"]
        for item in migrated["evidence"]:
            source_review = by_evidence[item["evidence_id"]]
            if (
                source_review.get("statement_id") != item["statement_id"]
                or source_review.get("historical_quote") != item["quote"]
                or source_review.get("historical_item_sha256") != confirmed_item_sha256(item)
                or _compact(item["quote"]) not in _compact(str(old_page["span_preview"]))
            ):
                raise ValueError("current binding does not preserve historical confirmed Evidence/statement")
            quote = source_review.get("current_quote")
            if not isinstance(quote, str) or not quote.strip() or _compact(quote) not in _compact(text):
                raise ValueError("current quote is not present in its complete bound SourceSpan")
            review = source_review.get("semantic_review", {})
            if (
                review.get("status") != "same"
                or review.get("reviewer_type") != "ai_source_review"
                or review.get("user_confirmation") is not False
                or any(not isinstance(review.get(field), str) or not review[field].strip() for field in ("reviewer", "reviewed_at", "basis"))
                or review.get("checks") != {field: "same" for field in ("quote_support", "quantity", "negation", "direction", "condition")}
                or review.get("conflicts") != []
            ):
                raise ValueError("current source semantic review is missing, unresolved or conflicting")
            if _typographic_quote(item["quote"]) != _typographic_quote(quote):
                raise ValueError("current quote changes confirmed wording; a source-only review cannot approve semantic changes")
            # Statement text, quantities, polarity, scope and IDs remain historical.
            item["quote"] = quote
            item["source_span_id"] = span["span_id"]
        migrated_groups.append(migrated)
        current_pages[current["page_id"]] = {
            **current, "asset_sha256": current["sha256"], "span_id": span["span_id"], "span_preview": text,
        }
        span_ids.add(span["span_id"])
    return migrated_groups, current_pages


def load_confirmed_real_corpus(
    confirmation_path: Path,
    runtime_corpus_path: Path,
    *,
    current_binding_path: Path | None = None,
) -> tuple[FixtureDocument, ...]:
    """Load strict historical sources, or an explicitly reviewed current binding.

    Merely placing a binding file in the project never changes the default path.
    Current binding data is source review, not new approval of statement semantics.
    """
    confirmation = json.loads(confirmation_path.read_text(encoding="utf-8"))
    if confirmation.get("status") != "confirmed_by_user":
        raise ValueError("real trial confirmation is not user-confirmed")
    runtime = json.loads(runtime_corpus_path.read_text(encoding="utf-8"))
    pages = {item["page_id"]: item for item in runtime["pages"]}
    registry_path = PROJECT_ROOT / "data" / "registry" / "source_assets.jsonl"
    registry_assets = {
        item["asset_id"]: item
        for item in (
            json.loads(line)
            for line in registry_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    groups = confirmation["confirmed_groups"]
    if current_binding_path is not None:
        groups, pages = _current_sources(current_binding_path, confirmation_path, runtime_corpus_path, confirmation, pages, registry_assets, registry_path)
    documents: list[FixtureDocument] = []
    for group in groups:
        if group.get("review_status") != "confirmed" or group.get("user_confirmation") is not True:
            raise ValueError("each imported evidence group must be user-confirmed")
        page_raw = pages.get(group["page_id"])
        if page_raw is None or page_raw["span_id"] != group["span_id"]:
            raise ValueError(f"confirmed page is absent from the runtime corpus: {group['page_id']}")
        for field in ("document_logical_id", "revision_id", "asset_id", "pdf_page_number"):
            if page_raw[field] != group[field]:
                raise ValueError(f"confirmed page mismatch for {group['page_id']}: {field}")
        if page_raw.get("relative_path") and page_raw["relative_path"] != group["relative_path"]:
            raise ValueError(f"confirmed page path mismatch for {group['page_id']}")
        if page_raw.get("asset_sha256") and page_raw["asset_sha256"] != group["sha256"]:
            raise ValueError(f"confirmed page asset hash mismatch for {group['page_id']}")
        preview = str(page_raw["span_preview"])
        source_span = SourceSpan(group["span_id"], group["page_id"], preview)
        source_scope = ApplicabilityScope.from_dict(group["source_scope"])
        registry_asset = registry_assets.get(group["asset_id"])
        if registry_asset is None:
            raise ValueError(f"confirmed asset is absent from Registry: {group['asset_id']}")
        for field in ("document_logical_id", "revision_id", "relative_path", "sha256"):
            if registry_asset[field] != group[field]:
                raise ValueError(f"confirmed group differs from Registry: {field}")
        registry_scope = ApplicabilityScope.from_dict(registry_asset["applicability_scope_structured"])
        valid, errors = scope_within_parent(registry_scope, source_scope)
        if not valid:
            raise ValueError(f"confirmed source scope broadens Registry scope: {errors}")
        evidence: list[Evidence] = []
        statements: list[EngineeringStatement] = []
        for item in group["evidence"]:
            quote = _normalise(item["quote"])
            if _compact(quote) not in _compact(preview):
                raise ValueError(f"confirmed quote is not present on page {group['page_id']}")
            evidence.append(
                Evidence(
                    evidence_id=item["evidence_id"],
                    source_span_ids=(group["span_id"],),
                    statement_id=item["statement_id"],
                    object_id=item["object_id"],
                    text=item["quote"],
                    value=item.get("value"),
                    unit=item.get("unit"),
                    quantities=tuple(
                        (quantity.get("min"), quantity.get("max"), quantity["unit"])
                        for quantity in item.get("quantities", [])
                    ),
                )
            )
            statement = EngineeringStatement(
                    statement_id=item["statement_id"],
                    statement_type=item["statement_type"],
                    text=item["statement_text"],
                    evidence_ids=(item["evidence_id"],),
                    object_id=item["object_id"],
                    scope=ApplicabilityScope.from_dict(item["applicability"]),
                    value=item.get("value"),
                    unit=item.get("unit"),
                    quantities=tuple(
                        (quantity.get("min"), quantity.get("max"), quantity["unit"])
                        for quantity in item.get("quantities", [])
                    ),
                )
            valid, errors = statement_scope_within_source(source_scope, statement.scope)
            if not valid:
                raise ValueError(f"confirmed statement broadens source scope: {errors}")
            statements.append(statement)
        asset_kind = "derived_ocr" if str(group["relative_path"]).startswith("OCR/") else "original"
        source_root_id = "ocr_derived" if asset_kind == "derived_ocr" else "source"
        documents.append(
            FixtureDocument(
                asset=Asset(
                    group["asset_id"],
                    group["relative_path"],
                    asset_kind,
                    source_root_id,
                    tuple(registry_asset["applicability_scope"]),
                ),
                logical_document=LogicalDocument(
                    group["document_logical_id"],
                    group["title"],
                    group["source_role"],
                    source_scope,
                ),
                revision=Revision(group["revision_id"], group["document_logical_id"], group["revision_id"]),
                source_role=group["source_role"],
                title=group["title"],
                source_scope=source_scope,
                pages=(Page(
                    group["page_id"],
                    group["revision_id"],
                    group["pdf_page_number"],
                    preview,
                    group.get("logical_page"),
                ),),
                spans=(source_span,),
                evidence=tuple(evidence),
                statements=tuple(statements),
            )
        )
    return tuple(documents)

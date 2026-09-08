"""Adapter that makes the user-confirmed real-page slice executable.

The adapter deliberately accepts only the small, reviewed Stage 3 slice.  It
does not turn the 15-page sample into a production corpus or a formal release.
"""

from __future__ import annotations

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


def load_confirmed_real_corpus(
    confirmation_path: Path,
    runtime_corpus_path: Path,
) -> tuple[FixtureDocument, ...]:
    confirmation = json.loads(confirmation_path.read_text(encoding="utf-8"))
    if confirmation.get("status") != "confirmed_by_user":
        raise ValueError("real trial confirmation is not user-confirmed")
    runtime = json.loads(runtime_corpus_path.read_text(encoding="utf-8"))
    pages = {item["page_id"]: item for item in runtime["pages"]}
    registry_assets = {
        item["asset_id"]: item
        for item in (
            json.loads(line)
            for line in (PROJECT_ROOT / "data" / "registry" / "source_assets.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    documents: list[FixtureDocument] = []
    for group in confirmation["confirmed_groups"]:
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
                pages=(Page(group["page_id"], group["revision_id"], group["pdf_page_number"], preview),),
                spans=(source_span,),
                evidence=tuple(evidence),
                statements=tuple(statements),
            )
        )
    return tuple(documents)

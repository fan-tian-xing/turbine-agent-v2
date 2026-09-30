"""Apply original-page-reviewed boundaries to Stage 6 source Evidence.

These narrow rules keep navigation, incomplete adjacent-page fragments, and
diagram labels from being presented as standalone extractable requirements.
"""

from __future__ import annotations

import hashlib
import re

import pymupdf

from turbine_kg.evidence import build_evidence

from scripts.stage6_reviewed_pdf_regions import append_pdf_regions, region_text


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _scope(sample: dict) -> tuple[str, int]:
    return sample["document_key"], int(sample["physical_page"])


def body_spans_for_sample(sample: dict, spans: list, overrides: dict) -> list:
    """Apply only measured page margins, without dropping any interior prose."""
    rules = [row for row in overrides.get("reviewed_page_body_bounds", [])
             if (row["document_key"], row["physical_page"]) == _scope(sample)]
    if not rules:
        return spans
    if len(rules) != 1:
        raise ValueError(f"duplicate reviewed body bounds: {_scope(sample)}")
    lower, upper = rules[0]["y_min"], rules[0]["y_max"]
    if not 0 <= lower < upper:
        raise ValueError("invalid reviewed body bounds")
    return [span for span in spans if span.bbox is not None
            and lower <= span.bbox.y0 and span.bbox.y1 <= upper]


def _split_row(item, ir, metadata: dict, rule: dict, *, processing_path, original_path):
    key, number = rule["document_key"], rule["physical_page"]
    if _sha(item.source_text) != rule["source_text_sha256"]:
        raise ValueError(f"reviewed split source changed: {key} p{number}")
    fragments = []
    texts = []
    with pymupdf.open(processing_path) as processing, pymupdf.open(original_path) as original:
        for segment in rule["segments"]:
            bbox = segment["bbox_pdf_pt"]
            current = region_text(processing[number - 1], bbox)
            authority = region_text(original[number - 1], bbox)
            if _sha(current) != segment["text_sha256"] or current != authority:
                raise ValueError(f"reviewed split region changed: {segment['segment_id']}")
            texts.append(current)
            fragments.append({"physical_page": number, "source_boxes": [{
                "box_id": segment["segment_id"], "bbox_pdf_pt": bbox, "text": current,
            }]})
        if re.sub(r"\s+", "", "".join(texts)) != re.sub(r"\s+", "", item.source_text):
            raise ValueError(f"reviewed split did not conserve source text: {key} p{number}")
        adapted, links = append_pdf_regions(ir, processing, fragments)
    rows = []
    for segment in rule["segments"]:
        if segment["disposition"] == "visual_only":
            # The separate manifest-bound figure Evidence supplies this
            # context without elevating loose legend text to a claim.
            continue
        sid = segment["segment_id"]
        evidence = build_evidence(
            adapted, (links[sid],), evidence_role=item.evidence_role,
            disposition=segment["disposition"],
            authority_rotation_deg=item.locations[0].authority_rotation_deg,
            coordinate_transform=item.locations[0].coordinate_transform,
        )
        details = {**metadata, "reviewed_segment_id": sid}
        if segment["stage12_extractability"] != "source_text":
            details["stage12_extractability"] = segment["stage12_extractability"]
            details["extractability_reason"] = "Reviewed source fragment lacks its adjacent-page context or belongs to a diagram."
        rows.append((evidence, adapted, details))
    return rows


def apply_reviewed_page_boundaries(sample: dict, rows: list, overrides: dict, *,
                                   processing_path, original_path) -> list:
    """Replace only exact reviewed rows and classify diagram/context material."""
    scope = _scope(sample)
    rules = [row for row in overrides.get("reviewed_span_splits", [])
             if (row["document_key"], row["physical_page"]) == scope]
    if len({row["source_text_sha256"] for row in rules}) != len(rules):
        raise ValueError(f"duplicate reviewed source split: {scope}")
    matched = {row["source_text_sha256"]: 0 for row in rules}
    expanded = []
    for item, ir, metadata in rows:
        digest = _sha(item.source_text)
        rule = next((row for row in rules if row["source_text_sha256"] == digest), None)
        if rule is None:
            expanded.append((item, ir, metadata))
        else:
            matched[digest] += 1
            expanded.extend(_split_row(item, ir, metadata, rule,
                                       processing_path=processing_path, original_path=original_path))
    if any(count != 1 for count in matched.values()):
        raise ValueError(f"missing or duplicate reviewed source split: {scope}")

    context_rules = [row for row in overrides.get("reviewed_context_evidence", [])
                     if (row["document_key"], row["physical_page"]) == scope]
    context_matched = {row["source_text_sha256"]: 0 for row in context_rules}
    result = []
    for item, ir, metadata in expanded:
        details = dict(metadata)
        digest = _sha(item.source_text)
        visual_supplement = details.get("source_supplement_kind") == "reviewed_visual_only"
        context = next((row for row in context_rules if row["source_text_sha256"] == digest), None) if not visual_supplement else None
        if context is not None:
            context_matched[digest] += 1
            details["stage12_extractability"] = context["status"]
            details["extractability_reason"] = context["reason"]
        # Reviewed figure areas are source context, not independent spatial or
        # numeric claims. The figure rectangle/caption has separate Evidence.
        if (scope == ("DL5190.3", 25) and not visual_supplement
                and item.locations[0].bbox.y0 >= 120):
            continue
        elif (scope == ("DL5190.3", 86) and not visual_supplement
              and 385 <= item.locations[0].bbox.y0 <= 455):
            continue
        elif (scope == ("D300N", 38) and not visual_supplement):
            # The original p38 drawing contains an unreadable center label;
            # preserve its image/caption only, not its guessed OCR tokens.
            continue
        result.append((item, ir, details))
    if any(count != 1 for count in context_matched.values()):
        raise ValueError(f"missing or duplicate reviewed context Evidence: {scope}")
    if not result:
        raise ValueError(f"reviewed boundary removed all Evidence: {scope}")
    return result

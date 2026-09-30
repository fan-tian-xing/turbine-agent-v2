"""Build source-grounded Evidence for the independently selected Reserve regions.

The source-region manifest is reviewed against original PDF page images before
this builder runs.  This builder verifies identity and OCR extraction, and never
reads Gold, Candidate or model output.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pymupdf

from scripts.build_stage11_holdout import _page_index, _id
from turbine_kg.registry.source_inputs import resolve_allowlisted_path
from turbine_kg.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data/stage11/evaluation_sample_registry.json"
REGIONS_PATH = ROOT / "data/stage12/stage12_reserve_source_regions.json"
OUTPUT_PATH = ROOT / "data/stage12/stage12_reserve_evidence.jsonl"
RESERVE_SPLIT = "acceptance_holdout_reserve"
EXECUTION_ARTIFACTS = (
    ROOT / "data/stage12/stage12_reserve_execution_marker.json",
    ROOT / "data/stage12/stage12_reserve_candidates.json",
    ROOT / "data/stage12/stage12_reserve_acceptance.json",
)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_text(value: str) -> str:
    return _sha_bytes(value.encode("utf-8"))


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def assert_rebuild_safe() -> None:
    existing = [path.name for path in EXECUTION_ARTIFACTS if path.exists()]
    if existing:
        raise PermissionError("Reserve Evidence cannot be rebuilt after execution: " + ", ".join(existing))
    manifest_path = ROOT / "data/stage12/stage12_reserve_freeze_manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("freeze_status") == "human_approved":
            raise PermissionError("Reserve Evidence cannot be rebuilt after human-approved freeze")


def build() -> list[dict]:
    settings = Settings.from_environment()
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    regions_doc = json.loads(REGIONS_PATH.read_text(encoding="utf-8"))
    if regions_doc.get("status") != "reviewed_pre_execution" or regions_doc.get("coverage_scope") != "selected_regions_only":
        raise ValueError("Reserve source regions lack reviewed, bounded coverage")
    reserve = [row for row in registry.get("records", []) if row.get("split") == RESERVE_SPLIT]
    regions = regions_doc.get("regions", [])
    if len(reserve) != len(regions) or len(reserve) != 5:
        raise ValueError("Reserve Registry and reviewed regions must contain five matching pages")
    by_key = {(row["document_key"], int(row["physical_page"])): row for row in regions}
    if len(by_key) != len(regions):
        raise ValueError("Reserve source region pages are duplicated")
    used = {(row["document_key"], int(row["physical_page"])) for row in registry["records"] if row.get("split") != RESERVE_SPLIT}
    old = {(row["document_key"], int(row["physical_page"])) for row in regions_doc.get("excluded_prior_reserve_pages", [])}
    if set(by_key) & (used | old):
        raise ValueError("Reserve page overlaps Development, exposed Holdout or consumed Reserve")
    if set(by_key) != {(row["document_key"], int(row["physical_page"])) for row in reserve}:
        raise ValueError("Reserve reviewed regions do not match frozen Registry pages")
    pages = _page_index()
    assets = {row["asset_id"]: row for row in _rows(ROOT / "data/registry/source_assets.jsonl")}
    result = []
    for record in sorted(reserve, key=lambda row: (row["document_key"], int(row["physical_page"]))):
        key = record["document_key"], int(record["physical_page"])
        region = by_key[key]
        page_meta = pages[key]
        if record.get("frozen") is not True or (record.get("independence") or {}).get("statement") is not True:
            raise ValueError(f"Reserve Registry independence is not frozen: {key}")
        if page_meta.get("page_status") != "text_accepted" or region.get("review_status") != "original_visual_reviewed":
            raise ValueError(f"Reserve page or original visual review is incomplete: {key}")
        for field, page_field in (("document_logical_id", "document_logical_id"), ("revision_id", "revision_id"), ("source_text_sha256", "analysis_text_sha256")):
            if record.get(field) != page_meta.get(page_field):
                raise ValueError(f"Reserve Registry/page identity differs: {key} {field}")
        authority = assets[page_meta["authority_asset_id"]]
        original_path = resolve_allowlisted_path(authority["relative_path"], settings.source_root, settings.ocr_derived_root)
        processing_path = resolve_allowlisted_path(page_meta["processing_relative_path"], settings.source_root, settings.ocr_derived_root)
        if _sha_bytes(original_path.read_bytes()) != authority["sha256"]:
            raise ValueError(f"original PDF changed: {key}")
        original = pymupdf.open(original_path)
        processing = pymupdf.open(processing_path)
        try:
            page_number = key[1] - 1
            original_page, processing_page = original[page_number], processing[page_number]
            page_text = processing_page.get_text("text").strip()
            if _sha_text(page_text) != record["source_text_sha256"]:
                raise ValueError(f"Reserve processing page changed: {key}")
            source_text = str(region.get("source_text") or "").strip()
            if not source_text or re.sub(r"\s+", "", source_text) not in re.sub(r"\s+", "", page_text):
                raise ValueError(f"Reserve reviewed quote not present in current OCR page: {key}")
            bbox = region.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                raise ValueError(f"Reserve original-page bbox is missing: {key}")
            rect = pymupdf.Rect(bbox)
            if rect.is_empty or not original_page.rect.contains(rect) or not processing_page.rect.contains(rect):
                raise ValueError(f"Reserve original-page bbox is outside the page: {key}")
            source_hash = _sha_text(source_text)
            source_span_id = _id("stage12-reserve-span", record["sample_id"], source_hash)
            evidence_id = _id("stage12-reserve-evidence", record["sample_id"], source_hash)
            evidence_row = {
                "sample_id": record["sample_id"],
                "evidence_id": evidence_id,
                "evidence_version_id": _id("stage12-reserve-evver", source_span_id),
                "document_key": record["document_key"],
                "document_logical_id": record["document_logical_id"],
                "revision_id": record["revision_id"],
                "authority_asset_id": authority["asset_id"],
                "processing_asset_id": page_meta["processing_asset_id"],
                "physical_page": key[1],
                "logical_page": page_meta.get("logical_page"),
                "source_span_id": source_span_id,
                "source_span_ids": [source_span_id],
                "locations": [{"physical_page": key[1], "logical_page": page_meta.get("logical_page"), "source_span_id": source_span_id, "bbox": bbox}],
                "bbox": bbox,
                "source_locator": region["source_locator"],
                "source_text": source_text,
                "effective_text": source_text,
                "source_text_sha256": source_hash,
                "evidence_text_sha256": source_hash,
                "page_analysis_text_sha256": record["source_text_sha256"],
                "evidence_status": "accepted",
                "source_confirmation_status": "accepted",
                "review_status": "accepted",
                "reviewer_type": "assistant_original_visual_review",
                "review_reason": region["review_basis"],
                "coverage_scope": "selected_region_only",
                "support_type": "direct",
                "formal_release": False,
                "reserve_provenance": {
                    "registry_sha256": _sha_bytes(REGISTRY_PATH.read_bytes()),
                    "source_regions_sha256": _sha_bytes(REGIONS_PATH.read_bytes()),
                    "original_pdf_sha256": authority["sha256"],
                    "candidate_not_seen": True,
                    "gold_not_seen": True,
                    "model_execution_started": False,
                },
            }
            question_review = region.get("question_review")
            looks_like_options = bool(re.search(r"[（(]\s*A\s*[）)]", source_text, re.I) and re.search(r"[（(]\s*B\s*[）)]", source_text, re.I))
            if looks_like_options and not question_review:
                raise ValueError(f"Reserve question options lack original-page answer review: {key}")
            if question_review is not None:
                if question_review.get("status") != "confirmed_original_page":
                    raise ValueError(f"Reserve question answer is not source-confirmed: {key}")
                option = str(question_review.get("marked_option") or "").strip().upper()
                answer_text = str(question_review.get("answer_text") or "").strip()
                answer_quote = str(question_review.get("answer_source_quote") or "").strip()
                if not option or not answer_text or not answer_quote or answer_quote not in source_text or answer_text not in answer_quote or not re.search(r"[（(]\s*" + re.escape(option) + r"\s*[）)]", source_text):
                    raise ValueError(f"Reserve question marked answer does not match reviewed source: {key}")
                evidence_row["related_source_context"] = [{
                    "group_id": _id("stage12-reserve-question", record["sample_id"], source_hash),
                    "group_kind": "question_options",
                    "source_review_status": "confirmed",
                    "answer_marked": True,
                    "stem_evidence_id": evidence_id,
                    "answer_evidence_id": evidence_id,
                    "answer_option": option,
                    "answer_text": answer_text,
                    "answer_source_quote": answer_quote,
                    "member_evidence": [{
                        "evidence_id": evidence_id,
                        "text": source_text,
                        "document_logical_id": record["document_logical_id"],
                        "revision_id": record["revision_id"],
                        "locations": evidence_row["locations"],
                        "evidence_version_id": evidence_row["evidence_version_id"],
                        "source_text_sha256": source_hash,
                    }],
                }]
            result.append(evidence_row)
        finally:
            processing.close()
            original.close()
    if len({row["evidence_id"] for row in result}) != 5:
        raise ValueError("Reserve Evidence identities must be unique")
    return result


if __name__ == "__main__":
    assert_rebuild_safe()
    rows = build()
    OUTPUT_PATH.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"artifact": str(OUTPUT_PATH), "evidence_count": len(rows), "pages": [[row["document_key"], row["physical_page"]] for row in rows]}, ensure_ascii=False))

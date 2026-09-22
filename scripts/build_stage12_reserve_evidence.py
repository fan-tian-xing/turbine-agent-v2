"""Build the frozen Stage 12 Independent Reserve Evidence slice.

This producer is deliberately Evidence-only.  It reads only the five frozen
``acceptance_holdout_reserve`` pages from the Stage 11 registry and emits one
page-scoped Evidence record per page.  It never reads a Candidate or Gold
artifact and it never invokes an extraction provider.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf

from build_stage11_holdout import _page_index, _id
from turbine_kg.registry.source_inputs import resolve_allowlisted_path
from turbine_kg.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "data" / "stage11" / "evaluation_sample_registry.json"
OUTPUT_PATH = ROOT / "data" / "stage12" / "stage12_reserve_evidence.jsonl"
RESERVE_SPLIT = "acceptance_holdout_reserve"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalise_page_text(value: str) -> str:
    # The frozen Stage 11 page manifest hashes the stripped PDF text, keeping
    # line breaks so the Reserve Evidence remains verbatim and page-recallable.
    return (value or "").strip()


def _page_bbox(page) -> list[float]:
    return [round(float(value), 3) for value in page.rect]


def _load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def build() -> list[dict]:
    settings = Settings.from_environment()
    registry = _load_registry()
    reserve = [row for row in registry.get("records", []) if row.get("split") == RESERVE_SPLIT]
    if len(reserve) != 5:
        raise ValueError(f"expected five frozen Reserve records, found {len(reserve)}")
    if any(row.get("frozen") is not True for row in reserve):
        raise ValueError("Reserve pages must be frozen before Evidence preparation")
    if any((row.get("independence") or {}).get("statement") is not True for row in reserve):
        raise ValueError("Reserve statement independence is not intact")

    pages = _page_index()
    evidence: list[dict] = []
    for record in sorted(reserve, key=lambda row: (row["document_key"], int(row["physical_page"]))):
        key = (record["document_key"], int(record["physical_page"]))
        page_meta = pages.get(key)
        if page_meta is None or page_meta.get("page_status") != "text_accepted":
            raise ValueError(f"Reserve page is not a frozen text_accepted page: {key}")
        for field in ("document_logical_id", "revision_id", "source_text_sha256"):
            if record.get(field) != page_meta.get("document_logical_id" if field == "document_logical_id" else field if field != "source_text_sha256" else "analysis_text_sha256"):
                raise ValueError(f"registry/page manifest mismatch for {key}: {field}")

        pdf_path = resolve_allowlisted_path(
            page_meta["processing_relative_path"],
            settings.source_root,
            settings.ocr_derived_root,
        )
        pdf = pymupdf.open(pdf_path)
        try:
            page = pdf[int(record["physical_page"]) - 1]
            source_text = _normalise_page_text(page.get_text("text"))
            if not source_text:
                raise ValueError(f"Reserve page has no extractable text: {key}")
            source_hash = _sha(source_text)
            if source_hash != record["source_text_sha256"] or source_hash != page_meta["analysis_text_sha256"]:
                raise ValueError(f"Reserve page hash mismatch: {key}")
            source_span_id = _id("stage12-reserve-span", record["sample_id"], source_hash)
            evidence_id = _id("stage12-reserve-evidence", record["sample_id"], source_hash)
            evidence_version_id = _id("stage12-reserve-evver", source_span_id)
            evidence.append({
                "sample_id": record["sample_id"],
                "evidence_id": evidence_id,
                "evidence_version_id": evidence_version_id,
                "document_key": record["document_key"],
                "document_logical_id": record["document_logical_id"],
                "revision_id": record["revision_id"],
                "authority_asset_id": page_meta["authority_asset_id"],
                "processing_asset_id": page_meta["processing_asset_id"],
                "physical_page": int(record["physical_page"]),
                "logical_page": page_meta.get("logical_page"),
                "source_span_id": source_span_id,
                "bbox": _page_bbox(page),
                "source_text": source_text,
                "evidence_status": "accepted",
                "source_confirmation_status": "accepted",
                "page_analysis_text_sha256": page_meta["analysis_text_sha256"],
                "evidence_text_sha256": source_hash,
                "source_text_sha256": source_hash,
                "effective_text": source_text,
                "review_status": "accepted",
                "reviewer": "stage12_frozen_page_text_verifier",
                "reviewer_type": "deterministic_source_verifier",
                "review_reason": "Evidence is the complete whitespace-normalized text of the frozen Reserve page; registry and page-manifest hashes were verified before any Candidate or Gold was read.",
                "review_plan": [
                    {"round": 1, "reviewer_role": "independent_source_reviewer", "scope": "original_page_and_source_span"},
                    {"round": 2, "reviewer_role": "independent_semantic_reviewer", "scope": "numeric_unit_negation_scope"},
                ],
                "support_type": "direct",
                "formal_release": False,
                "reserve_provenance": {
                    "registry_sha256": _file_sha(REGISTRY_PATH),
                    "candidate_not_seen": True,
                    "gold_not_seen": True,
                    "model_execution_started": False,
                },
            })
        finally:
            pdf.close()
    if len(evidence) != 5 or len({row["evidence_id"] for row in evidence}) != 5:
        raise ValueError("Reserve Evidence must contain exactly five unique page records")
    return evidence


if __name__ == "__main__":
    rows = build()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    print(json.dumps({"artifact": str(OUTPUT_PATH), "evidence_count": len(rows), "pages": [[row["document_key"], row["physical_page"]] for row in rows]}, ensure_ascii=False))

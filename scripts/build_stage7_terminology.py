"""Build Stage 7 terminology candidates from the frozen page manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf

from turbine_kg.settings import Settings
from turbine_kg.terminology.analyzer import analyze_terminology, capability_questions_payload, load_stage6_evidence_bundle
from turbine_kg.terminology.models import CANDIDATE_TYPES
from turbine_kg.terminology.validation import content_fingerprint, validate_candidates, validate_input_manifest


ROOT = Path(__file__).resolve().parents[1]
STAGE7 = ROOT / "data" / "stage7"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _accepted_page_texts(manifest: dict, settings: Settings) -> dict[str, str]:
    texts: dict[str, str] = {}
    grouped: dict[str, list[dict]] = {}
    for page in manifest["pages"]:
        if page["page_status"] == "text_accepted":
            grouped.setdefault(page["processing_relative_path"], []).append(page)
    for relative_path, pages in grouped.items():
        path = settings.ocr_derived_root / relative_path.removeprefix("OCR/") if relative_path.startswith("OCR/") else settings.source_root / relative_path
        with pymupdf.open(path) as pdf:
            for page in pages:
                text = pdf[page["physical_page"] - 1].get_text("text").strip()
                actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if actual_sha != page["processing_text_sha256"]:
                    raise ValueError(f"processing text fingerprint changed: {page['page_id']}")
                texts[page["page_id"]] = text
    return texts


def main() -> None:
    settings = Settings.from_environment()
    manifest = _read(STAGE7 / "terminology_input_manifest.json")
    validate_input_manifest(manifest)
    # Loading the canonical Stage 6 artifact is an intentional bounded
    # consumer and prevents this stage from silently treating it as full text.
    stage6_rows = load_stage6_evidence_bundle(ROOT / "data" / "stage6" / "stage6_evidence_bundle.jsonl")
    page_texts = _accepted_page_texts(manifest, settings)
    candidates = analyze_terminology(manifest, page_texts, stage6_rows=stage6_rows)
    accepted_keys = {
        (row["document_logical_id"], row["physical_page"])
        for row in manifest["pages"]
        if row["page_status"] == "text_accepted"
    }
    validate_candidates(candidates, accepted_keys)

    review_queue = []
    review_decisions = []
    for candidate in candidates:
        if candidate["candidate_type"] not in {"parameter", "requirement", "applicability_condition", "ocr_variant_candidate"}:
            continue
        queue_row = {
            "queue_id": f"review-{candidate['candidate_id']}",
            "candidate_id": candidate["candidate_id"],
            "reason": "high_risk_or_ocr_candidate_requires_review_before_any ontology or vocabulary promotion",
            "blocking": False,
            "status": "deferred_not_promoted",
        }
        review_queue.append(queue_row)
        review_decisions.append({
            "queue_id": queue_row["queue_id"],
            "candidate_id": candidate["candidate_id"],
            "decision": "deferred_not_promoted",
            "decision_source": "stage7_boundary_policy",
            "human_reviewed": False,
            "consumer_permission": "candidate_only",
            "reason": "Discovery output is retained for later human review; it cannot change frozen ontology or display vocabulary.",
        })

    source_fingerprint = content_fingerprint({
        "manifest": manifest["content_fingerprint"],
        "stage6_bundle_sha256": _sha(ROOT / "data" / "stage6" / "stage6_evidence_bundle.jsonl"),
        "page_text_ids": sorted(page_texts),
    })
    header = {
        "schema_version": 1,
        "stage": "7",
        "artifact_kind": "terminology_candidates",
        "status": "candidate_only",
        "formal_release": False,
        "producer": "turbine_kg.terminology.analyzer",
        "consumer": ["data/stage7/terminology_review_queue.jsonl", "Stage 8 only after review_status=accepted"],
        "inputs": {
            "terminology_input_manifest": "data/stage7/terminology_input_manifest.json",
            "stage6_canonical_sample": "data/stage6/stage6_evidence_bundle.jsonl",
            "source_fingerprint": source_fingerprint,
        },
        "analysis_rounds": {
            "round_1": "metadata and reliable native text discovery boundary recorded in the manifest",
            "round_2": "only text_accepted pages from the frozen manifest",
        },
        "candidate_types": sorted(CANDIDATE_TYPES),
        "automatic_promotion": False,
        "candidates": candidates,
    }
    (STAGE7 / "terminology_candidates.json").write_text(json.dumps(header, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_jsonl(STAGE7 / "terminology_review_queue.jsonl", review_queue)
    _write_jsonl(STAGE7 / "terminology_review_decisions.jsonl", review_decisions)

    capability = capability_questions_payload()
    capability["inputs"] = {"terminology_contract_sha256": _sha(ROOT / "config" / "terminology_contract.json")}
    capability["content_fingerprint"] = content_fingerprint(capability)
    (STAGE7 / "business_capability_questions.json").write_text(json.dumps(capability, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": header["status"], "candidate_count": len(candidates), "review_queue_count": len(review_queue), "accepted_page_count": len(page_texts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

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
from turbine_kg.terminology.analyzer import analyze_terminology, capability_questions_payload, load_stage6_evidence_bundle, load_terminology_contract
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


def _accepted_page_texts(manifest: dict, settings: Settings, stage6_rows: list[dict]) -> dict[str, str]:
    texts: dict[str, str] = {}
    grouped: dict[str, list[dict]] = {}
    stage6_by_key: dict[tuple[str, int], list[dict]] = {}
    for row in stage6_rows:
        key = (row["document_key"], int(row["input"]["physical_page"]))
        stage6_by_key.setdefault(key, []).append(row)
    for page in manifest["pages"]:
        if page["page_status"] == "text_accepted":
            grouped.setdefault(page["processing_relative_path"], []).append(page)
    for relative_path, pages in grouped.items():
        path = settings.ocr_derived_root / relative_path.removeprefix("OCR/") if relative_path.startswith("OCR/") else settings.source_root / relative_path
        with pymupdf.open(path) as pdf:
            for page in pages:
                if page["text_source"] == "stage6_accepted_evidence":
                    rows = stage6_by_key.get((page["document_key"], page["physical_page"]), [])
                    evidence_text = "\n".join(
                        row["evidence"].get("effective_text") or row["evidence"].get("source_text") or ""
                        for row in rows
                        if row["evidence"].get("disposition") == "structured"
                    ).strip()
                    if not evidence_text:
                        raise ValueError(f"accepted OCR page has no structured Stage 6 text: {page['page_id']}")
                    actual_sha = hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()
                    if actual_sha != page["analysis_text_sha256"]:
                        raise ValueError(f"Stage 6 analysis text fingerprint changed: {page['page_id']}")
                    texts[page["page_id"]] = evidence_text
                    continue
                if page["text_source"] != "native_pdf_text":
                    raise ValueError(f"non-native page entered text_accepted without Stage 6 acceptance: {page['page_id']}")
                text = pdf[page["physical_page"] - 1].get_text("text").strip()
                actual_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if actual_sha != page["processing_text_sha256"]:
                    raise ValueError(f"processing text fingerprint changed: {page['page_id']}")
                if actual_sha != page["analysis_text_sha256"]:
                    raise ValueError(f"native analysis text fingerprint changed: {page['page_id']}")
                texts[page["page_id"]] = text
    return texts


def main() -> None:
    settings = Settings.from_environment()
    manifest = _read(STAGE7 / "terminology_input_manifest.json")
    validate_input_manifest(manifest)
    # Loading the canonical Stage 6 artifact is an intentional bounded
    # consumer and prevents this stage from silently treating it as full text.
    stage6_rows = load_stage6_evidence_bundle(ROOT / "data" / "stage6" / "stage6_evidence_bundle.jsonl")
    contract = load_terminology_contract(ROOT / "config" / "terminology_contract.json")
    page_texts = _accepted_page_texts(manifest, settings, stage6_rows)
    candidates = analyze_terminology(manifest, page_texts, stage6_rows=stage6_rows, contract=contract)
    accepted_keys = {
        (row["document_logical_id"], row["physical_page"])
        for row in manifest["pages"]
        if row["page_status"] == "text_accepted"
    }
    validate_candidates(candidates, accepted_keys)

    review_queue = []
    for candidate in candidates:
        needs_review = (
            candidate["candidate_type"] in set(contract["review"]["blocking_candidate_types"])
            or "ocr_text" in candidate["text_origins"]
        )
        if not needs_review:
            continue
        queue_row = {
            "queue_id": f"review-{candidate['candidate_id']}",
            "candidate_id": candidate["candidate_id"],
            "candidate_fingerprint": candidate["content_fingerprint"],
            "manifest_fingerprint": manifest["content_fingerprint"],
            "reason": "high-risk, abbreviation, or OCR candidate requires human review before ontology or vocabulary promotion",
            "blocking": True,
            "status": "pending_human_review",
            "human_review_required": True,
        }
        review_queue.append(queue_row)

    relationship_queue = [
        {
            "queue_id": "relationship-synonym-candidate",
            "candidate_type": "synonym_candidate",
            "status": "pending_human_review",
            "blocking": True,
            "human_review_required": True,
            "reason": "Synonym relation discovery requires an explicit source-backed human comparison; no automatic inference is made in Stage 7.",
            "manifest_fingerprint": manifest["content_fingerprint"],
        },
        {
            "queue_id": "relationship-old-name-candidate",
            "candidate_type": "old_name_candidate",
            "status": "pending_human_review",
            "blocking": True,
            "human_review_required": True,
            "reason": "Old-name relation discovery requires an explicit source-backed human comparison; no automatic inference is made in Stage 7.",
            "manifest_fingerprint": manifest["content_fingerprint"],
        },
    ]
    review_decisions: list[dict] = []

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
        "contract_sha256": _sha(ROOT / "config" / "terminology_contract.json"),
        "automatic_promotion": False,
        "candidates": candidates,
    }
    (STAGE7 / "terminology_candidates.json").write_text(json.dumps(header, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_jsonl(STAGE7 / "terminology_review_queue.jsonl", review_queue)
    _write_jsonl(STAGE7 / "terminology_review_decisions.jsonl", review_decisions)
    _write_jsonl(STAGE7 / "terminology_relationship_review_queue.jsonl", relationship_queue)

    review_summary = {
        "schema_version": 1,
        "stage": "7",
        "artifact_kind": "stage7_human_review_summary",
        "status": "pending_human_review",
        "formal_release": False,
        "producer": "scripts/build_stage7_terminology.py",
        "consumer": ["human reviewer", "scripts/audit_stage7_exit.py"],
        "manifest_fingerprint": manifest["content_fingerprint"],
        "candidate_count": len(candidates),
        "candidate_review_queue_count": len(review_queue),
        "relationship_review_queue_count": len(relationship_queue),
        "required_review_candidate_types": sorted(set(contract["review"]["blocking_candidate_types"])),
        "required_relationship_types": sorted(contract["review"]["unresolved_relationship_types_require_user_review"]),
        "review_queue": "data/stage7/terminology_review_queue.jsonl",
        "relationship_review_queue": "data/stage7/terminology_relationship_review_queue.jsonl",
        "decisions": "data/stage7/terminology_review_decisions.jsonl",
        "promotion_permission": "none_until_human_reviewed_accepted_with_matching_fingerprint_and_evidence_refs",
    }
    review_summary["content_fingerprint"] = content_fingerprint(review_summary)
    (STAGE7 / "stage7_human_review_summary.json").write_text(json.dumps(review_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    capability = capability_questions_payload()
    capability["inputs"] = {"terminology_contract_sha256": _sha(ROOT / "config" / "terminology_contract.json")}
    capability["content_fingerprint"] = content_fingerprint(capability)
    (STAGE7 / "business_capability_questions.json").write_text(json.dumps(capability, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": header["status"], "candidate_count": len(candidates), "review_queue_count": len(review_queue), "accepted_page_count": len(page_texts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

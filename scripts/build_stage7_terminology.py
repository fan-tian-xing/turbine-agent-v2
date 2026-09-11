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

    source_fingerprint = content_fingerprint({
        "manifest": manifest["content_fingerprint"],
        "page_text_ids": sorted(page_texts),
    })
    header = {
        "schema_version": 1,
        "stage": "7",
        "artifact_kind": "terminology_candidates",
        "status": "candidate_only",
        "formal_release": False,
        "producer": "turbine_kg.terminology.analyzer",
        "consumer": ["Stage 8 ontology capability mapping and selected-candidate review"],
        "inputs": {
            "terminology_input_manifest": "data/stage7/terminology_input_manifest.json",
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

    capability = capability_questions_payload()
    capability["inputs"] = {"terminology_contract_sha256": _sha(ROOT / "config" / "terminology_contract.json")}
    capability["content_fingerprint"] = content_fingerprint(capability)
    (STAGE7 / "business_capability_questions.json").write_text(json.dumps(capability, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": header["status"], "candidate_count": len(candidates), "accepted_page_count": len(page_texts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

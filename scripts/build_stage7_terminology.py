"""Build Stage 7 terminology candidates from the frozen page manifest."""

from __future__ import annotations

import hashlib
import json
import argparse
from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf

from turbine_kg.settings import Settings
from turbine_kg.terminology.analyzer import analyze_terminology, capability_questions_payload, load_stage6_evidence_bundle, load_terminology_contract
from turbine_kg.terminology.models import CANDIDATE_TYPES
from turbine_kg.terminology.validation import content_fingerprint, validate_candidates, validate_input_manifest
from turbine_kg.observability.runtime import producer_ref, run_with_cache, sha256_value
from turbine_kg.registry.source_inputs import load_allowlist, resolve_allowlisted_path, sha256_file


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


def _runtime_input_gate(manifest: dict, settings: Settings) -> None:
    """Recheck the frozen manifest against the current allowlist and Registry."""
    assets = {
        json.loads(line)["asset_id"]: json.loads(line)
        for line in (ROOT / "data/registry/source_assets.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    _, allowlist = load_allowlist(ROOT / "config/source_allowlist.tsv")
    allowlisted = {row["path"] for row in allowlist}
    for page in manifest["pages"]:
        if page["page_status"] != "text_accepted":
            continue
        asset = assets.get(page["processing_asset_id"])
        if asset is None or asset["relative_path"] not in allowlisted:
            raise ValueError(f"runtime manifest asset is not currently allowlisted: {page['page_id']}")
        if asset["document_logical_id"] != page["document_logical_id"] or asset["revision_id"] != page["revision_id"]:
            raise ValueError(f"runtime manifest identity differs from Registry: {page['page_id']}")
        path = resolve_allowlisted_path(asset["relative_path"], settings.source_root, settings.ocr_derived_root)
        if not path.is_file() or sha256_file(path) != asset["sha256"]:
            raise ValueError(f"runtime manifest asset fingerprint differs from Registry: {page['page_id']}")


def _runtime_run(manifest: dict, settings: Settings, *, force: bool) -> None:
    stage6_path = ROOT / "data/stage6/stage6_evidence_bundle.jsonl"
    manifest_ref = {"id": "stage7-input-manifest", "path": "data/stage7/terminology_input_manifest.json", "sha256": _sha(STAGE7 / "terminology_input_manifest.json")}
    stage6_ref = {"id": "stage6-evidence-bundle", "path": "data/stage6/stage6_evidence_bundle.jsonl", "sha256": _sha(stage6_path)}
    config_path = ROOT / "config/terminology_contract.json"
    config_ref = {"id": "stage7-terminology-contract", "path": "config/terminology_contract.json", "sha256": _sha(config_path)}
    runtime_settings = {
        "source_root": str(settings.source_root),
        "ocr_derived_root": str(settings.ocr_derived_root),
        "llm_model": settings.llm_model,
        "llm_allow_evidence_send": settings.llm_allow_evidence_send,
    }
    config_refs = (
        config_ref,
        {"id": "stage10-runtime-settings", "settings": runtime_settings, "sha256": sha256_value(runtime_settings)},
    )
    producer = producer_ref(ROOT, (Path(__file__), ROOT / "src/turbine_kg/terminology/analyzer.py", ROOT / "src/turbine_kg/terminology/validation.py"), label="stage7-terminology-analyzer")

    def build_output() -> dict:
        stage6_rows = load_stage6_evidence_bundle(stage6_path)
        contract = load_terminology_contract(config_path)
        page_texts = _accepted_page_texts(manifest, settings, stage6_rows)
        candidates = analyze_terminology(manifest, page_texts, stage6_rows=stage6_rows, contract=contract)
        accepted_keys = {(row["document_logical_id"], row["physical_page"]) for row in manifest["pages"] if row["page_status"] == "text_accepted"}
        validate_candidates(candidates, accepted_keys)
        return {"schema_version": 1, "stage": "7", "artifact_kind": "terminology_candidates", "status": "candidate_only", "formal_release": False, "producer": "turbine_kg.terminology.analyzer", "inputs": {"terminology_input_manifest": manifest_ref, "stage6_evidence_bundle": stage6_ref}, "contract_sha256": config_ref["sha256"], "candidates": candidates}

    run, output = run_with_cache(
        cache_root=ROOT / "var/model_runs/stage10",
        operation="terminology_extraction",
        input_refs=(manifest_ref, stage6_ref), config_refs=config_refs, producer=producer,
        output=build_output, schema_path=ROOT / "config/runtime_run.schema.json", force=force,
        validate_input=lambda: _runtime_input_gate(manifest, settings),
        validate_output=lambda value: validate_candidates(value["candidates"], {(row["document_logical_id"], row["physical_page"]) for row in manifest["pages"] if row["page_status"] == "text_accepted"}),
        legacy_refs=({"kind": "stage7_frozen_candidate", "path": "data/stage7/terminology_candidates.json"},),
    )
    print(json.dumps({"status": run.status, "run_id": run.run_id, "cache_key": run.cache_key, "candidate_count": len(output["candidates"])}, ensure_ascii=False))


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
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", action="store_true", help="run in the Stage 10 isolated runtime/cache")
    parser.add_argument("--force", action="store_true", help="force a new run; valid only with --runtime")
    args = parser.parse_args()
    if args.force and not args.runtime:
        parser.error("--force requires --runtime")
    settings = Settings.from_environment()
    manifest = _read(STAGE7 / "terminology_input_manifest.json")
    validate_input_manifest(manifest)
    if args.runtime:
        _runtime_run(manifest, settings, force=args.force)
        return
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

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
from turbine_kg.observability.runtime import run_with_cache, sha256_value
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


def _registry_scope(manifest: dict) -> list[dict]:
    """Snapshot only source identities actually consumed by accepted pages."""
    assets = {}
    for line in (ROOT / "data/registry/source_assets.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            assets[row["asset_id"]] = row
    asset_ids = {
        page[field]
        for page in manifest["pages"] if page["page_status"] == "text_accepted"
        for field in ("processing_asset_id", "authority_asset_id")
    }
    if not asset_ids <= assets.keys():
        raise ValueError("Stage 7 manifest references a missing Registry asset")
    return [assets[asset_id] for asset_id in sorted(asset_ids)]


def _stage7_registry_ref(manifest: dict) -> dict:
    fields = ("asset_id", "relative_path", "sha256", "document_logical_id", "revision_id")
    scope = [{field: row[field] for field in fields} for row in _registry_scope(manifest)]
    return {
        "kind": "registry_scope", "path": "data/registry/source_assets.jsonl",
        "asset_ids": [row["asset_id"] for row in scope], "sha256": sha256_value(scope),
    }


def _runtime_input_gate(manifest: dict, settings: Settings, registry_ref: dict | None = None) -> None:
    """Recheck consumed assets, current admission and manifest path bindings."""
    assets = {row["asset_id"]: row for row in _registry_scope(manifest)}
    if registry_ref is not None and _stage7_registry_ref(manifest) != registry_ref:
        raise ValueError("Stage 7 Registry scope changed during the runtime request")
    _, allowlist = load_allowlist(ROOT / "config/source_allowlist.tsv")
    allowlisted = {row["path"]: row for row in allowlist}
    for page in manifest["pages"]:
        if page["page_status"] != "text_accepted":
            continue
        original = assets[page["authority_asset_id"]]
        if original.get("asset_kind") != "original" or original.get("admission_status") != "admitted":
            raise ValueError(f"runtime manifest original is not currently admitted: {page['page_id']}")
        processing = assets[page["processing_asset_id"]]
        if processing.get("text_adapter_status") not in {"native_text_available", "ocr_validated"}:
            raise ValueError(f"runtime manifest processing text is not currently ready: {page['page_id']}")
        for asset, path_field in ((processing, "processing_relative_path"), (original, "authority_relative_path")):
            if asset["relative_path"] != page[path_field]:
                raise ValueError(f"runtime manifest {path_field} differs from Registry: {page['page_id']}")
            if asset["document_logical_id"] != page["document_logical_id"] or asset["revision_id"] != page["revision_id"]:
                raise ValueError(f"runtime manifest identity differs from Registry: {page['page_id']}")
    # Hash each distinct input once, rather than once for every manifest page.
    for asset in assets.values():
        declared = allowlisted.get(asset["relative_path"])
        if declared is None or declared["sha256"] != asset["sha256"]:
            raise ValueError(f"runtime Registry asset differs from its allowlist: {asset['asset_id']}")
        path = resolve_allowlisted_path(asset["relative_path"], settings.source_root, settings.ocr_derived_root)
        if not path.is_file() or sha256_file(path) != asset["sha256"]:
            raise ValueError(f"runtime manifest asset fingerprint differs from Registry: {asset['asset_id']}")


def _validate_runtime_output(value: dict, expected_inputs: dict, contract_sha: str, accepted_keys: set) -> None:
    expected_header = {
        "schema_version": 1, "stage": "7", "artifact_kind": "terminology_candidates",
        "status": "candidate_only", "formal_release": False,
        "producer": "turbine_kg.terminology.analyzer",
        "inputs": expected_inputs, "contract_sha256": contract_sha,
    }
    if not isinstance(value, dict) or any(value.get(key) != expected for key, expected in expected_header.items()):
        raise ValueError("cached Stage 7 output header or input binding differs from the request")
    if not isinstance(value.get("candidates"), list):
        raise ValueError("Stage 7 output candidates must be an array")
    validate_candidates(value["candidates"], accepted_keys)


def _stage7_cache_context(root: Path) -> tuple[dict]:
    """Return only direct producer dependencies that can change candidates.

    Runtime settings such as the configured LLM are intentionally excluded:
    this producer is deterministic and does not call an LLM.
    """
    dependencies = (
        Path(__file__),
        root / "src/turbine_kg/terminology/analyzer.py",
        root / "src/turbine_kg/terminology/models.py",
        root / "src/turbine_kg/terminology/validation.py",
        root / "src/turbine_kg/documents/ids.py",
    )
    return ({
        "implementation": "stage7-terminology-analyzer",
        "files": [
            {"path": str(path.relative_to(root)).replace("\\", "/"), "sha256": _sha(path)}
            for path in dependencies
        ],
    },)


def _runtime_run(manifest: dict, settings: Settings, *, force: bool) -> None:
    stage6_path = ROOT / "data/stage6/stage6_evidence_bundle.jsonl"
    manifest_ref = {"kind": "input_manifest", "id": "stage7-input-manifest", "path": "data/stage7/terminology_input_manifest.json", "sha256": _sha(STAGE7 / "terminology_input_manifest.json")}
    stage6_ref = {"kind": "evidence_bundle", "id": "stage6-evidence-bundle", "path": "data/stage6/stage6_evidence_bundle.jsonl", "sha256": _sha(stage6_path)}
    revision_scope = sorted({
        (row["document_logical_id"], row["revision_id"])
        for row in manifest["pages"] if row["page_status"] == "text_accepted"
    })
    revision_ref = {
        "kind": "revision_scope",
        "revisions": [{"document_logical_id": document_id, "revision_id": revision_id} for document_id, revision_id in revision_scope],
        "content_fingerprint": sha256_value(revision_scope),
    }
    config_path = ROOT / "config/terminology_contract.json"
    config_ref = {"kind": "contract", "id": "stage7-terminology-contract", "path": "config/terminology_contract.json", "sha256": _sha(config_path)}
    registry_ref = _stage7_registry_ref(manifest)
    cache_context = _stage7_cache_context(ROOT)
    output_inputs = {"terminology_input_manifest": manifest_ref, "stage6_evidence_bundle": stage6_ref, "registry_scope": registry_ref}
    accepted_keys = {(row["document_logical_id"], row["physical_page"]) for row in manifest["pages"] if row["page_status"] == "text_accepted"}

    def build_output() -> dict:
        stage6_rows = load_stage6_evidence_bundle(stage6_path)
        contract = load_terminology_contract(config_path)
        page_texts = _accepted_page_texts(manifest, settings, stage6_rows)
        candidates = analyze_terminology(manifest, page_texts, stage6_rows=stage6_rows, contract=contract)
        return {"schema_version": 1, "stage": "7", "artifact_kind": "terminology_candidates", "status": "candidate_only", "formal_release": False, "producer": "turbine_kg.terminology.analyzer", "inputs": output_inputs, "contract_sha256": config_ref["sha256"], "candidates": candidates}

    run, output = run_with_cache(
        cache_root=ROOT / "var/model_runs/stage10",
        operation="terminology_extraction",
        input_refs=(manifest_ref, stage6_ref, revision_ref, config_ref, registry_ref), cache_context=cache_context,
        output=build_output, schema_path=ROOT / "config/runtime_run.schema.json", force=force,
        validate_input=lambda: _runtime_input_gate(manifest, settings, registry_ref),
        validate_output=lambda value: _validate_runtime_output(value, output_inputs, config_ref["sha256"], accepted_keys),
    )
    print(json.dumps({"status": run.status, "extraction_batch_id": run.extraction_batch_id, "candidate_count": len(output["candidates"])}, ensure_ascii=False))


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
        path = resolve_allowlisted_path(relative_path, settings.source_root, settings.ocr_derived_root)
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
    _runtime_input_gate(manifest, settings)
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

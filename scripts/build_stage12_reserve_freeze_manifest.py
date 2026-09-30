"""Prepare and verify the Stage 12 Reserve freeze manifest.

The command records the exact inputs that a later, human-approved one-shot
Reserve run must use.  It does not freeze Gold and it never calls a provider.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "data/stage12/stage12_reserve_freeze_manifest.json"

ASSETS = {
    "reserve_gold": "data/stage12/stage12_reserve_gold.jsonl",
    "reserve_source_regions": "data/stage12/stage12_reserve_source_regions.json",
    "reserve_gold_reviews": "data/stage12/stage12_reserve_gold_reviews.json",
    "reserve_consistency_audit": "data/stage12/stage12_reserve_gold_audit.json",
    "reserve_evidence": "data/stage12/stage12_reserve_evidence.jsonl",
    "reserve_evidence_builder": "scripts/build_stage12_reserve_evidence.py",
    "evaluation_sample_registry": "data/stage11/evaluation_sample_registry.json",
    "source_asset_registry": "data/registry/source_assets.jsonl",
    "profile_routing": "config/stage12_profile_routing.json",
    "stage12_contract": "config/stage12_statement_contract.json",
    "extraction_prompt": "config/stage12_prompt.txt",
    "response_schema": "config/stage12_extraction_response.schema.json",
    "candidate_schema": "config/stage12_candidate.schema.json",
    "semantic_validation": "src/turbine_kg/extraction/semantic.py",
    "llm_transport": "src/turbine_kg/llm_client.py",
    "stage9_semantic_validation": "src/turbine_kg/ontology/semantic.py",
    "stage9_runtime_schema": "config/semantic_runtime.schema.json",
    "stage9_ontology": "ontology/stage9_core.ttl",
    "stage9_shapes": "ontology/stage9_shapes.ttl",
    "stage8_imported_ontology": "ontology/minimal_turbine.ttl",
    "canonicalization_logic": "scripts/build_stage12_candidates.py",
    "provider_config": "config/stage12_provider.json",
    "reserve_runner": "scripts/stage12_reserve_pipeline.py",
    "reserve_evaluator": "scripts/stage12_reserve_pipeline.py",
    "gold_consistency_audit": "scripts/build_stage12_reserve_gold.py",
    "acceptance_gate": "scripts/audit_stage12_exit.py",
    "stage12_exit_logic": "scripts/audit_stage12_exit.py",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def asset_hashes(root: Path = ROOT) -> dict[str, str]:
    missing = [name for name, relative in ASSETS.items() if not (root / relative).is_file()]
    if missing:
        raise FileNotFoundError("missing freeze assets: " + ", ".join(missing))
    return {name: sha256(root / relative) for name, relative in ASSETS.items()}


def build_manifest(root: Path = ROOT) -> dict:
    hashes = asset_hashes(root)
    return {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_reserve_freeze_manifest",
        "freeze_status": "prepared_not_approved",
        "formal_release": False,
        "approval_token": None,
        "provider_runtime_fingerprint": None,
        "source_split": "acceptance_holdout_reserve",
        "assets": {name: {"path": relative, "sha256": hashes[name]} for name, relative in ASSETS.items()},
        "execution_policy": {
            "real_provider_requires_human_approval": True,
            "one_shot_only": True,
            "candidate_must_remain_candidate_only": True,
            "fixture_allowed_only_for_synthetic_evidence": True,
        },
        "producer": "scripts/build_stage12_reserve_freeze_manifest.py",
    }


def verify_manifest(manifest: dict, root: Path = ROOT) -> dict:
    issues: list[str] = []
    if manifest.get("artifact_kind") != "stage12_reserve_freeze_manifest":
        issues.append("artifact_kind")
    if manifest.get("source_split") != "acceptance_holdout_reserve":
        issues.append("source_split")
    if manifest.get("formal_release") is not False:
        issues.append("formal_release")
    assets = manifest.get("assets")
    if not isinstance(assets, dict) or set(assets) != set(ASSETS):
        issues.append("asset_set")
        assets = assets if isinstance(assets, dict) else {}
    for name, relative in ASSETS.items():
        expected = assets.get(name)
        path = root / relative
        if not isinstance(expected, dict) or expected.get("path") != relative or not path.is_file() or expected.get("sha256") != sha256(path):
            issues.append(name)
    return {
        "valid": not issues,
        "issues": sorted(set(issues)),
        "freeze_status": manifest.get("freeze_status"),
        "human_approval_present": (
            manifest.get("freeze_status") == "human_approved"
            and bool(manifest.get("approval_token"))
            and isinstance(manifest.get("provider_runtime_fingerprint"), str)
            and len(manifest["provider_runtime_fingerprint"]) == 64
        ),
    }


if __name__ == "__main__":
    if MANIFEST_PATH.exists():
        prior = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if prior.get("freeze_status") != "prepared_not_approved":
            raise FileExistsError("approved or unknown freeze manifest exists; refusing to overwrite its audit history")
    manifest = build_manifest()
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"path": str(MANIFEST_PATH), "verification": verify_manifest(manifest)}, ensure_ascii=False))

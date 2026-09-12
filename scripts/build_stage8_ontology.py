"""Build the minimal Stage 8 OWL and stop at the manual mapping review gate."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from turbine_kg.ontology.builder import (  # noqa: E402
    apply_mapping_review_overlay,
    build_mapping_payload,
    build_review_queue,
    load_json,
    render_turtle,
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    contract = load_json(ROOT / "config" / "ontology_contract.json")
    manifest_path = ROOT / "data" / "stage7" / "terminology_input_manifest.json"
    candidates = load_json(ROOT / "data" / "stage7" / "terminology_candidates.json")
    capabilities = load_json(ROOT / "data" / "stage7" / "business_capability_questions.json")
    overlay_path = ROOT / "data" / "stage8" / "ontology_mapping_review_overlay.jsonl"
    manifest = load_json(manifest_path)
    if manifest.get("status") != "frozen" or manifest.get("input_boundary", {}).get("source_count") != 5:
        raise ValueError("Stage 8 requires the frozen five-unit Stage 7 input manifest")
    stage8 = ROOT / "data" / "stage8"
    stage8.mkdir(parents=True, exist_ok=True)

    (ROOT / "ontology" / "minimal_turbine.ttl").write_text(render_turtle(contract), encoding="utf-8")
    mapping = build_mapping_payload(contract, candidates, capabilities)
    mapping = apply_mapping_review_overlay(mapping, load_jsonl(overlay_path))
    mapping["inputs"] = {
        "terminology_input_manifest": {"path": "data/stage7/terminology_input_manifest.json", "sha256": file_sha256(manifest_path)},
        "terminology_candidates": {"path": "data/stage7/terminology_candidates.json", "sha256": file_sha256(ROOT / "data" / "stage7" / "terminology_candidates.json")},
        "business_capability_questions": {"path": "data/stage7/business_capability_questions.json", "sha256": file_sha256(ROOT / "data" / "stage7" / "business_capability_questions.json")},
        "ontology_contract": {"path": "config/ontology_contract.json", "sha256": file_sha256(ROOT / "config" / "ontology_contract.json")},
        "mapping_review_overlay": {"path": "data/stage8/ontology_mapping_review_overlay.jsonl", "sha256": file_sha256(overlay_path)},
    }
    (stage8 / "ontology_mapping_shortlist.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    queue = build_review_queue(mapping)
    (stage8 / "ontology_mapping_review_queue.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in queue), encoding="utf-8"
    )
    entry = {
        "schema_version": 1,
        "stage": "8",
        "artifact_kind": "stage8_entry_audit",
        "status": "complete" if not queue else "in_progress",
        "formal_release": False,
        "producer": "scripts/build_stage8_ontology.py",
        "inputs": mapping["inputs"],
        "outputs": {
            "ontology": "ontology/minimal_turbine.ttl",
            "mapping_shortlist": "data/stage8/ontology_mapping_shortlist.json",
            "review_queue": "data/stage8/ontology_mapping_review_queue.jsonl",
            "exit_audit": "data/stage8/stage8_exit_audit.json",
        },
        "counts": {
            "shortlist_count": len(mapping["shortlist"]),
            "reviewed_accepted_count": mapping["review_summary"]["accepted_count"],
            "reviewed_deferred_count": mapping["review_summary"]["deferred_count"],
            "review_pending_count": sum(row["review_status"] == "pending_manual_review" for row in queue),
            "original_page_confirmation_pending_count": sum(
                row["review_gate"] == "original_page_confirmation" for row in queue
            ),
        },
        "checks": {
            "minimal_owl_built": True,
            "stage7_candidate_only_input": True,
            "candidate_content_fingerprints_bound": all(
                row.get("candidate_content_fingerprint") for row in mapping["shortlist"]
            ),
            "original_page_evidence_bound": all(row.get("source_occurrences") for row in mapping["shortlist"]),
            "automatic_promotion_disabled": mapping["automatic_promotion"] is False,
            "numeric_only_values_excluded": all(row["candidate_type"] != "parameter" for row in mapping["shortlist"]),
            "sentence_and_scope_fragments_excluded": all(row["candidate_type"] != "applicability_condition" for row in mapping["shortlist"]),
            "review_overlay_fingerprints_bound": True,
            "candidate_dispositions_recorded": all(
                row.get("candidate_disposition") in mapping["candidate_disposition_schema"]
                for row in mapping["shortlist"] + mapping.get("deferred_candidates", [])
            ),
            "future_scope_deferred": all(item in mapping["deferred_scope"] for item in ("EngineeringCase", "Release")),
            "manual_mapping_review_complete": len(queue) == 0,
        },
        "review_boundary": (
            "Stage 8 mapping review is complete; no formal vocabulary or Release is produced."
            if not queue
            else "Stage 8 stops here until a human reviews the independent mapping overlay; no formal vocabulary or Release is produced."
        ),
        "next_stage_allowed": not queue,
        "next_stage": "Stage 9 OWL/SHACL semantic authority package" if not queue else "Stage 8 manual ontology mapping review",
    }
    (stage8 / "stage8_entry_audit.json").write_text(
        json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not queue:
        (stage8 / "stage8_exit_audit.json").write_text(
            json.dumps({
                "schema_version": 1,
                "stage": "8",
                "artifact_kind": "stage8_exit_audit",
                "status": "complete",
                "formal_release": False,
                "producer": "scripts/build_stage8_ontology.py",
                "inputs": mapping["inputs"],
                "outputs": entry["outputs"],
                "counts": entry["counts"],
                "checks": {
                    **entry["checks"],
                    "manual_mapping_review_complete": True,
                    "no_pending_review_queue": True,
                    "no_formal_vocabulary_or_release": True,
                },
                "next_stage_allowed": True,
                "next_stage": "Stage 9 OWL/SHACL semantic authority package",
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({
        "status": mapping["status"],
        "shortlist_count": len(mapping["shortlist"]),
        "review_pending_count": len(queue),
        "manual_review_required": bool(queue),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()

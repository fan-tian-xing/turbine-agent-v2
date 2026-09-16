"""Freeze a label-free, development-only representative Stage 12 input view."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from turbine_kg.extraction.semantic import ProfileRouter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/stage12/stage12_input_manifest.json"
BASELINE = ROOT / "data/stage12/stage12_representative_baseline.json"
ROUTING = ROOT / "config/stage12_profile_routing.json"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build() -> dict:
    audit = json.loads((ROOT / "data/stage11/stage11_exit_audit.json").read_text(encoding="utf-8"))
    if audit.get("status") != "complete" or not audit.get("checks", {}).get("stage12_entry_allowed"):
        raise ValueError("Stage 11 is not open for Stage 12")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    if baseline.get("scope_kind") != "representative_page_baseline" or baseline.get("representative_chapter_claim_allowed") is not False:
        raise ValueError("Stage 12 baseline is not an explicitly limited page baseline")
    router = ProfileRouter(ROUTING)
    registry = json.loads((ROOT / "data/stage11/evaluation_sample_registry.json").read_text(encoding="utf-8"))
    records = registry.get("records", [])
    pages_by_split: dict[str, set[tuple[str, int]]] = {}
    for record in records:
        pages_by_split.setdefault(record["split"], set()).add((record["document_logical_id"], int(record["physical_page"])))
    evidence = {row["evidence"]["evidence_id"]: row["evidence"] for row in _rows(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")}
    pages = []
    dev_pages = pages_by_split.get("development_regression_golden", set())
    holdout_pages = pages_by_split.get("acceptance_holdout", set()) | pages_by_split.get("acceptance_holdout_reserve", set())
    for baseline_page in baseline.get("pages", []):
        identity = (baseline_page["document_logical_id"], int(baseline_page["physical_page"]))
        if identity not in dev_pages or identity in holdout_pages:
            raise ValueError(f"baseline page is not a development-only Registry page: {identity}")
        ids = list(baseline_page.get("evidence_ids", []))
        if not ids or any(evidence_id not in evidence for evidence_id in ids):
            raise ValueError(f"representative page has no canonical Evidence: {identity}")
        page_evidence = [evidence[evidence_id] for evidence_id in ids]
        if any(item.get("review_status") != "accepted" for item in page_evidence):
            raise ValueError(f"baseline includes non-accepted Evidence: {identity}")
        profiles = {router.route(item) for item in page_evidence}
        if len(profiles) != 1:
            raise ValueError(f"baseline page has ambiguous profile routing: {identity}")
        profile = next(iter(profiles))
        if any(item["document_logical_id"] != identity[0] or item["revision_id"] != baseline_page["revision_id"] or int(item["locations"][0]["physical_page"]) != identity[1] for item in page_evidence):
            raise ValueError(f"baseline page identity differs from canonical Evidence: {identity}")
        pages.append({
            "document_key": baseline_page["document_key"],
            "physical_page": identity[1],
            "document_logical_id": identity[0],
            "revision_id": baseline_page["revision_id"],
            "logical_page": next(iter({item.get("locations", [{}])[0].get("logical_page") for item in page_evidence})),
            "evidence_ids": ids,
            "source_text_sha256": sorted({item["source_text_sha256"] for item in page_evidence}),
            "coverage": baseline_page.get("coverage", []),
            "section_path": baseline_page.get("section_path", "chapter_unknown"),
            "section_identity_status": "unresolved",
            "extraction_profile_id": profile.extraction_profile_id,
            "semantic_role": profile.semantic_role,
        })
    return {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_development_input_manifest",
        "status": "frozen",
        "formal_release": False,
        "producer": "scripts/build_stage12_input_manifest.py",
        "source_split": "development_regression_golden",
        "label_free_extractor_view": True,
        "holdout_used_for_tuning": False,
        "blind_read": False,
        "scope_kind": baseline["scope_kind"],
        "representative_chapter_claim_allowed": False,
        "representative_criteria": sorted({item for page in pages for item in page["coverage"]}),
        "pages": pages,
        "inputs": {
            "stage9_exit_audit": _sha(ROOT / "data/stage9/stage9_exit_audit.json"),
            "stage10_audit": _sha(ROOT / "data/stage10/stage10_audit.json"),
            "stage11_exit_audit": _sha(ROOT / "data/stage11/stage11_exit_audit.json"),
            "stage6_evidence_bundle": _sha(ROOT / "data/stage6/stage6_evidence_bundle.jsonl"),
            "stage11_evaluation_sample_registry": _sha(ROOT / "data/stage11/evaluation_sample_registry.json"),
            "stage12_representative_baseline": _sha(BASELINE),
            "stage12_profile_routing": _sha(ROUTING),
        },
        "consumers": ["scripts/build_stage12_candidates.py"],
    }


if __name__ == "__main__":
    result = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "page_count": len(result["pages"]), "documents": sorted({p["document_key"] for p in result["pages"]})}, ensure_ascii=False))

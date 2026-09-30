"""Freeze the label-free Stage 11 Development pages for Stage 12 extraction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from turbine_kg.extraction.semantic import ProfileRouter
from turbine_kg.settings import Settings

try:
    from scripts.build_stage6_golden_evidence import DOCUMENTS
except ModuleNotFoundError:  # direct execution from the scripts directory
    from build_stage6_golden_evidence import DOCUMENTS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/stage12/stage12_input_manifest.json"
BASELINE = ROOT / "data/stage12/stage12_representative_baseline.json"
ROUTING = ROOT / "config/stage12_profile_routing.json"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _audit_inputs_current(audit: dict) -> bool:
    """Reject a completed upstream audit whose recorded source files changed."""
    inputs = audit.get("inputs")
    if not isinstance(inputs, dict) or not inputs:
        return False
    for entry in inputs.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not isinstance(entry.get("sha256"), str):
            return False
        path = (ROOT / entry["path"]).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file() or _sha(path) != entry["sha256"]:
            return False
    for name, expected in (audit.get("input_sha256") or {}).items():
        entry = inputs.get(name)
        if not isinstance(entry, dict) or entry.get("sha256") != expected:
            return False
    return True


def upstream_lineage_current(stage6_coverage: dict, stage11_audit: dict) -> tuple[bool, bool]:
    stage6_current = _audit_inputs_current(stage6_coverage)
    pdf_hashes = stage6_coverage.get("original_pdf_sha256")
    if not isinstance(pdf_hashes, dict) or set(pdf_hashes) != set(DOCUMENTS):
        stage6_current = False
    else:
        source_root = Settings.from_environment().source_root
        for key, document in DOCUMENTS.items():
            path = source_root / document["original"]
            if not path.is_file() or _sha(path) != pdf_hashes[key]:
                stage6_current = False
                break
    stage11_current = _audit_inputs_current(stage11_audit)
    return stage6_current, stage11_current


def build() -> dict:
    audit = json.loads((ROOT / "data/stage11/stage11_exit_audit.json").read_text(encoding="utf-8"))
    stage6_coverage = json.loads((ROOT / "data/stage6/stage6_semantic_coverage_audit.json").read_text(encoding="utf-8"))
    stage6_lineage_current, stage11_lineage_current = upstream_lineage_current(stage6_coverage, audit)
    stage11_ready = audit.get("status") == "complete" and audit.get("checks", {}).get("stage12_entry_allowed") is True and stage11_lineage_current
    stage6_ready = stage6_coverage.get("status") == "complete" and stage6_coverage.get("stage12_input_allowed") is True and stage6_lineage_current
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    if baseline.get("scope_kind") != "representative_page_baseline" or baseline.get("representative_chapter_claim_allowed") is not False:
        raise ValueError("Stage 12 regression baseline is not an explicitly limited page baseline")
    router = ProfileRouter(ROUTING)
    registry = json.loads((ROOT / "data/stage11/evaluation_sample_registry.json").read_text(encoding="utf-8"))
    records = registry.get("records", [])
    pages_by_split: dict[str, set[tuple[str, int]]] = {}
    for record in records:
        pages_by_split.setdefault(record["split"], set()).add((record["document_logical_id"], int(record["physical_page"])))
    evidence_rows = _rows(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")
    evidence_by_page: dict[tuple[str, str, int], list[dict]] = {}
    for row in evidence_rows:
        item = row["evidence"]
        if not item.get("locations"):
            continue
        key = (item["document_logical_id"], item["revision_id"], int(item["locations"][0]["physical_page"]))
        evidence_by_page.setdefault(key, []).append(row)
    pages = []
    dev_records = [item for item in records if item["split"] == "development_regression_golden"]
    dev_pages = pages_by_split.get("development_regression_golden", set())
    holdout_pages = pages_by_split.get("acceptance_holdout", set()) | pages_by_split.get("acceptance_holdout_reserve", set())
    baseline_by_identity = {(item["document_logical_id"], int(item["physical_page"])): item for item in baseline.get("pages", [])}
    if len(dev_records) != len(dev_pages):
        raise ValueError("Stage 11 Development registry repeats a page identity")
    if len(dev_records) != registry.get("development", {}).get("page_count"):
        raise ValueError("Stage 11 Development page count differs from its frozen registry")
    for record in dev_records:
        identity = (record["document_logical_id"], int(record["physical_page"]))
        if identity not in dev_pages or identity in holdout_pages:
            raise ValueError(f"page is not a development-only Registry page: {identity}")
        key = (record["document_logical_id"], record["revision_id"], int(record["physical_page"]))
        all_rows = evidence_by_page.get(key, [])
        # Region-scoped text may contain useful engineering propositions and
        # must reach the model. A table region without reviewed cell values is
        # only context until Stage 6 promotes trustworthy cell Evidence.
        page_evidence = [row["evidence"] for row in all_rows if row["evidence"].get("review_status") == "accepted" and row["evidence"].get("content_kind") != "table" and row.get("stage12_extractability") not in {"pending_review", "context_only"}]
        context_only = [row for row in all_rows if row.get("stage12_extractability") == "context_only"]
        excluded = [row["evidence"] for row in all_rows if row["evidence"] not in page_evidence and row not in context_only]
        profile = router.route(page_evidence[0]) if page_evidence else None
        if profile is not None and any(router.route(item) != profile for item in page_evidence):
            raise ValueError(f"page has ambiguous profile routing: {identity}")
        baseline_page = baseline_by_identity.get(identity, {})
        status = "pending_region_review" if excluded else "extractable" if page_evidence else "context_only" if context_only else "no_canonical_evidence"
        pages.append({
            "document_key": record["document_key"],
            "physical_page": identity[1],
            "document_logical_id": identity[0],
            "revision_id": record["revision_id"],
            "logical_page": page_evidence[0].get("locations", [{}])[0].get("logical_page") if page_evidence else None,
            "evidence_ids": [item["evidence_id"] for item in page_evidence],
            "excluded_evidence_ids": [item["evidence_id"] for item in excluded],
            "context_only_evidence_ids": [row["evidence"]["evidence_id"] for row in context_only],
            "context_only_reasons": {row["evidence"]["evidence_id"]: row.get("extractability_reason", "Stage 6 context only") for row in context_only},
            "coverage_status": status,
            "source_text_sha256": sorted({item["source_text_sha256"] for item in page_evidence}) or [record["source_text_sha256"]],
            "coverage": baseline_page.get("coverage", []),
            "section_path": baseline_page.get("section_path", "chapter_unknown"),
            "section_identity_status": "unresolved",
            "extraction_profile_id": profile.extraction_profile_id if profile else None,
            "semantic_role": profile.semantic_role if profile else None,
        })
    return {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_development_input_manifest",
        "status": "frozen" if stage6_ready and stage11_ready else "prepared_pending_upstream",
        "upstream_blockers": [name for name, ready in (("stage6_semantic_coverage", stage6_ready), ("stage6_inner_lineage", stage6_lineage_current), ("stage11_exit", stage11_ready), ("stage11_inner_lineage", stage11_lineage_current)) if not ready],
        "formal_release": False,
        "producer": "scripts/build_stage12_input_manifest.py",
        "source_split": "development_regression_golden",
        "label_free_extractor_view": True,
        "holdout_used_for_tuning": False,
        "blind_read": False,
        "scope_kind": "development_page_registry",
        "representative_chapter_claim_allowed": False,
        "representative_criteria": sorted({item for page in pages for item in page["coverage"]}),
        "coverage_summary": {
            "registered_pages": len(pages),
            "extractable_evidence": sum(len(page["evidence_ids"]) for page in pages),
            "excluded_evidence": sum(len(page["excluded_evidence_ids"]) for page in pages),
            "context_only_evidence": sum(len(page["context_only_evidence_ids"]) for page in pages),
            "pages_without_canonical_evidence": sum(not page["evidence_ids"] and not page["excluded_evidence_ids"] and not page["context_only_evidence_ids"] for page in pages),
            "pages_without_extractable_evidence": sum(not page["evidence_ids"] for page in pages),
        },
        "pages": pages,
        "inputs": {
            "stage9_exit_audit": _sha(ROOT / "data/stage9/stage9_exit_audit.json"),
            "stage10_audit": _sha(ROOT / "data/stage10/stage10_audit.json"),
            "stage11_exit_audit": _sha(ROOT / "data/stage11/stage11_exit_audit.json"),
            "stage6_evidence_bundle": _sha(ROOT / "data/stage6/stage6_evidence_bundle.jsonl"),
            "stage6_semantic_coverage_audit": _sha(ROOT / "data/stage6/stage6_semantic_coverage_audit.json"),
            "stage6_source_structure_index": _sha(ROOT / "data/stage6/stage6_source_structure_index.json"),
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

"""Run a small, cache-bypassing Stage 12 Development diagnosis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from turbine_kg.extraction.semantic import ProfileRouter, provider_from_config

from build_stage12_candidates import (
    _extract_with_evidence_cache,
    _jsonl,
    _gate,
    prune_stale_evidence_cache,
)
from stage12_failure_summary import decorate_event, write_failure_summary

ROOT = Path(__file__).resolve().parents[1]
STAGE12 = ROOT / "data/stage12"


def _evidence_map() -> dict[str, dict]:
    return {row["evidence"]["evidence_id"]: row["evidence"] for row in _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")}


def _selected_ids(manifest: dict, requested: list[str], limit: int) -> list[str]:
    allowed = [evidence_id for page in manifest.get("pages", []) for evidence_id in page.get("evidence_ids", [])]
    if requested:
        unknown = sorted(set(requested) - set(allowed))
        if unknown:
            raise ValueError(f"diagnostic Evidence is outside the frozen Development manifest: {unknown}")
        return list(dict.fromkeys(requested))
    return list(dict.fromkeys(allowed))[:limit]


def diagnose(*, evidence_ids: list[str] | None = None, limit: int = 2) -> dict:
    manifest = json.loads((STAGE12 / "stage12_input_manifest.json").read_text(encoding="utf-8"))
    _gate(manifest)
    contract = json.loads((ROOT / "config/stage12_statement_contract.json").read_text(encoding="utf-8"))
    cache_root = ROOT / contract["runtime"]["cache_root"]
    maintenance = prune_stale_evidence_cache(manifest, cache_root)
    from build_stage12_candidates import latest_completed_batch_id, prune_stale_batch_records
    current_batch_id = latest_completed_batch_id(cache_root)
    if current_batch_id:
        maintenance["batch_records_deleted"] = prune_stale_batch_records(cache_root, current_batch_id)
    evidence_by_id = _evidence_map()
    selected = _selected_ids(manifest, evidence_ids or [], limit)
    router = ProfileRouter()
    provider = provider_from_config()
    if getattr(provider, "metadata", {}).get("mode") != "real_llm":
        raise ValueError("Stage 12 diagnostic requires the configured real_llm provider")
    events = []
    page_by_evidence = {
        evidence_id: page
        for page in manifest.get("pages", [])
        for evidence_id in page.get("evidence_ids", [])
    }
    for evidence_id in selected:
        page = page_by_evidence[evidence_id]
        evidence = dict(evidence_by_id[evidence_id], document_key=page["document_key"])
        profile = router.route(evidence)
        observer = lambda event, item=evidence: events.append(decorate_event(item, dict(event)))
        try:
            _extract_with_evidence_cache(
                evidence,
                profile,
                provider,
                manifest["source_split"],
                cache_root,
                force=True,
                attempt_observer=observer,
            )
        except Exception as error:
            if not any(item.get("evidence_id") == evidence_id for item in events):
                events.append(decorate_event(evidence, {
                    "attempt": None,
                    "outcome": "failure",
                    "failure_type": "transport_failure" if "provider failed" in str(error) else "semantic_validation_failure",
                    "field": None,
                    "validator_reason": str(error),
                    "model_value_or_text": None,
                }))
    summary = write_failure_summary(
        events,
        run_kind="controlled_development_diagnostic",
        evidence_ids=selected,
        cache_maintenance=maintenance,
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=2)
    args = parser.parse_args()
    report = diagnose(evidence_ids=args.evidence_id, limit=args.limit)
    print(json.dumps({"status": report["status"], "evidence_ids": report["evidence_ids"], "counts": report["counts"]}, ensure_ascii=False))

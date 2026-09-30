"""Run the Stage 12 development extractor through the Stage 10 runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from turbine_kg.extraction.semantic import (
    _normalized_text,
    _statement_asserts_question_answer,
    _text_similarity,
    SOURCE_LIST_KINDS,
    source_step_local_match,
    source_step_clause_coverage,
    ProfileRouter,
    ProviderBackedExtractor,
    compare_candidates,
    extraction_contract_fingerprint,
    extraction_source_fingerprint,
    load_contract,
    provider_from_config,
    to_stage9_runtime_payload,
    validate_candidate_against_evidence,
    validate_source_unit_coverage,
    candidate_review_diagnostics,
    validate_candidate_evidence_binding,
    validate_candidate_payload,
)
from turbine_kg.observability.runtime import canonical_json, run_with_cache
try:
    from scripts.stage12_failure_summary import decorate_event, write_failure_summary
    from scripts.build_stage12_development_adjudication import OUTPUT_PATH, RAW_EVALUATION_PATH
    from scripts.build_stage12_input_manifest import upstream_lineage_current
except ModuleNotFoundError:  # direct execution from the scripts directory
    from stage12_failure_summary import decorate_event, write_failure_summary
    from build_stage12_development_adjudication import OUTPUT_PATH, RAW_EVALUATION_PATH
    from build_stage12_input_manifest import upstream_lineage_current

ROOT = Path(__file__).resolve().parents[1]
STAGE12 = ROOT / "data/stage12"


class IncompleteDevelopmentError(RuntimeError):
    """Raised after all Evidence was attempted but one or more items failed."""

    def __init__(self, progress: dict[str, Any]):
        super().__init__("Stage 12 Development batch is incomplete")
        self.progress = progress


def _evidence_cache_key(evidence: dict, profile, provider, split: str) -> str:
    provider_key_metadata = provider.cache_key_metadata() if hasattr(provider, "cache_key_metadata") else getattr(provider, "metadata", {})
    material = {
        "evidence": evidence,
        "profile": {
            "semantic_role": profile.semantic_role,
            "extraction_profile_id": profile.extraction_profile_id,
            "source_profile_id": profile.source_profile_id,
            "source_applicability_scope": list(profile.source_applicability_scope),
            "external_llm_allowed": profile.external_llm_allowed,
        },
        "split": split,
        "provider_id": provider.provider_id,
        # Routing policy is deliberately excluded.  A Primary-generated cache
        # remains reusable when only a Backup endpoint is added.
        "provider_metadata": provider_key_metadata,
        "prompt_sha256": _sha(ROOT / "config/stage12_prompt.txt"),
        "response_schema_sha256": _sha(ROOT / "config/stage12_extraction_response.schema.json"),
        "extraction_contract_sha256": extraction_contract_fingerprint(),
        "candidate_schema_sha256": _sha(ROOT / "config/stage12_candidate.schema.json"),
        "semantic_source_sha256": extraction_source_fingerprint(),
    }
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def _cache_path(evidence: dict, profile, provider, split: str, cache_root: Path) -> Path:
    return cache_root / "evidence" / f"{_evidence_cache_key(evidence, profile, provider, split)}.json"


def _write_evidence_cache(cache_path: Path, provider, candidates: list[dict], response_status: str, no_statement_reason: str | None = None, *, source_unit_coverage: list[dict] | None = None, candidate_source_unit_ids: dict[str, list[str]] | None = None, review_diagnostics: list[dict] | None = None) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "schema_version": 3,
        "cache_key": cache_path.stem,
        "provider_id": provider.provider_id,
        "provider_mode": "real_llm",
        "provider_metadata": getattr(provider, "last_result_metadata", getattr(provider, "metadata", {})),
        "contract_fingerprints": {
            "prompt": _sha(ROOT / "config/stage12_prompt.txt"),
            "response_schema": _sha(ROOT / "config/stage12_extraction_response.schema.json"),
            "contract": extraction_contract_fingerprint(),
            "contract_full": _sha(ROOT / "config/stage12_statement_contract.json"),
            "candidate_schema": _sha(ROOT / "config/stage12_candidate.schema.json"),
            "semantic_source": extraction_source_fingerprint(),
            "provider_config": _sha(ROOT / "config/stage12_provider.json"),
        },
        "response_status": response_status,
        "no_statement_reason": no_statement_reason,
        "candidates": candidates,
        "source_unit_coverage": source_unit_coverage,
        "candidate_source_unit_ids": candidate_source_unit_ids,
        "review_diagnostics": review_diagnostics,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _extract_with_evidence_cache(
    evidence: dict,
    profile,
    provider,
    split: str,
    cache_root: Path,
    *,
    force: bool = False,
    attempt_observer=None,
    outcome: dict | None = None,
) -> list[dict]:
    """Reuse only fully validated structured candidates; never persist raw model text."""
    if getattr(provider, "metadata", {}).get("mode") != "real_llm":
        extractor = ProviderBackedExtractor(provider, profile=profile, split=split)
        candidates = extractor.extract(evidence)
        if outcome is not None:
            outcome.update(status=extractor.last_response_status, no_statement_reason=extractor.last_no_statement_reason, review_diagnostics=extractor.last_review_diagnostics)
        return candidates
    cache_key = _evidence_cache_key(evidence, profile, provider, split)
    cache_path = cache_root / "evidence" / f"{cache_key}.json"
    if cache_path.exists() and not force:
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("schema_version") == 3 and cached.get("cache_key") == cache_key and cached.get("provider_id") == provider.provider_id:
                expected_fingerprints = {
                    "prompt": _sha(ROOT / "config/stage12_prompt.txt"),
                    "response_schema": _sha(ROOT / "config/stage12_extraction_response.schema.json"),
                    "contract": extraction_contract_fingerprint(),
                    "candidate_schema": _sha(ROOT / "config/stage12_candidate.schema.json"),
                    "semantic_source": extraction_source_fingerprint(),
                    "provider_config": _sha(ROOT / "config/stage12_provider.json"),
                }
                cached_fingerprints = cached.get("contract_fingerprints") or {}
                if any(cached_fingerprints.get(key) != value for key, value in expected_fingerprints.items()):
                    raise ValueError("Stage 12 cache contract fingerprints are stale")
                cache_provider_matches = provider.cache_provider_matches(cached) if hasattr(provider, "cache_provider_matches") else bool(cached.get("provider_metadata"))
                if not cache_provider_matches:
                    raise ValueError("Stage 12 cache provider configuration is stale")
                candidates = cached.get("candidates")
                response_status = cached.get("response_status")
                if not isinstance(candidates, list) or response_status not in {"ok", "no_statement"}:
                    raise ValueError("Stage 12 cache response status or candidates field is invalid")
                if response_status == "no_statement" and candidates:
                    raise ValueError("no_statement cache must contain an empty candidates list")
                if response_status == "no_statement" and not str(cached.get("no_statement_reason") or "").strip():
                    raise ValueError("no_statement cache must retain the provider reason")
                if response_status == "ok" and not candidates:
                    raise ValueError("ok cache must contain at least one candidate")
                for candidate in candidates:
                    validate_candidate_evidence_binding(candidate, evidence)
                    validate_candidate_against_evidence(candidate, evidence)
                _validate_cached_ledger(evidence, cached)
                if outcome is not None:
                    outcome.update(status=response_status, no_statement_reason=cached.get("no_statement_reason"), review_diagnostics=cached["review_diagnostics"])
                return candidates
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass
    extractor = ProviderBackedExtractor(provider, profile=profile, split=split)
    candidates = extractor.extract(evidence, attempt_observer=attempt_observer)
    if outcome is not None:
        outcome.update(status=extractor.last_response_status, no_statement_reason=extractor.last_no_statement_reason, review_diagnostics=extractor.last_review_diagnostics)
    _write_evidence_cache(cache_path, provider, candidates, extractor.last_response_status or "ok", extractor.last_no_statement_reason, source_unit_coverage=extractor.last_source_unit_coverage, candidate_source_unit_ids=extractor.last_candidate_source_unit_ids, review_diagnostics=extractor.last_review_diagnostics)
    return candidates


def _validate_cached_ledger(evidence: dict, cached: dict) -> None:
    candidates = cached["candidates"]
    assignments = cached.get("candidate_source_unit_ids")
    if not isinstance(assignments, dict) or set(assignments) != {str(index) for index in range(1, len(candidates) + 1)}:
        raise ValueError("cache candidate source-unit assignments missing")
    response = {
        "status": cached["response_status"],
        "source_unit_coverage": cached.get("source_unit_coverage"),
        "candidates": [dict(candidate, source_unit_ids=assignments[str(index)]) for index, candidate in enumerate(candidates, start=1)],
    }
    validate_source_unit_coverage(evidence, response)
    diagnostics = candidate_review_diagnostics(evidence, candidates, response["source_unit_coverage"])
    if cached.get("review_diagnostics") != diagnostics:
        raise ValueError("cache review diagnostics are stale")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _source_groups() -> list[dict]:
    index = json.loads((ROOT / "data/stage6/stage6_source_structure_index.json").read_text(encoding="utf-8"))
    groups = index.get("operation_groups")
    if not isinstance(groups, list):
        raise ValueError("Stage 6 source structure index has no operation_groups")
    return groups


def _context_groups() -> list[dict]:
    index = json.loads((ROOT / "data/stage6/stage6_source_structure_index.json").read_text(encoding="utf-8"))
    groups = index.get("context_groups", [])
    if not isinstance(groups, list):
        raise ValueError("Stage 6 source structure context_groups is not an array")
    return groups


def _step_evidence_ids(step: dict) -> list[str]:
    return list(step.get("evidence_ids") or ([step["evidence_id"]] if step.get("evidence_id") else []))


def _page_evidence(page: dict, evidence_by_id: dict[str, dict], groups: list[dict] | None = None, context_groups: list[dict] | None = None) -> dict[str, dict]:
    """Attach only Stage 6 source-confirmed group context to primary Evidence."""
    ids = page.get("evidence_ids", [])
    result = {}
    for evidence_id in ids:
        evidence = dict(evidence_by_id[evidence_id], document_key=page["document_key"])
        operation_context = []
        for group in groups or []:
            if group.get("source_review_status") != "confirmed":
                continue
            steps = group.get("steps") or []
            if evidence_id not in {member for step in steps for member in _step_evidence_ids(step)}:
                continue
            member_ids = list(dict.fromkeys([
                *([group["title_evidence_id"]] if group.get("title_evidence_id") else []),
                *(member for step in steps for member in _step_evidence_ids(step)),
            ]))
            operation_context.append({
                "group_id": group["group_id"],
                "source_title": group.get("source_title"),
                "title_evidence_id": group.get("title_evidence_id"),
                "source_review_status": group.get("source_review_status"),
                "source_total_steps": group.get("source_total_steps"),
                "physical_pages": group.get("physical_pages", []),
                "steps": steps,
                "member_evidence": [
                    {"evidence_id": member_id,
                     "text": str(evidence_by_id[member_id].get("effective_text") or evidence_by_id[member_id].get("source_text") or ""),
                     "document_logical_id": evidence_by_id[member_id].get("document_logical_id"),
                     "revision_id": evidence_by_id[member_id].get("revision_id"),
                     "locations": evidence_by_id[member_id].get("locations", []),
                     "evidence_version_id": evidence_by_id[member_id].get("evidence_version_id"),
                     "source_text_sha256": evidence_by_id[member_id].get("source_text_sha256")}
                    for member_id in member_ids if member_id in evidence_by_id and evidence_by_id[member_id].get("review_status") == "accepted"
                ],
            })
        evidence["operation_group_context"] = operation_context
        evidence["related_source_context"] = [
            {"group_id": group["group_id"], "group_kind": group["group_kind"],
             "source_title": group.get("source_title"), "source_review_status": group.get("source_review_status"),
             "title_evidence_id": group.get("title_evidence_id"),
             "stem_evidence_id": group.get("stem_evidence_id"),
             "answer_marked": group.get("answer_marked"),
             "answer_option": group.get("answer_option"), "answer_text": group.get("answer_text"),
             "answer_source_quote": group.get("answer_source_quote"), "answer_evidence_id": group.get("answer_evidence_id"),
             "source_total_items": group.get("source_total_items"), "items": group.get("items", []),
             "member_evidence": [
                 {"evidence_id": member_id,
                  "text": str(evidence_by_id[member_id].get("effective_text") or evidence_by_id[member_id].get("source_text") or ""),
                  "document_logical_id": evidence_by_id[member_id].get("document_logical_id"),
                  "revision_id": evidence_by_id[member_id].get("revision_id"),
                  "locations": evidence_by_id[member_id].get("locations", []),
                  "evidence_version_id": evidence_by_id[member_id].get("evidence_version_id"),
                  "source_text_sha256": evidence_by_id[member_id].get("source_text_sha256")}
                 for member_id in dict.fromkeys([*([group["title_evidence_id"]] if group.get("group_kind") in SOURCE_LIST_KINDS and group.get("title_evidence_id") else []), *group.get("member_evidence_ids", [])])
                 if member_id in evidence_by_id and evidence_by_id[member_id].get("review_status") == "accepted"
             ]}
            for group in context_groups or [] if group.get("source_review_status") == "confirmed" and evidence_id in group.get("member_evidence_ids", [])
        ]
        result[evidence_id] = evidence
    return result


def summarize_operation_groups(groups: list[dict], candidates: list[dict], evidence_by_id: dict[str, dict], eligible_ids: set[str]) -> list[dict]:
    """Complete only source-confirmed groups with every ordered step grounded."""
    relevant = [group for group in groups if any(member in eligible_ids for step in group.get("steps", []) for member in _step_evidence_ids(step))]
    known_ids = {group["group_id"] for group in relevant}
    unknown = {item["procedure_group"]["group_id"] for item in candidates if item.get("procedure_group") and item["procedure_group"]["group_id"] not in known_ids}
    if unknown:
        raise ValueError("candidate references an unknown Development operation group: " + ", ".join(sorted(unknown)))
    outcomes = []
    for group in relevant:
        steps = group.get("steps") or []
        total = group.get("source_total_steps")
        expected = set(range(1, total + 1)) if isinstance(total, int) and total > 0 else set()
        source_valid = (
            group.get("source_review_status") == "confirmed"
            and bool(expected)
            and len(steps) == total
            and [step.get("source_order") for step in steps] == list(range(1, total + 1))
            and all(step.get("status") == "confirmed" for step in steps)
        )
        members = [item for item in candidates if (item.get("procedure_group") or {}).get("group_id") == group["group_id"]]
        covered = []
        for item in members:
            annotation = item["procedure_group"]
            if annotation.get("status") != "indexed" or annotation.get("step_total") != total:
                continue
            index = annotation.get("step_index")
            if index not in expected:
                continue
            source_step = steps[index - 1]
            source_ids = set(_step_evidence_ids(source_step))
            if not source_ids or not source_ids <= eligible_ids:
                continue
            if item.get("document_logical_id") != group.get("document_logical_id") or item.get("revision_id") != group.get("revision_id"):
                continue
            if any(evidence_by_id.get(evidence_id, {}).get("document_logical_id") != group.get("document_logical_id") or evidence_by_id.get(evidence_id, {}).get("revision_id") != group.get("revision_id") for evidence_id in source_ids):
                continue
            bindings = item.get("evidence_bindings", [])
            direct_ids = {binding.get("evidence_id") for binding in bindings if binding.get("support_type") == "direct"}
            context_ids = {binding.get("evidence_id") for binding in bindings if binding.get("support_type") == "context"}
            expected_context = {group["title_evidence_id"]} - source_ids if group.get("title_evidence_id") else set()
            if direct_ids != source_ids or context_ids != expected_context:
                continue
            binding_spans = {span for binding in bindings if binding.get("support_type") == "direct" for span in (binding.get("source_span_ids") or item.get("source_span_ids") or [])}
            if not set(source_step.get("source_span_ids") or ()) <= binding_spans:
                continue
            source_quote = str(source_step.get("source_quote") or "")
            evidence_text = " ".join(str(evidence_by_id[evidence_id].get("effective_text") or evidence_by_id[evidence_id].get("source_text") or "") for evidence_id in _step_evidence_ids(source_step))
            source_quotes = source_step.get("source_quotes") or {}
            if source_quotes and any(
                not str(source_quotes.get(evidence_id) or "").strip()
                or "".join(str(source_quotes[evidence_id]).split()) not in "".join(str(evidence_by_id[evidence_id].get("effective_text") or evidence_by_id[evidence_id].get("source_text") or "").split())
                for evidence_id in source_ids
            ):
                continue
            if not source_quote or _text_similarity(source_quote, evidence_text) < 0.45:
                continue
            if not source_step_local_match(item.get("statement_text", ""), source_quote, str(group.get("source_title") or "")):
                continue
            covered.append(index)
        ordered = covered == sorted(covered)
        content_complete = all(
            source_step_clause_coverage(
                str(step.get("source_quote") or ""),
                [item.get("statement_text", "") for item in members if (item.get("procedure_group") or {}).get("step_index") == step.get("source_order")],
            )
            for step in steps
        )
        status = "complete" if source_valid and ordered and set(covered) == expected and len(members) == len(covered) and content_complete else "unresolved"
        outcomes.append({
            "group_id": group["group_id"],
            "status": status,
            "candidate_ids": [item["candidate_id"] for item in members],
            "covered_step_indices": sorted(set(covered)),
            "missing_step_indices": sorted(expected - set(covered)),
        })
    return outcomes


def summarize_context_groups(groups: list[dict], candidates: list[dict], evidence_by_id: dict[str, dict], eligible_ids: set[str]) -> list[dict]:
    """Account for every reviewed non-sequential source list item."""
    relevant = [group for group in groups if group.get("group_kind") in SOURCE_LIST_KINDS and any(member in eligible_ids for item in group.get("items", []) for member in _step_evidence_ids(item))]
    known_ids = {group["group_id"] for group in relevant}
    unknown = {item["source_list_item"]["group_id"] for item in candidates if item.get("source_list_item") and item["source_list_item"]["group_id"] not in known_ids}
    if unknown:
        raise ValueError("candidate references an unknown Development source list: " + ", ".join(sorted(unknown)))
    outcomes = []
    for group in relevant:
        items = group.get("items") or []
        total = group.get("source_total_items")
        expected = set(range(1, total + 1)) if isinstance(total, int) and total > 0 else set()
        source_valid = (
            group.get("group_kind") in SOURCE_LIST_KINDS
            and group.get("source_review_status") == "confirmed"
            and bool(expected) and len(items) == total
            and [item.get("source_order") for item in items] == list(range(1, total + 1))
            and all(item.get("status") == "confirmed" for item in items)
        )
        members = [item for item in candidates if (item.get("source_list_item") or {}).get("group_id") == group["group_id"]]
        covered = []
        for candidate in members:
            annotation = candidate["source_list_item"]
            index = annotation.get("item_index")
            if annotation.get("status") != "indexed" or annotation.get("item_total") != total or index not in expected or (group.get("group_kind") == "classification_list" and candidate.get("statement_type") == "procedure") or candidate.get("procedure_group"):
                continue
            source_item = items[index - 1]
            source_ids = set(_step_evidence_ids(source_item))
            bindings = candidate.get("evidence_bindings", [])
            direct_ids = {binding.get("evidence_id") for binding in bindings if binding.get("support_type") in {"direct", "partial"}}
            context_ids = {binding.get("evidence_id") for binding in bindings if binding.get("support_type") == "context"}
            allowed_context = {group.get("title_evidence_id")} - set(source_ids) - {None}
            if not source_ids or not source_ids <= eligible_ids or direct_ids != source_ids or context_ids != allowed_context:
                continue
            if candidate.get("document_logical_id") != group.get("document_logical_id") or candidate.get("revision_id") != group.get("revision_id"):
                continue
            if any(evidence_by_id.get(evidence_id, {}).get("document_logical_id") != group.get("document_logical_id") or evidence_by_id.get(evidence_id, {}).get("revision_id") != group.get("revision_id") for evidence_id in source_ids):
                continue
            binding_spans = {span for binding in bindings for span in (binding.get("source_span_ids") or candidate.get("source_span_ids") or [])}
            if not set(source_item.get("source_span_ids") or ()) <= binding_spans:
                continue
            source_quotes = source_item.get("source_quotes") or {}
            if any(not str(source_quotes.get(evidence_id) or "").strip() or "".join(str(source_quotes[evidence_id]).split()) not in "".join(str(evidence_by_id[evidence_id].get("effective_text") or evidence_by_id[evidence_id].get("source_text") or "").split()) for evidence_id in source_ids):
                continue
            if _text_similarity(candidate.get("statement_text"), source_item.get("source_quote")) < 0.25:
                continue
            covered.append(index)
        covered_indices = sorted(set(covered))
        outcomes.append({
            "group_id": group["group_id"],
            "status": "complete" if source_valid and set(covered_indices) == expected and len(members) == len(covered) else "unresolved",
            "candidate_ids": [item["candidate_id"] for item in members],
            "covered_item_indices": covered_indices,
            "missing_item_indices": sorted(expected - set(covered_indices)),
        })
    return outcomes


def summarize_question_groups(groups: list[dict], candidates: list[dict], evidence_by_id: dict[str, dict], eligible_ids: set[str], supportable_ids: set[str] | None = None) -> list[dict]:
    """Only marked, source-confirmed stem/option pairs can yield answer facts."""
    relevant = [group for group in groups if group.get("group_kind") == "question_options" and any(evidence_id in eligible_ids for evidence_id in group.get("member_evidence_ids", []))]
    supportable_ids = supportable_ids if supportable_ids is not None else eligible_ids
    known_ids = {group["group_id"] for group in relevant}
    unknown = {item["source_question"]["group_id"] for item in candidates if item.get("source_question") and item["source_question"]["group_id"] not in known_ids}
    if unknown:
        raise ValueError("candidate references an unknown Development question: " + ", ".join(sorted(unknown)))
    outcomes = []
    for group in relevant:
        members = [item for item in candidates if (item.get("source_question") or {}).get("group_id") == group["group_id"]]
        source_ids = set(group.get("member_evidence_ids", []))
        answer_pair_ids = {group.get("stem_evidence_id"), group.get("answer_evidence_id")}
        source_valid = (
            group.get("source_review_status") == "confirmed"
            and group.get("answer_marked") is True
            and bool(group.get("answer_option")) and bool(group.get("answer_text"))
            and bool(group.get("answer_source_quote")) and group.get("answer_evidence_id") in source_ids
            and bool(source_ids) and answer_pair_ids <= source_ids and source_ids <= supportable_ids
            and all(evidence_by_id.get(evidence_id, {}).get("document_logical_id") == group.get("document_logical_id") and evidence_by_id.get(evidence_id, {}).get("revision_id") == group.get("revision_id") for evidence_id in source_ids)
        )
        model_valid = bool(members) and all(
            (item.get("source_question") or {}).get("status") == "linked"
            and _normalized_text((item.get("source_question") or {}).get("answer_option")).upper() == _normalized_text(group.get("answer_option")).upper()
            and _normalized_text((item.get("source_question") or {}).get("answer_text")) == _normalized_text(group.get("answer_text"))
            and _statement_asserts_question_answer(item.get("statement_text"), group.get("answer_text"))
            and item.get("statement_type") != "procedure"
            and {binding.get("evidence_id") for binding in item.get("evidence_bindings", [])} == answer_pair_ids
            and item.get("document_logical_id") == group.get("document_logical_id")
            and item.get("revision_id") == group.get("revision_id")
            for item in members
        )
        outcomes.append({"group_id": group["group_id"], "status": "complete" if source_valid and model_valid else "unresolved", "candidate_ids": [item["candidate_id"] for item in members]})
    return outcomes


def prune_stale_evidence_cache(manifest: dict, cache_root: Path) -> dict[str, int]:
    """Count obsolete cache records without deleting or re-keying files."""
    evidence_by_id = {row["evidence"]["evidence_id"]: row["evidence"] for row in _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")}
    router = ProfileRouter()
    provider = provider_from_config()
    groups = _source_groups()
    context_groups = _context_groups()
    current_keys = {
        _evidence_cache_key(evidence, router.route(evidence), provider, manifest["source_split"])
        for page in manifest.get("pages", []) for evidence in _page_evidence(page, evidence_by_id, groups, context_groups).values()
    }
    evidence_dir = cache_root / "evidence"
    files = list(evidence_dir.glob("*.json")) if evidence_dir.exists() else []
    return {"current_valid": len(_current_valid_evidence_ids(manifest, cache_root)), "stale_ignored": sum(path.stem not in current_keys for path in files), "stale_deleted": 0}


def prune_stale_batch_records(cache_root: Path, keep_batch_id: str) -> int:
    """Report historical batch records; project policy forbids bulk deletion."""
    return 0


def latest_completed_batch_id(cache_root: Path) -> str | None:
    batches_root = cache_root / "batches"
    candidates = []
    if not batches_root.exists():
        return None
    for batch_dir in batches_root.iterdir():
        record_path = batch_dir / "batch.json"
        if not batch_dir.is_dir() or not record_path.exists():
            continue
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if record.get("status") == "completed":
                candidates.append((record_path.stat().st_mtime, record.get("extraction_batch_id")))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    candidates = [(mtime, batch_id) for mtime, batch_id in candidates if batch_id]
    return max(candidates, default=(0, None))[1]


def _gate(manifest: dict) -> None:
    stage6_audit = json.loads((ROOT / "data/stage6/stage6_semantic_coverage_audit.json").read_text(encoding="utf-8"))
    stage11_audit = json.loads((ROOT / "data/stage11/stage11_exit_audit.json").read_text(encoding="utf-8"))
    stage6_lineage_current, stage11_lineage_current = upstream_lineage_current(stage6_audit, stage11_audit)
    if not stage6_lineage_current or not stage11_lineage_current:
        raise ValueError("Stage 6 or Stage 11 audit has stale inner source lineage")
    if stage6_audit.get("status") != "complete" or stage6_audit.get("stage12_input_allowed") is not True:
        raise ValueError("Stage 6 semantic coverage gate is closed")
    for upstream in ("data/stage9/stage9_exit_audit.json", "data/stage10/stage10_audit.json"):
        upstream_audit = json.loads((ROOT / upstream).read_text(encoding="utf-8"))
        if upstream_audit.get("status") != "complete" or upstream_audit.get("next_stage_allowed") is not True:
            raise ValueError(f"upstream gate is closed: {upstream}")
    audit = stage11_audit
    if audit.get("status") != "complete" or not audit.get("checks", {}).get("stage12_entry_allowed"):
        raise ValueError("Stage 11 entry gate is closed")
    if manifest.get("status") != "frozen" or manifest.get("source_split") != "development_regression_golden":
        raise ValueError("Stage 12 development manifest is not frozen development input")
    if not manifest.get("label_free_extractor_view") or manifest.get("holdout_used_for_tuning") or manifest.get("blind_read"):
        raise ValueError("Stage 12 development input is not label-free or violates isolation")
    if any(key in json.dumps(manifest, ensure_ascii=False).lower() for key in ("acceptance_holdout", "blind_test")):
        raise ValueError("Stage 12 development manifest contains a holdout or blind boundary")
    for path, expected in manifest.get("inputs", {}).items():
        paths = {
            "stage11_exit_audit": ROOT / "data/stage11/stage11_exit_audit.json",
            "stage9_exit_audit": ROOT / "data/stage9/stage9_exit_audit.json",
            "stage10_audit": ROOT / "data/stage10/stage10_audit.json",
            "stage6_evidence_bundle": ROOT / "data/stage6/stage6_evidence_bundle.jsonl",
            "stage6_semantic_coverage_audit": ROOT / "data/stage6/stage6_semantic_coverage_audit.json",
            "stage6_source_structure_index": ROOT / "data/stage6/stage6_source_structure_index.json",
            "stage11_evaluation_sample_registry": ROOT / "data/stage11/evaluation_sample_registry.json",
            "stage12_representative_baseline": ROOT / "data/stage12/stage12_representative_baseline.json",
            "stage12_profile_routing": ROOT / "config/stage12_profile_routing.json",
        }
        if path in paths and _sha(paths[path]) != expected:
            raise ValueError(f"Stage 12 manifest input hash is stale: {path}")


def _current_valid_evidence_ids(manifest: dict, cache_root: Path) -> set[str]:
    """Return Evidence IDs with a current, schema-valid per-Evidence cache."""
    evidence_by_id = {row["evidence"]["evidence_id"]: row["evidence"] for row in _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")}
    router = ProfileRouter()
    provider = provider_from_config()
    groups = _source_groups()
    context_groups = _context_groups()
    expected_fingerprints = {
        "prompt": _sha(ROOT / "config/stage12_prompt.txt"),
        "response_schema": _sha(ROOT / "config/stage12_extraction_response.schema.json"),
        "contract": extraction_contract_fingerprint(),
        "candidate_schema": _sha(ROOT / "config/stage12_candidate.schema.json"),
        "semantic_source": extraction_source_fingerprint(),
        "provider_config": _sha(ROOT / "config/stage12_provider.json"),
    }
    valid = set()
    for page in manifest.get("pages", []):
        for evidence_id, evidence in _page_evidence(page, evidence_by_id, groups, context_groups).items():
            profile = router.route(evidence)
            key = _evidence_cache_key(evidence, profile, provider, manifest["source_split"])
            path = cache_root / "evidence" / f"{key}.json"
            if not path.exists():
                continue
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                candidates = cached.get("candidates")
                response_status = cached.get("response_status")
                if (
                    cached.get("schema_version") == 3
                    and cached.get("cache_key") == key
                    and cached.get("provider_id") == provider.provider_id
                    and all((cached.get("contract_fingerprints") or {}).get(key) == value for key, value in expected_fingerprints.items())
                    and (provider.cache_provider_matches(cached) if hasattr(provider, "cache_provider_matches") else bool(cached.get("provider_metadata")))
                    and isinstance(candidates, list)
                    and response_status in {"ok", "no_statement"}
                    and ((response_status == "no_statement" and not candidates and str(cached.get("no_statement_reason") or "").strip()) or (response_status == "ok" and candidates))
                ):
                    for candidate in candidates:
                        validate_candidate_evidence_binding(candidate, evidence)
                        validate_candidate_against_evidence(candidate, evidence)
                    _validate_cached_ledger(evidence, cached)
                    valid.add(evidence_id)
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    return valid


def run_evidence_batch(items: list[tuple[str, dict, Any]], extract_one, *, max_workers: int = 1) -> tuple[list[dict], list[tuple[str, dict, Exception]]]:
    """Process every Evidence independently and return successes plus failures."""
    candidates: list[dict] = []
    failures: list[tuple[str, dict, Exception]] = []
    def attempt(item):
        evidence_id, evidence, profile = item
        try:
            return extract_one(evidence, profile), None
        except Exception as error:  # isolate one Evidence; caller decides batch status
            return [], (evidence_id, evidence, error)
    if max_workers == 1:
        results = map(attempt, items)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(attempt, items))
    for rows, failure in results:
        candidates.extend(rows)
        if failure is not None:
            failures.append(failure)
    return candidates, failures


def _build(manifest: dict, cache_root: Path, *, attempt_observer=None, max_workers: int = 16) -> dict:
    canonical_rows = _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")
    evidence_by_id = {row["evidence"]["evidence_id"]: row["evidence"] for row in canonical_rows}
    source_rows = {row["evidence"]["evidence_id"]: row for row in canonical_rows}
    router = ProfileRouter()
    provider = provider_from_config()
    groups = _source_groups()
    context_groups = _context_groups()
    candidates = []
    failed_evidence_ids = []
    attempted_evidence_ids = []
    evidence_outcomes = []
    review_diagnostics = []
    provider_run_counts: dict[str, dict[str, int]] = {}
    work_items = []
    for page in manifest["pages"]:
        for evidence_id, evidence in _page_evidence(page, evidence_by_id, groups, context_groups).items():
            if evidence.get("review_status") != "accepted":
                raise ValueError(f"development Evidence is not accepted: {evidence_id}")
            source_row = source_rows[evidence_id]
            if evidence.get("content_kind") == "table" or source_row.get("stage12_extractability") in {"pending_review", "context_only"}:
                raise ValueError(f"development Evidence is not eligible for semantic extraction: {evidence_id}")
            profile = router.route(evidence)
            if page.get("extraction_profile_id") != profile.extraction_profile_id or page.get("semantic_role") != profile.semantic_role:
                raise ValueError(f"manifest Profile route does not match Evidence: {evidence_id}")
            work_items.append((evidence_id, evidence, profile))
    def extract_one(evidence, profile):
        observer = None if attempt_observer is None else lambda event: attempt_observer(evidence, event)
        response_outcome = {}
        local_provider = provider_from_config()
        rows = _extract_with_evidence_cache(evidence, profile, local_provider, manifest["source_split"], cache_root, attempt_observer=observer, outcome=response_outcome)
        metadata = getattr(local_provider, "metadata", {})
        provider_run_counts[evidence["evidence_id"]] = {
            key: int(metadata.get(key) or 0)
            for key in ("primary_success_count", "backup_success_count", "fallback_count", "primary_transport_failure_count", "backup_transport_failure_count", "primary_schema_failure_count", "backup_schema_failure_count")
        }
        result = {"evidence_id": evidence["evidence_id"], "status": response_outcome["status"], "candidate_ids": [item["candidate_id"] for item in rows]}
        if result["status"] == "no_statement":
            result["no_statement_reason"] = response_outcome["no_statement_reason"]
        evidence_outcomes.append(result)
        review_diagnostics.extend(response_outcome.get("review_diagnostics") or [])
        return rows
    extracted_candidates, failures = run_evidence_batch(work_items, extract_one, max_workers=max_workers)
    candidates.extend(extracted_candidates)
    evidence_order = {evidence_id: index for index, (evidence_id, _, _) in enumerate(work_items)}
    evidence_outcomes.sort(key=lambda row: evidence_order[row["evidence_id"]])
    for evidence_id, evidence, error in failures:
        failed_evidence_ids.append(evidence_id)
        attempted_evidence_ids.append(evidence_id)
        if attempt_observer is not None:
            details = getattr(error, "details", {}) or {}
            attempt_observer(evidence, {
                "attempt": 1,
                "outcome": "failure",
                "failure_type": getattr(error, "failure_type", "semantic_validation_failure" if "semantic" in str(error).lower() else "transport_failure"),
                "field": None,
                "validator_reason": str(error),
                "exception_type": type(error).__name__,
                "message": str(error)[:1600],
                "model_value_or_text": None,
                **details,
            })
    if failed_evidence_ids:
        valid_ids = _current_valid_evidence_ids(manifest, cache_root)
        raise IncompleteDevelopmentError({
            "total_evidence": sum(len(page.get("evidence_ids", [])) for page in manifest.get("pages", [])),
            "valid_cached_evidence": len(valid_ids),
            "attempted_this_run": [],
            "succeeded_this_run": [],
            "failed_evidence_ids": sorted(set(failed_evidence_ids)),
            "remaining_evidence_ids": sorted({evidence_id for page in manifest.get("pages", []) for evidence_id in page.get("evidence_ids", [])} - valid_ids),
            "batch_complete": False,
            "current_artifact_status": "current_artifact_not_available",
        })
    eligible_ids = {item[0] for item in work_items}
    provider_metadata = dict(provider.metadata)
    for key in ("primary_success_count", "backup_success_count", "fallback_count", "primary_transport_failure_count", "backup_transport_failure_count", "primary_schema_failure_count", "backup_schema_failure_count"):
        if key in provider_metadata:
            provider_metadata[key] = sum(item[key] for item in provider_run_counts.values())
    operation_group_outcomes = summarize_operation_groups(groups, candidates, evidence_by_id, eligible_ids)
    context_group_outcomes = summarize_context_groups(context_groups, candidates, evidence_by_id, eligible_ids)
    supportable_ids = eligible_ids | {evidence_id for page in manifest["pages"] for evidence_id in page.get("context_only_evidence_ids", [])}
    question_group_outcomes = summarize_question_groups(context_groups, candidates, evidence_by_id, eligible_ids, supportable_ids)
    payload = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "engineering_statement_candidates",
        "status": "candidate_only",
        "formal_release": False,
        "producer": "scripts/build_stage12_candidates.py",
        "inputs": {
            "stage11_exit_audit": "data/stage11/stage11_exit_audit.json",
            "stage9_exit_audit": "data/stage9/stage9_exit_audit.json",
            "stage10_audit": "data/stage10/stage10_audit.json",
            "stage12_input_manifest": "data/stage12/stage12_input_manifest.json",
            "stage6_evidence_bundle": "data/stage6/stage6_evidence_bundle.jsonl",
            "stage6_semantic_coverage_audit": "data/stage6/stage6_semantic_coverage_audit.json",
            "stage6_source_structure_index": "data/stage6/stage6_source_structure_index.json",
            "stage12_statement_contract": "config/stage12_statement_contract.json",
            "stage12_profile_routing": "config/stage12_profile_routing.json",
            "stage12_representative_baseline": "data/stage12/stage12_representative_baseline.json",
        },
        "extraction_profile": "profile_routing_v1",
        "provider_id": provider.provider_id,
        "prompt_version": "stage12-candidate-prompt-v30",
        "provider_metadata": provider_metadata,
        "input_sha256": {path: _sha(ROOT / path) for path in (
            "data/stage9/stage9_exit_audit.json", "data/stage10/stage10_audit.json",
            "data/stage12/stage12_input_manifest.json", "data/stage6/stage6_evidence_bundle.jsonl", "data/stage6/stage6_semantic_coverage_audit.json", "data/stage6/stage6_source_structure_index.json",
            "config/stage12_statement_contract.json", "config/stage12_candidate.schema.json", "ontology/stage9_core.ttl", "ontology/stage9_shapes.ttl",
            "config/stage12_profile_routing.json", "data/stage12/stage12_representative_baseline.json",
            "config/stage12_provider.json", "config/stage12_prompt.txt", "config/stage12_extraction_response.schema.json",
            "data/registry/source_assets.jsonl",
            "src/turbine_kg/extraction/semantic.py",
        )},
        "extraction_fingerprint": {
            "contract": extraction_contract_fingerprint(),
            "prompt": _sha(ROOT / "config/stage12_prompt.txt"),
            "response_schema": _sha(ROOT / "config/stage12_extraction_response.schema.json"),
            "candidate_schema": _sha(ROOT / "config/stage12_candidate.schema.json"),
            "semantic_source": extraction_source_fingerprint(),
            "provider_config": _sha(ROOT / "config/stage12_provider.json"),
        },
        "audit_lineage_sha256": {
            "stage11_exit_audit": _sha(ROOT / "data/stage11/stage11_exit_audit.json"),
            "project_state": _sha(ROOT / "data/project_state.json"),
        },
        "candidates": candidates,
        "evidence_outcomes": evidence_outcomes,
        "review_diagnostics": sorted(review_diagnostics, key=lambda item: (item["evidence_id"], item.get("candidate_id", ""), item["code"], item["message"])),
        "operation_group_outcomes": operation_group_outcomes,
        "context_group_outcomes": context_group_outcomes,
        "question_group_outcomes": question_group_outcomes,
    }
    validate_candidate_payload(payload)
    expected_ids = eligible_ids
    outcome_ids = [item["evidence_id"] for item in evidence_outcomes]
    if len(outcome_ids) != len(expected_ids) or set(outcome_ids) != expected_ids:
        raise ValueError("Development Evidence outcomes do not cover each extractable Evidence exactly once")
    candidate_ids = {item["candidate_id"] for item in candidates}
    if {candidate_id for item in evidence_outcomes for candidate_id in item["candidate_ids"]} != candidate_ids:
        raise ValueError("Development Evidence outcomes do not account for all candidates")
    if any(
        (item["status"] == "ok" and (not item["candidate_ids"] or item.get("no_statement_reason")))
        or (item["status"] == "no_statement" and (item["candidate_ids"] or not str(item.get("no_statement_reason") or "").strip()))
        for item in evidence_outcomes
    ):
        raise ValueError("Development Evidence outcome status and reason are inconsistent")
    prepared_evidence = {item[0]: item[1] for item in work_items}
    for candidate in candidates:
        for binding in candidate.get("evidence_bindings", []):
            evidence_id = binding.get("evidence_id")
            source_row = source_rows.get(evidence_id)
            if source_row is None:
                raise ValueError(f"candidate binds unknown Evidence: {evidence_id}")
            if source_row.get("stage12_extractability") == "pending_review":
                raise ValueError(f"candidate binds pending-review Evidence: {evidence_id}")
            evidence = prepared_evidence.get(evidence_id) or evidence_by_id.get(evidence_id)
            if evidence is None:
                raise ValueError(f"candidate binds unknown Evidence: {binding.get('evidence_id')}")
            validate_candidate_evidence_binding(candidate, evidence)
            if binding == candidate.get("evidence_bindings", [None])[0]:
                validate_candidate_against_evidence(candidate, evidence)
    to_stage9_runtime_payload(candidates)
    return payload


def build(*, force: bool = False, max_workers: int = 16) -> tuple[dict, dict]:
    """Build a complete batch; force rebuilds the batch record, not valid Evidence caches."""
    manifest = json.loads((STAGE12 / "stage12_input_manifest.json").read_text(encoding="utf-8"))
    _gate(manifest)
    contract = load_contract()
    cache_root = ROOT / contract["runtime"]["cache_root"]
    cache_maintenance = prune_stale_evidence_cache(manifest, cache_root)
    events = []
    events_lock = threading.Lock()
    evidence_ids = [evidence_id for page in manifest.get("pages", []) for evidence_id in page.get("evidence_ids", [])]
    def observer(evidence, event):
        with events_lock:
            record_event(evidence, event)
    def record_event(evidence, event):
        decorated = decorate_event(evidence, dict(event))
        details = event.get("details") or event
        # Provider-level events already contain the Primary and Backup
        # attempts. Do not append a duplicate aggregate failure event.
        if isinstance(details, dict) and details.get("primary") and details.get("backup"):
            return
        if decorated.get("outcome") == "failure":
            previous = [item for item in events if item.get("evidence_id") == decorated.get("evidence_id") and item.get("outcome") == "failure"]
            previous_provider = previous[-1].get("provider_alias") if previous else None
            if previous and not decorated.get("fallback_triggered") and previous_provider == decorated.get("provider_alias"):
                previous[-1].update({key: decorated[key] for key in ("exception_type", "message", "root_cause", "status_code", "elapsed_seconds", "fallback_eligible", "fallback_triggered", "fallback_provider") if decorated.get(key) is not None})
                return
        events.append(decorated)
    input_refs = tuple(
        {"kind": "file", "path": path, "sha256": _sha(ROOT / path)}
        for path in (
            "data/stage12/stage12_input_manifest.json",
            "data/stage6/stage6_evidence_bundle.jsonl", "data/stage6/stage6_semantic_coverage_audit.json", "data/stage6/stage6_source_structure_index.json", "config/stage12_statement_contract.json",
            "config/stage12_candidate.schema.json", "ontology/stage9_core.ttl", "ontology/stage9_shapes.ttl",
            "config/stage12_profile_routing.json", "data/stage12/stage12_representative_baseline.json",
            "config/stage12_provider.json", "config/stage12_prompt.txt", "config/stage12_extraction_response.schema.json",
            "data/registry/source_assets.jsonl",
            "src/turbine_kg/extraction/semantic.py",
        )
    )
    try:
        batch, payload = run_with_cache(
        cache_root=cache_root,
        operation=contract["runtime"]["operation"],
        input_refs=input_refs,
        cache_context=({"extractor": "profile_routing_v1", "provider": provider_from_config().provider_id}, {"extraction_contract": extraction_contract_fingerprint(), "profile_routing": _sha(ROOT / "config/stage12_profile_routing.json"), "provider_config": _sha(ROOT / "config/stage12_provider.json"), "prompt": _sha(ROOT / "config/stage12_prompt.txt"), "extractor_source": extraction_source_fingerprint()} ),
        output=lambda: _build(manifest, cache_root, attempt_observer=observer, max_workers=max_workers),
        schema_path=ROOT / "config/runtime_run.schema.json",
        force=force,
        validate_input=lambda: _gate(manifest),
        validate_output=lambda result: (validate_candidate_payload(result), to_stage9_runtime_payload(result["candidates"])),
        )
    except IncompleteDevelopmentError as error:
        valid_ids = _current_valid_evidence_ids(manifest, cache_root)
        progress = dict(error.progress)
        progress["valid_cached_evidence"] = len(valid_ids)
        progress["attempted_this_run"] = sorted({item.get("evidence_id") for item in events})
        progress["succeeded_this_run"] = sorted({item.get("evidence_id") for item in events if item.get("outcome") == "success"})
        progress["remaining_evidence_ids"] = sorted(set(evidence_ids) - valid_ids)
        progress["batch_complete"] = False
        error.progress = progress
        write_failure_summary(events, run_kind="development_batch", evidence_ids=evidence_ids, cache_maintenance=cache_maintenance, status="incomplete", progress=progress)
        raise
    except Exception:
        if events:
            write_failure_summary(events, run_kind="development_batch", evidence_ids=evidence_ids, cache_maintenance=cache_maintenance, status="failed")
        raise
    cache_maintenance["batch_records_deleted"] = prune_stale_batch_records(cache_root, batch.extraction_batch_id)
    valid_ids = _current_valid_evidence_ids(manifest, cache_root)
    progress = {
        "total_evidence": len(evidence_ids),
        "valid_cached_evidence": len(valid_ids),
        "attempted_this_run": sorted({item.get("evidence_id") for item in events}),
        "succeeded_this_run": sorted({item.get("evidence_id") for item in events if item.get("outcome") == "success"}),
        "failed_evidence_ids": [],
        "remaining_evidence_ids": sorted(set(evidence_ids) - valid_ids),
        "batch_complete": len(valid_ids) == len(evidence_ids),
        "current_artifact_status": "current_artifact_available",
    }
    write_failure_summary(events, run_kind="development_batch", evidence_ids=evidence_ids, cache_maintenance=cache_maintenance, progress=progress)
    STAGE12.mkdir(parents=True, exist_ok=True)
    (STAGE12 / "stage12_development_candidates.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return batch.as_dict(), payload


def evaluate_development(payload: dict) -> dict:
    gold = _jsonl(ROOT / "data/stage11/stage11_statement_development_samples.jsonl")
    evidence_by_id = {
        row["evidence"]["evidence_id"]: row["evidence"]
        for row in _jsonl(ROOT / "data/stage6/stage6_evidence_bundle.jsonl")
    }
    if not OUTPUT_PATH.exists():
        raise FileNotFoundError(f"missing canonical Development adjudication: {OUTPUT_PATH}")
    adjudication = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    report = compare_candidates(payload["candidates"], gold, gold_exhaustive=False, evidence_by_id=evidence_by_id, adjudication=adjudication)
    report["adjudication_artifact"] = "data/stage12/stage12_development_disagreement_adjudication.json"
    report["raw_evaluation_artifact"] = "data/stage12/stage12_development_raw_evaluation.json"
    report["raw_evaluation_sha256"] = _sha(RAW_EVALUATION_PATH)
    report.update({"schema_version": 1, "stage": "12", "artifact_kind": "stage12_development_evaluation", "status": "completed", "formal_release": False, "evaluator_version": "stage12-field-evaluator-v7", "holdout_used_for_tuning": False, "blind_read": False, "real_llm_execution": payload.get("provider_metadata", {}).get("mode") == "real_llm", "candidate_artifact": "data/stage12/stage12_development_candidates.json", "gold_artifact": "data/stage11/stage11_statement_development_samples.jsonl", "input_sha256": {"candidate": _sha(STAGE12 / "stage12_development_candidates.json"), "gold": _sha(ROOT / "data/stage11/stage11_statement_development_samples.jsonl"), "manifest": _sha(STAGE12 / "stage12_input_manifest.json"), "routing": _sha(ROOT / "config/stage12_profile_routing.json"), "baseline": _sha(STAGE12 / "stage12_representative_baseline.json"), "contract": _sha(ROOT / "config/stage12_statement_contract.json"), "provider_config": _sha(ROOT / "config/stage12_provider.json"), "prompt": _sha(ROOT / "config/stage12_prompt.txt"), "response_schema": _sha(ROOT / "config/stage12_extraction_response.schema.json"), "registry": _sha(ROOT / "data/registry/source_assets.jsonl"), "adjudication": _sha(OUTPUT_PATH), "evaluator": "stage12-field-evaluator-v7"}})
    report["coverage_matrix"] = "data/stage12/stage12_semantic_coverage_matrix.json"
    report["robustness_artifact"] = "data/stage12/stage12_robustness_evaluation.json"
    (STAGE12 / "stage12_development_evaluation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="Rebuild the batch record while reusing valid per-Evidence caches.")
    parser.add_argument("--workers", type=int, default=16, help="Concurrent Evidence extraction workers (default: 16).")
    parser.add_argument("--evaluate-development", action="store_true")
    parser.add_argument("--evaluate-existing", action="store_true", help="Evaluate the canonical Candidate without rebuilding it or changing its hash.")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 64:
        parser.error("--workers must be between 1 and 64")
    if args.evaluate_existing:
        if args.force or args.evaluate_development:
            parser.error("--evaluate-existing cannot be combined with build options")
        payload = json.loads((STAGE12 / "stage12_development_candidates.json").read_text(encoding="utf-8"))
        report = evaluate_development(payload)
        print(json.dumps({"status": "completed", "field_accuracy": report["field_accuracy"]}, ensure_ascii=False))
        raise SystemExit(0)
    try:
        batch, payload = build(force=args.force, max_workers=args.workers)
    except IncompleteDevelopmentError as error:
        print(json.dumps({"status": "incomplete", **error.progress}, ensure_ascii=False))
        raise SystemExit(2)
    result = {"status": "completed", "extraction_batch_id": batch["extraction_batch_id"], "candidate_count": len(payload["candidates"])}
    if args.evaluate_development:
        evaluation = evaluate_development(payload)
        result["field_accuracy"] = evaluation["field_accuracy"]
    print(json.dumps(result, ensure_ascii=False))

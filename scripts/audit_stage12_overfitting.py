"""Audit Stage 12 for Development overfitting and validation leakage.

This report is intentionally evidence based and non-mutating with respect to
Candidates, Gold and runtime caches.  It inventories the deterministic
semantic helpers, checks for direct Development label/quote leakage in the
Prompt, and records the scope of the current Robustness artifact.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/stage12/stage12_overfitting_audit.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def audit() -> dict:
    prompt_path = ROOT / "config/stage12_prompt.txt"
    semantic_path = ROOT / "src/turbine_kg/extraction/semantic.py"
    contract_path = ROOT / "config/stage12_statement_contract.json"
    prompt = prompt_path.read_text(encoding="utf-8")
    gold_rows = [
        json.loads(line)
        for line in (ROOT / "data/stage11/stage11_statement_development_samples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    gold_ids = [str(row.get("statement_id")) for row in gold_rows if row.get("statement_id")]
    gold_quotes = [str(row.get("statement_text", "")) for row in gold_rows if len(str(row.get("statement_text", ""))) >= 20]
    prompt_normalized = _normalized(prompt)
    direct_id_matches = [item for item in gold_ids if item in prompt]
    direct_quote_matches = [item for item in gold_quotes if _normalized(item) in prompt_normalized]

    heuristic_inventory = [
        {
            "name": "quantity_unit_range_comparator_derivation",
            "symbols": ["QUANTITY_RE", "COMPARATORS", "_quantity_fields"],
            "category": "strong_deterministic",
            "scope": "production_and_fixture",
            "decision": "retain",
            "reason": "Structured numeric, unit, range and comparator parsing is source bounded and independently testable.",
        },
        {
            "name": "evidence_binding_and_surface_grounding",
            "symbols": ["validate_candidate_evidence_binding", "validate_candidate_against_evidence"],
            "category": "strong_deterministic",
            "scope": "production_and_fixture",
            "decision": "retain",
            "reason": "Safety boundary; prevents unsupported surface forms and wrong Evidence attribution.",
        },
        {
            "name": "negation_and_modality_derivation",
            "symbols": ["NEGATIONS", "_negation_fields", "_modality"],
            "category": "semantic_hint_or_consistency_check",
            "scope": "production_derived_fields",
            "decision": "retain_with_review",
            "reason": "Markers are useful for deterministic polarity checks, but scope remains dependent on the returned statement text.",
        },
        {
            "name": "relation_direction_derivation",
            "symbols": ["_relation_direction"],
            "category": "strong_deterministic",
            "scope": "production_and_fixture",
            "decision": "retain",
            "reason": "Direction follows structured predicate and provider-returned conditions; it does not reinterpret conditional keywords.",
        },
        {
            "name": "fixture_condition_applicability_causality_lexical_rules",
            "symbols": ["CONDITION_RE", "_has_causal_marker", "_applicability_scope"],
            "category": "development_like_lexical_heuristic",
            "scope": "fixture_heuristic_only",
            "decision": "do_not_promote",
            "reason": "Useful for offline fixture coverage, but lexical markers cannot replace LLM context classification in the production path.",
        },
        {
            "name": "fixture_entity_and_statement_type_inference",
            "symbols": ["_entities", "_statement_type", "_predicate"],
            "category": "development_like_lexical_heuristic",
            "scope": "fixture_heuristic_only",
            "decision": "do_not_promote",
            "reason": "Fallback interpretation is intentionally small and must not be treated as production semantic authority.",
        },
    ]

    robustness_path = ROOT / "data/stage12/stage12_robustness_evaluation.json"
    robustness = _json(robustness_path) if robustness_path.exists() else {}
    report = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_overfitting_audit",
        "status": "completed",
        "formal_release": False,
        "baseline": {
            "branch": _git("branch", "--show-current"),
            "head": _git("rev-parse", "HEAD"),
            "commit_count": int(_git("rev-list", "--count", "HEAD")),
            "working_tree_clean": not bool(_git("status", "--short")),
        },
        "prompt_audit": {
            "prompt_version": "stage12-candidate-prompt-v18",
            "direct_gold_id_matches": direct_id_matches,
            "direct_gold_quote_match_count": len(direct_quote_matches),
            "direct_gold_quote_matches": direct_quote_matches[:10],
            "risk": "moderate",
            "reason": "No direct Gold IDs or full Development statement quotations were found. The Prompt contains many accumulated semantic boundary rules, so generality must be checked by synthetic counterexamples before further tuning.",
        },
        "deterministic_heuristic_audit": {
            "risk": "moderate",
            "reason": "Production assembly derives bounded fields and validates grounding; fixture-only lexical helpers remain a separate overfitting risk if treated as production authority.",
            "inventory": heuristic_inventory,
        },
        "evaluator_audit": {
            "risk": "moderate",
            "reason": "The legacy exact Gold metric remains for historical comparison, while evaluator v6 adds statement-level, matched-field, safety and adjudication layers. Gold/evaluator errors are not inferred automatically.",
            "unmatched_rows_repeat_field_penalty": False,
            "evaluator_version": "stage12-field-evaluator-v6",
        },
        "robustness_audit": {
            "risk": "moderate",
            "derived_from_development": True,
            "blind": bool(robustness.get("blind_read", False)),
            "proves": "metamorphic or regression stability for the declared cases",
            "does_not_prove": "generalization to unseen independent documents",
        },
        "lineage": {
            "prompt": _sha(prompt_path),
            "semantic_source": _sha(semantic_path),
            "contract": _sha(contract_path),
            "audit_producer": "scripts/audit_stage12_overfitting.py",
        },
        "stop_tuning_rule": "A Development mismatch may change Prompt or production semantic code only when it exposes a generalizable rule that survives synthetic counterexamples. Evidence-supported non-critical granularity disagreements require adjudication and do not justify sample-specific tuning.",
        "reserve_policy": "Independent Reserve remains one-shot and cannot be used for Prompt, semantic or Gold tuning after exposure.",
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))

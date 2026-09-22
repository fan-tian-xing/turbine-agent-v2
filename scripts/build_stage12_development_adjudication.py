"""Build the canonical, user-authorized Stage 12 Development adjudication.

This artifact classifies raw evaluator disagreements; it never edits Gold or
Candidate and is consumed only by the deterministic Development evaluator.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STAGE12 = ROOT / "data/stage12"
GOLD_PATH = ROOT / "data/stage11/stage11_statement_development_samples.jsonl"
CANDIDATE_PATH = STAGE12 / "stage12_development_candidates.json"
EVALUATION_PATH = STAGE12 / "stage12_development_evaluation.json"
OUTPUT_PATH = STAGE12 / "stage12_development_disagreement_adjudication.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# The order is part of the audit history and follows the user-authorized list.
_DECISIONS: tuple[dict[str, Any], ...] = (
    {"gold": "stage12-gold-delivery-01", "candidate": "stage12-candidate-5da72c3633924388ce65", "decision": "D", "subtype": "accepted_boundary_merge", "reason": "Adjacent delivery facts are merged without losing the installation fact."},
    {"gold": "stage12-gold-delivery-04", "candidate": "stage12-candidate-b4a636fc85f9d8a634da", "decision": "D", "subtype": "accepted_boundary_merge", "reason": "Foundation and formwork delivery facts remain complete in one statement."},
    {"gold": "stage12-gold-delivery-06", "candidate": "stage12-candidate-4523e2c0189ea7d0c76e", "decision": "D", "subtype": "accepted_boundary_merge", "reason": "Both parallel closure and roof-waterproofing facts are retained."},
    {"candidate": "stage12-candidate-6be5b994f5a3b8e1395b", "decision": "D", "subtype": "benign_redundant_boundary_split", "reason": "Evidence-supported local cleanup content is a harmless redundant split."},
    {"candidate": "stage12-candidate-b8cc5d3bdf421c18048a", "decision": "D", "subtype": "acceptable_causal_summary", "reason": "The high-level causal summary is Evidence-supported and directionally correct."},
    {"gold": "stage12-gold-gasket-material", "decision": "D", "subtype": "entity_granularity", "reason": "The requirement text is complete; only the structured entity granularity differs."},
    {"gold": "stage12-gold-seal-measurements", "decision": "D", "subtype": "entity_scope_granularity", "reason": "All measurement targets and the requirement are retained with finer entity scope."},
    {"gold": "real-statement-d300n-p73-lift-clearance", "decision": "D", "subtype": "condition_applicability_granularity", "reason": "The inspection, overhaul and half-cylinder context remain in text and Evidence."},
    {"gold": "real-statement-d300n-p73-lift-clearance-s2", "decision": "D", "subtype": "condition_applicability_granularity", "reason": "The same procedure context is represented with a different field partition."},
    {"gold": "real-statement-d300n-p73-lift-clearance-s3", "decision": "D", "subtype": "condition_applicability_granularity", "reason": "The cleaning procedure and its context remain complete despite field granularity."},
    {"gold": "real-statement-d300n-p73-lift-clearance-s4", "decision": "D", "subtype": "condition_applicability_and_entity_granularity", "reason": "The reset operation is retained; only noncritical scope and entity granularity differ."},
    {"gold": "stage11-statement-d2c4678c8e96566b0c8f-s2", "decision": "D", "subtype": "applicability_granularity", "reason": "施工准备时 and the broader foundation context are Evidence-supported scope variants."},
    {"gold": "stage11-statement-090cfb6b41999cdd681c", "decision": "D", "subtype": "compound_entity_split", "reason": "The compound steel, foundation and wall entities are split without information loss."},
    {"gold": "stage12-gold-delivery-05", "candidate": "stage12-candidate-4523e2c0189ea7d0c76e", "decision": "D", "subtype": "accepted_boundary_merge", "reason": "The adjacent delivery requirements remain jointly retrievable and complete."},
    {"gold": "stage12-gold-delivery-07", "decision": "D", "subtype": "entity_granularity", "reason": "Parallel facility and marking entities are represented at a different granularity."},
    {"gold": "stage12-gold-delivery-11", "decision": "D", "subtype": "entity_granularity", "reason": "Hole, temporary cover and guardrail entities remain represented completely."},
    {"gold": "stage11-statement-5ef7104eda31c3578f06", "decision": "D", "subtype": "entity_granularity", "reason": "The control-oil pressure adjustment requirement is complete despite entity roles differing."},
    {"gold": "stage11-statement-5ef7104eda31c3578f06-s3", "decision": "D", "subtype": "entity_granularity", "reason": "The pressure confirmation requirement is complete despite entity granularity."},
    {"gold": "stage11-statement-5740cf642266fcd6feb4", "decision": "D", "subtype": "entity_selection_granularity", "reason": "The staged commissioning prerequisite and authorization are complete in statement text."},
    {"gold": "stage11-statement-7121ec273c465f8f2c6f-s2", "decision": "D", "subtype": "compound_entity_split", "reason": "System, equipment and baseline data are a supported split of one compound entity."},
    {"gold": "stage11-statement-a9ff60d2526222447813", "decision": "D", "subtype": "surface_and_entity_granularity", "reason": "Vacuum establishment and the approximately 60 kPa target are complete."},
    {"gold": "stage11-statement-a9ff60d2526222447813-s2", "decision": "D", "subtype": "soft_scope_metadata_difference", "reason": "The low-vacuum causal fact is complete; only the noncritical roll-stage scope metadata differs."},
    {"gold": "stage11-statement-a9ff60d2526222447813-s3", "candidate": "stage12-candidate-6c97bb897ba7feb43ea1", "decision": "A", "subtype": "noncritical_causal_chain_completeness_error", "reason": "The explicit pressure-rise to positive-pressure causal hop is not retained; this is noncritical and not a safety, quantity, polarity or direction error.", "critical": False, "information_coverage": False},
    {"gold": "stage11-statement-a9ff60d2526222447813-s4", "decision": "D", "subtype": "soft_applicability_difference", "reason": "The positive-pressure to safety-film causal fact is complete; only roll-stage applicability is implicit."},
    {"gold": "stage11-statement-a9ff60d2526222447813-s5", "decision": "D", "subtype": "entity_and_scope_granularity", "reason": "The thermal-shock causal content is complete with different entity and scope granularity."},
)


def _bind_ids(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gold = {row["statement_id"]: row for row in _jsonl(GOLD_PATH)}
    candidate = {row["candidate_id"]: row for row in json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))["candidates"]}
    matched_candidates = {
        item.get("statement_id"): item.get("candidate_id")
        for item in json.loads(EVALUATION_PATH.read_text(encoding="utf-8")).get("matched_pairs", [])
    }
    output = []
    for index, item in enumerate(decisions, start=1):
        gold_id = item.get("gold")
        candidate_id = item.get("candidate") or (matched_candidates.get(gold_id) if gold_id else None)
        source = gold.get(gold_id) if gold_id else candidate.get(candidate_id)
        if source is None:
            raise ValueError(f"adjudication reference not found: {gold_id or candidate_id}")
        source_bindings = source.get("evidence_bindings") or []
        if not source_bindings:
            raise ValueError(f"adjudication reference has no Evidence binding: {gold_id or candidate_id}")
        if candidate_id:
            candidate_row = candidate.get(candidate_id)
            if candidate_row is None:
                raise ValueError(f"adjudication candidate reference not found: {candidate_id}")
            candidate_evidence = {str(item.get("evidence_id")) for item in candidate_row.get("evidence_bindings") or []}
            gold_evidence = {str(item.get("evidence_id")) for item in source_bindings}
            if not candidate_evidence & gold_evidence:
                raise ValueError(f"adjudication Gold/Candidate Evidence mismatch: {gold_id}, {candidate_id}")
        critical = bool(item.get("critical", False))
        output.append({
            "disagreement_id": f"stage12-development-disagreement-{index:02d}",
            "gold_statement_id": gold_id,
            "candidate_id": candidate_id,
            "evidence_id": str(source_bindings[0].get("evidence_id")),
            "decision": item["decision"],
            "subtype": item["subtype"],
            "reason": item["reason"],
            "critical": critical,
            "severity": "critical" if critical else ("noncritical" if item["decision"] == "A" else "not_applicable"),
            "information_coverage": bool(item.get("information_coverage", item["decision"] == "D")),
            "review_provenance": "user acceptance policy delegated review: completeness + Evidence support + no fabrication; independent Reviewer A/B not claimed",
        })
    return output


def build() -> dict[str, Any]:
    if not EVALUATION_PATH.exists():
        raise FileNotFoundError(EVALUATION_PATH)
    decisions = _bind_ids(list(_DECISIONS))
    artifact = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_development_disagreement_adjudication",
        "status": "completed",
        "formal_release": False,
        "producer": "scripts/build_stage12_development_adjudication.py",
        "decision_basis": "user acceptance policy delegated review: completeness + Evidence support + no fabrication",
        "independent_reviewer_ab_claimed": False,
        "source_sha256": {
            "evaluation": _sha(EVALUATION_PATH),
            "gold": _sha(GOLD_PATH),
            "candidate": _sha(CANDIDATE_PATH),
        },
        "decisions": decisions,
        "summary": {
            "total": len(decisions),
            "acceptable_semantic_equivalence": sum(item["decision"] == "D" for item in decisions),
            "confirmed_model_error": sum(item["decision"] == "A" for item in decisions),
            "confirmed_critical_model_error": sum(item["decision"] == "A" and item["critical"] for item in decisions),
            "confirmed_noncritical_model_error": sum(item["decision"] == "A" and not item["critical"] for item in decisions),
            "gold_error": sum(item["decision"] == "G" for item in decisions),
            "evaluator_error": sum(item["decision"] == "E" for item in decisions),
            "pending": 0,
        },
        "consumer": "scripts/build_stage12_candidates.py::evaluate_development and scripts/audit_stage12_exit.py",
    }
    if artifact["summary"] != {"total": 25, "acceptable_semantic_equivalence": 24, "confirmed_model_error": 1, "confirmed_critical_model_error": 0, "confirmed_noncritical_model_error": 1, "gold_error": 0, "evaluator_error": 0, "pending": 0}:
        raise AssertionError("unexpected adjudication summary")
    OUTPUT_PATH.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return artifact


if __name__ == "__main__":
    result = build()
    print(json.dumps(result["summary"], ensure_ascii=False))

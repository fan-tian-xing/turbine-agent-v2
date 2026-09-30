"""Build reviewed Reserve Gold from source-first semantic decisions.

This builder is generic: document-specific statements live in the reviewed JSON,
not in code.  It does not read Development Candidate or any Reserve model output.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from turbine_kg.extraction.semantic import _canonical_quantity_unit, _quantity_fields, _resolved_marked_answer_text

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "data/stage12/stage12_reserve_evidence.jsonl"
REVIEWS_PATH = ROOT / "data/stage12/stage12_reserve_gold_reviews.json"
OUTPUT_PATH = ROOT / "data/stage12/stage12_reserve_gold.jsonl"
AUDIT_PATH = ROOT / "data/stage12/stage12_reserve_gold_audit.json"
EXECUTION_PATHS = (
    ROOT / "data/stage12/stage12_reserve_execution_marker.json",
    ROOT / "data/stage12/stage12_reserve_candidates.json",
    ROOT / "data/stage12/stage12_reserve_acceptance.json",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts).encode("utf-8")
    return prefix + "-" + hashlib.sha1(payload).hexdigest()[:20]


def build() -> tuple[list[dict], dict]:
    if any(path.exists() for path in EXECUTION_PATHS):
        raise PermissionError("Reserve Gold cannot be changed after Reserve execution")
    reviews = json.loads(REVIEWS_PATH.read_text(encoding="utf-8"))
    evidence_rows = _rows(EVIDENCE_PATH)
    if reviews.get("status") != "original_review_complete_before_reserve_model_run":
        raise ValueError("Reserve Gold source review is not complete")
    if reviews.get("coverage_scope") != "all_semantic_units_in_selected_regions_only":
        raise ValueError("Reserve Gold coverage scope is not explicit")
    evidence_by_key = {(row["document_key"], row["physical_page"]): row for row in evidence_rows}
    region_reviews = reviews.get("regions", [])
    review_by_key = {(row["document_key"], row["physical_page"]): row for row in region_reviews}
    if len(evidence_by_key) != 5 or len(review_by_key) != 5 or set(evidence_by_key) != set(review_by_key):
        raise ValueError("Reserve Gold reviews must cover each selected region exactly once")
    rows = []
    issues = []
    for key in sorted(review_by_key):
        evidence = evidence_by_key[key]
        review = review_by_key[key]
        if review.get("source_locator") != evidence.get("source_locator"):
            issues.append(f"{key}:source_locator")
        if evidence.get("review_status") != "accepted" or evidence.get("coverage_scope") != "selected_region_only":
            issues.append(f"{key}:source_review")
        statements = review.get("statements", [])
        if not statements:
            issues.append(f"{key}:empty_gold")
        for spec in statements:
            statement_text = str(spec.get("text") or "").strip()
            subject = str(spec.get("subject") or "").strip()
            if not statement_text or not subject or subject not in statement_text:
                issues.append(f"{key}:statement_or_subject")
                continue
            if spec.get("type") not in {"fact", "requirement", "procedure", "condition", "observation", "verification", "limitation"}:
                issues.append(f"{key}:statement_type")
            if spec.get("predicate") not in {"requires", "prohibits", "describes", "causes", "verifies", "limits_scope"}:
                issues.append(f"{key}:predicate")
            if spec.get("modality") not in {"shall", "must", "recommended", "descriptive"}:
                issues.append(f"{key}:modality")
            quantity = spec.get("quantity")
            answer = spec.get("answer_injection")
            source_for_quantity = evidence["source_text"]
            if answer:
                question_groups = [group for group in evidence.get("related_source_context", []) if group.get("group_kind") == "question_options" and group.get("source_review_status") == "confirmed"]
                group = question_groups[0] if len(question_groups) == 1 else None
                if group is None or answer.get("marked_option") != group.get("answer_option") or str(answer.get("option_text")) != str(group.get("answer_text")) or answer.get("question_id") not in evidence["source_text"]:
                    issues.append(f"{key}:answer_mapping")
                else:
                    resolved = _resolved_marked_answer_text({"source_question": {"group_id": group["group_id"], "status": "linked"}}, evidence)
                    if resolved is None:
                        issues.append(f"{key}:answer_mapping")
                    else:
                        source_for_quantity = resolved
            if quantity is not None:
                if not isinstance(quantity, dict) or not quantity.get("unit") or quantity.get("value") is None:
                    issues.append(f"{key}:quantity")
                else:
                    def matches(actual):
                        return any(
                            item.get("value") == quantity.get("value")
                            and _canonical_quantity_unit(item.get("unit")) == _canonical_quantity_unit(quantity.get("unit"))
                            and item.get("operator") == quantity.get("operator")
                            for item in actual
                        )
                    if not matches(_quantity_fields(statement_text)[0]):
                        issues.append(f"{key}:quantity_statement")
                    if not matches(_quantity_fields("".join(source_for_quantity.split()))[0]):
                        issues.append(f"{key}:quantity_source")
            entities = [{
                "surface_form": subject, "role": "subject",
                "entity_id": _id("reserve-gold-entity", evidence["sample_id"], subject),
                "entity_class": spec.get("subject_class", "UnresolvedEntityCandidate"),
                "match_type": "reviewed_source_surface", "review_status": "accepted",
            }]
            if spec.get("object"):
                obj = str(spec["object"])
                entities.append({
                    "surface_form": obj, "role": "object",
                    "entity_id": _id("reserve-gold-entity", evidence["sample_id"], obj),
                    "entity_class": "UnresolvedEntityCandidate",
                    "match_type": "reviewed_source_surface", "review_status": "accepted",
                })
            scope = {
                "document_key": evidence["document_key"],
                "physical_page": evidence["physical_page"],
                "status": "known" if spec.get("applicability") else "unknown",
            }
            if spec.get("applicability"):
                scope["activity"] = spec["applicability"]
            row = {
                "sample_id": evidence["sample_id"],
                "split": "acceptance_holdout_reserve",
                "task": "statement",
                "label_status": "gold_frozen",
                "document_key": evidence["document_key"],
                "document_logical_id": evidence["document_logical_id"],
                "revision_id": evidence["revision_id"],
                "physical_page": evidence["physical_page"],
                "logical_page": evidence.get("logical_page"),
                "evidence_bindings": [{"evidence_id": evidence["evidence_id"], "support_type": "direct"}],
                "source_span_ids": [evidence["source_span_id"]],
                "source_text_sha256": evidence["source_text_sha256"],
                "evidence_version_id": evidence["evidence_version_id"],
                "evidence_quote": evidence["source_text"],
                "statement_id": _id("reserve-gold", evidence["sample_id"], statement_text),
                "statement_text": statement_text,
                "statement_type": spec["type"],
                "predicate": spec["predicate"],
                "relation_direction": "subject_to_object",
                "subject_entity_id": entities[0]["entity_id"],
                "entity_alignment": entities,
                "object_value": {"kind": "source_assertion", "value": statement_text},
                "value": quantity.get("value") if quantity else None,
                "unit": quantity.get("unit") if quantity else None,
                "quantities": [quantity] if quantity else [],
                "normative_modality": spec["modality"],
                "negation_scope": [
                    {"surface_form": value, "polarity": "negative", "scope_type": "statement"}
                    for value in spec.get("negation", [])
                ],
                "conditions": [
                    {"surface_form": value, "kind": "condition"}
                    for value in spec.get("conditions", [])
                ],
                "applicability_scope": scope,
                "review_status": "accepted",
                "reviewer": reviews["reviewer_id"],
                "reviewer_type": "original_page_semantic_review",
                "review_basis": reviews["review_basis"],
                "review_reason": "Independent of Reserve model output; source region reviewed before execution.",
                "source_locator": review["source_locator"],
                "coverage_scope": "selected_region_only",
                "formal_release": False,
            }
            if answer:
                row["answer_injection"] = answer
            rows.append(row)
    if len({row["statement_id"] for row in rows}) != len(rows):
        issues.append("duplicate_gold_ids")
    if issues:
        raise ValueError("Reserve Gold review is inconsistent: " + ", ".join(sorted(set(issues))))
    audit = {
        "schema_version": 1,
        "stage": "12",
        "artifact_kind": "stage12_reserve_gold_audit",
        "status": "reviewed_pre_execution",
        "gold_status": "reviewed_and_frozen_for_selected_regions",
        "freeze_eligibility": "READY_TO_FREEZE",
        "coverage_scope": reviews["coverage_scope"],
        "reviewer_id": reviews["reviewer_id"],
        "adjudication_validation": {"complete_current_and_independently_provenanced": True},
        "region_count": len(region_reviews),
        "gold_statement_count": len(rows),
        "per_region_gold_count": {
            f"{key[0]}:{key[1]}": len(review_by_key[key]["statements"])
            for key in sorted(review_by_key)
        },
        "inputs": {
            "reserve_evidence": {"path": "data/stage12/stage12_reserve_evidence.jsonl", "sha256": _sha(EVIDENCE_PATH)},
            "source_reviews": {"path": "data/stage12/stage12_reserve_gold_reviews.json", "sha256": _sha(REVIEWS_PATH)},
        },
        "reserve_model_invocations": 0,
        "formal_release": False,
        "producer": "scripts/build_stage12_reserve_gold.py",
    }
    return rows, audit


if __name__ == "__main__":
    gold, audit = build()
    OUTPUT_PATH.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in gold), encoding="utf-8")
    AUDIT_PATH.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": audit["status"], "gold_statement_count": len(gold)}, ensure_ascii=False))

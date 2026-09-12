"""Minimal Stage 8 ontology design and candidate mapping builder."""

from __future__ import annotations

import json
import re
from pathlib import Path


REQUIRED_TOP_LEVEL_CLASSES = {"PhysicalEntity", "ProcessEntity", "InformationEntity", "Situation", "Context"}
REQUIRED_RUNTIME_CLASSES = {
    "Equipment", "Component", "ProcedureDefinition", "StepDefinition",
    "EngineeringStatement", "Evidence", "ApplicabilityScope", "QuantityValue",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_contract(contract: dict) -> None:
    if contract.get("schema_version") != 1 or contract.get("stage") != "8":
        raise ValueError("invalid Stage 8 ontology contract header")
    top = {row.get("id") for row in contract.get("top_level_classes", [])}
    runtime = {row.get("id") for row in contract.get("runtime_classes", [])}
    if top != REQUIRED_TOP_LEVEL_CLASSES:
        raise ValueError("Stage 8 contract must contain exactly five top-level classes")
    if runtime != REQUIRED_RUNTIME_CLASSES:
        raise ValueError("Stage 8 contract must contain exactly eight runtime classes")
    if top & runtime:
        raise ValueError("top-level and runtime classes must remain separate")
    if "EngineeringCase" in top or "EngineeringCase" in runtime:
        raise ValueError("EngineeringCase is deferred beyond Stage 8")
    mapping = contract.get("candidate_mapping", {})
    if mapping.get("automatic_promotion") is not False:
        raise ValueError("Stage 8 candidate mapping cannot automatically promote terms")
    if mapping.get("review_status") != "pending_manual_review":
        raise ValueError("Stage 8 mapping must stop at manual review")
    expected_questions = {f"cap-{i:02d}" for i in range(1, 11)}
    if set(contract.get("capability_coverage", {})) != expected_questions:
        raise ValueError("Stage 8 contract must cover all ten Stage 7 capability questions")
    if set(contract.get("capability_status", {})) != expected_questions:
        raise ValueError("Stage 8 contract must classify all ten capability questions")


def _turtle_literal(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_turtle(contract: dict) -> str:
    validate_contract(contract)
    namespace = contract["namespace"]
    lines = [
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        f"@prefix tv2: <{namespace}> .",
        "",
        "tv2: a owl:Ontology ;",
        "    rdfs:label \"核电汽轮机安调 v2 最小本体\"@zh ;",
        "    rdfs:comment \"Stage 8 最小本体设计；不包含案例、SHACL 或 Release。\"@zh .",
        "",
    ]
    for row in contract["top_level_classes"]:
        lines.extend([
            f"tv2:{row['id']} a owl:Class ;",
            f"    rdfs:label {_turtle_literal(row['label'])}@zh ;",
            f"    rdfs:comment {_turtle_literal(row['comment'])}@zh .",
            "",
        ])
    for row in contract["runtime_classes"]:
        lines.extend([
            f"tv2:{row['id']} a owl:Class ;",
            f"    rdfs:subClassOf tv2:{row['parent']} ;",
            f"    rdfs:label {_turtle_literal(row['label'])}@zh ;",
            f"    rdfs:comment {_turtle_literal(row['comment'])}@zh .",
            "",
        ])
    for row in contract["object_properties"]:
        lines.extend([
            f"tv2:{row['id']} a owl:ObjectProperty ;",
            f"    rdfs:domain tv2:{row['domain']} ;",
            f"    rdfs:range tv2:{row['range']} ;",
            f"    rdfs:label {_turtle_literal(row['label'])}@zh .",
            "",
        ])
    for row in contract["datatype_properties"]:
        range_name = row["range"].rsplit("#", 1)[-1]
        lines.extend([
            f"tv2:{row['id']} a owl:DatatypeProperty ;",
            f"    rdfs:domain tv2:{row['domain']} ;",
            f"    rdfs:range xsd:{range_name} ;",
            f"    rdfs:label {_turtle_literal(row['label'])}@zh .",
            "",
        ])
    return "\n".join(lines)


def _occurrence_keys(row: dict) -> set[tuple]:
    return {
        (item.get("document_key"), item.get("physical_page"), item.get("page_id"))
        for item in row.get("occurrences", [])
    }


def _is_numeric_only_label(label: str) -> bool:
    """A number/unit token is data, even if Stage 7 assigned it another lexical type."""
    return bool(label and any(char.isdigit() for char in label) and not any("\u4e00" <= char <= "\u9fff" for char in label))


def _is_sentence_or_document_fragment(label: str, policy: dict) -> bool:
    if not label:
        return True
    if any(mark in label for mark in "，。；：？！"):
        return True
    if re.match(policy.get("chapter_prefix_pattern", r"^第[0-9一二三四五六七八九十]+章"), label):
        return True
    return any(label.startswith(prefix) for prefix in policy.get("fragment_prefixes", []))


def _is_generic_deferred_process(label: str, policy: dict) -> bool:
    return (
        label in set(policy.get("defer_generic_process_labels", []))
        or any(label.endswith(suffix) for suffix in policy.get("defer_generic_process_suffixes", []))
    )


def _is_shadowed_process_prefix(row: dict, peers: list[dict]) -> bool:
    """Drop a shorter lexical prefix when a longer process label occurs on the same pages."""
    label = row.get("normalized_form", "")
    if not label:
        return False
    row_pages = _occurrence_keys(row)
    for peer in peers:
        peer_label = peer.get("normalized_form", "")
        if (
            peer is not row
            and peer.get("candidate_type") == row.get("candidate_type")
            and len(peer_label) > len(label)
            and peer_label.startswith(label)
            and row_pages & _occurrence_keys(peer)
        ):
            return True
    return False


def select_mapping_shortlist(contract: dict, candidates: list[dict], questions: list[dict]) -> list[dict]:
    mapping = contract["candidate_mapping"]["allowed_candidate_types"]
    max_per_type = contract["candidate_mapping"]["max_candidates_per_type"]
    filter_policy = contract["candidate_mapping"].get("filter_policy", {})
    question_ids = {row["question_id"] for row in questions}
    eligible = [
        row for row in candidates
        if row.get("review_status") == contract["candidate_mapping"]["required_candidate_status"]
        and row.get("candidate_type") in mapping
        and any(item.get("source_kind") == "accepted_stage6_evidence" for item in row.get("occurrences", []))
        and set(row.get("capability_question_ids", [])) <= question_ids
        and not (
            filter_policy.get("exclude_numeric_only_values", True)
            and _is_numeric_only_label(row.get("normalized_form", ""))
        )
        and not (
            filter_policy.get("exclude_sentence_or_scope_fragments", True)
            and _is_sentence_or_document_fragment(row.get("normalized_form", ""), filter_policy)
        )
        and not (
            row.get("candidate_type") == "process"
            and (
                _is_generic_deferred_process(row.get("normalized_form", ""), filter_policy)
                or len(row.get("normalized_form", "")) < filter_policy.get("minimum_process_label_chars", 0)
            )
        )
    ]
    selected: list[dict] = []
    for candidate_type in sorted(mapping):
        ordered_rows = sorted(
            (
                row for row in eligible
                if row["candidate_type"] == candidate_type
                and not (
                    contract["candidate_mapping"].get("filter_policy", {}).get("exclude_shadowed_process_prefixes", True)
                    and candidate_type == "process"
                    and _is_shadowed_process_prefix(row, eligible)
                )
            ),
            key=lambda row: (-float(row.get("document_equal_weighted_score", 0)), row["candidate_id"]),
        )
        deduplicated_rows = []
        seen_labels = set()
        for row in ordered_rows:
            label = row.get("normalized_form", "")
            if label in seen_labels:
                continue
            seen_labels.add(label)
            deduplicated_rows.append(row)
        rows = deduplicated_rows[:filter_policy.get("max_candidates_by_type", {}).get(candidate_type, max_per_type)]
        for row in rows:
            accepted_occurrences = [
                item for item in row["occurrences"]
                if item.get("source_kind") == "accepted_stage6_evidence"
            ]
            requires_original_confirmation = row.get("requires_original_confirmation") is True or not any(
                item.get("text_origin") == "native_text" for item in accepted_occurrences
            )
            review_requirements = [
                "confirm candidate-to-class mapping",
                "confirm normalized label against the original page",
                "confirm every occurrence used for mapping is original-page Evidence",
                "confirm applicability and non-merging boundaries",
            ]
            if requires_original_confirmation:
                review_requirements.insert(1, "confirm OCR-derived surface form against the original page")
            selected.append({
                "candidate_id": row["candidate_id"],
                "surface_form": row["surface_form"],
                "normalized_form": row["normalized_form"],
                "candidate_type": row["candidate_type"],
                "mapped_class": mapping[row["candidate_type"]]["target_class"],
                "mapping_kind": mapping[row["candidate_type"]]["mapping_kind"],
                "capability_question_ids": sorted(row.get("capability_question_ids", [])),
                "candidate_content_fingerprint": row["content_fingerprint"],
                "source_occurrences": accepted_occurrences,
                "excluded_occurrence_count": len(row["occurrences"]) - sum(
                    item.get("source_kind") == "accepted_stage6_evidence" for item in row["occurrences"]
                ),
                "source_text_origins": row["text_origins"],
                "requires_original_confirmation": requires_original_confirmation,
                "review_status": contract["candidate_mapping"]["review_status"],
                "selection_reason": "capability coverage plus original-page Evidence; equal-weight score is only a deterministic tie-break",
                "review_requirements": review_requirements,
            })
    return selected


def build_mapping_payload(contract: dict, candidates_payload: dict, capability_payload: dict) -> dict:
    validate_contract(contract)
    if candidates_payload.get("status") != "candidate_only" or candidates_payload.get("automatic_promotion") is not False:
        raise ValueError("Stage 8 accepts only candidate_only Stage 7 terminology output")
    questions = capability_payload.get("questions", [])
    if capability_payload.get("artifact_kind") != "business_capability_questions" or len(questions) != 10:
        raise ValueError("Stage 8 requires the ten Stage 7 business capability questions")
    shortlist = select_mapping_shortlist(contract, candidates_payload.get("candidates", []), questions)
    return {
        "schema_version": 1,
        "stage": "8",
        "artifact_kind": "ontology_mapping_shortlist",
        "status": "pending_manual_review",
        "formal_release": False,
        "producer": "scripts/build_stage8_ontology.py",
        "consumer": ["Stage 8 manual mapping review", "Stage 8 exit audit"],
        "inputs": {
            "ontology_contract": "config/ontology_contract.json",
            "terminology_candidates": "data/stage7/terminology_candidates.json",
            "business_capability_questions": "data/stage7/business_capability_questions.json",
        },
        "automatic_promotion": False,
        "review_boundary": "Only this shortlist is eligible for manual ontology mapping review; no candidate enters formal vocabulary before approval.",
        "capability_coverage": contract["capability_coverage"],
        "capability_status": contract["capability_status"],
        "deferred_scope": contract["deferred_scope"],
        "filter_policy": contract["candidate_mapping"]["filter_policy"],
        "deferred_candidate_types": contract["candidate_mapping"]["deferred_candidate_types"],
        "candidate_disposition_schema": contract["candidate_mapping"]["candidate_disposition_schema"],
        "shortlist": shortlist,
    }


def apply_mapping_review_overlay(mapping_payload: dict, overlay: list[dict]) -> dict:
    """Apply independent Stage 8 review decisions without changing Stage 7 candidates."""
    rows = {row["candidate_id"]: dict(row) for row in mapping_payload["shortlist"]}
    for decision in overlay:
        candidate_id = decision.get("candidate_id")
        if candidate_id not in rows:
            raise ValueError(f"Stage 8 review overlay references a non-shortlisted candidate: {candidate_id}")
        row = rows[candidate_id]
        if decision.get("candidate_content_fingerprint") != row["candidate_content_fingerprint"]:
            raise ValueError(f"Stage 8 review overlay fingerprint mismatch: {candidate_id}")
        outcome = decision.get("decision")
        if outcome not in {"accepted", "deferred"}:
            raise ValueError(f"invalid Stage 8 mapping review decision: {outcome}")
        row["mapping_review_decision"] = outcome
        disposition = decision.get("disposition")
        expected_disposition = "class" if outcome == "accepted" else "defer"
        if disposition != expected_disposition:
            raise ValueError(f"Stage 8 review overlay disposition does not match decision: {candidate_id}")
        row["candidate_disposition"] = disposition
        confirmation = decision.get("original_page_confirmation")
        if confirmation not in {None, "confirmed_by_user"}:
            raise ValueError(f"invalid Stage 8 original-page confirmation: {candidate_id}")
        if confirmation == "confirmed_by_user":
            row["requires_original_confirmation"] = False
            row["original_page_confirmation_status"] = "confirmed_by_user"
            row["review_requirements"] = [
                item for item in row["review_requirements"]
                if "OCR-derived" not in item
                and "normalized label against the original page" not in item
                and "every occurrence used for mapping is original-page Evidence" not in item
            ]
        else:
            row["original_page_confirmation_status"] = (
                "pending" if row["requires_original_confirmation"] else "not_required"
            )
        row["reviewer"] = decision.get("reviewer", "unknown")
        row["reviewed_at"] = decision.get("reviewed_at")
        row["decision_reason"] = decision.get("decision_reason", "")
        row["promotion_status"] = "candidate_only"
        rows[candidate_id] = row
    for row in rows.values():
        row.setdefault("mapping_review_decision", "pending_manual_review")
        row.setdefault("candidate_disposition", "class")
        row.setdefault(
            "original_page_confirmation_status",
            "pending" if row["requires_original_confirmation"] else "not_required",
        )
        row.setdefault("promotion_status", "candidate_only")
    result = dict(mapping_payload)
    all_rows = [rows[key] for key in sorted(rows)]
    result["shortlist"] = [
        row for row in all_rows
        if row["mapping_review_decision"] != "deferred"
    ]
    result["deferred_candidates"] = [
        row for row in all_rows
        if row["mapping_review_decision"] == "deferred"
    ]
    result["review_summary"] = {
        "accepted_count": sum(row["mapping_review_decision"] == "accepted" for row in all_rows),
        "deferred_count": sum(row["mapping_review_decision"] == "deferred" for row in all_rows),
        "pending_manual_review_count": sum(
            row["mapping_review_decision"] == "pending_manual_review" for row in all_rows
        ),
    }
    result["status"] = (
        "review_complete_candidate_only"
        if all(
            row["mapping_review_decision"] != "pending_manual_review"
            and not row["requires_original_confirmation"]
            for row in all_rows
            if row["mapping_review_decision"] != "deferred"
        )
        else "pending_manual_review"
    )
    return result


def build_review_queue(mapping_payload: dict) -> list[dict]:
    return [
        {
            "review_id": f"ontology-review-{row['candidate_id']}",
            "candidate_id": row["candidate_id"],
            "candidate_content_fingerprint": row["candidate_content_fingerprint"],
            "normalized_form": row["normalized_form"],
            "proposed_class": row["mapped_class"],
            "mapping_kind": row["mapping_kind"],
            "review_status": row["review_status"],
            "mapping_review_decision": row.get("mapping_review_decision", "pending_manual_review"),
            "candidate_disposition": row.get("candidate_disposition", "class"),
            "requires_original_confirmation": row["requires_original_confirmation"],
            "source_occurrences": row["source_occurrences"],
            "excluded_occurrence_count": row["excluded_occurrence_count"],
            "review_requirements": row["review_requirements"],
            "review_gate": (
                "original_page_confirmation"
                if row["requires_original_confirmation"]
                else "mapping_decision"
            ),
        }
        for row in mapping_payload["shortlist"]
        if (
            row.get("mapping_review_decision", "pending_manual_review") == "pending_manual_review"
            or row["requires_original_confirmation"]
        )
    ]

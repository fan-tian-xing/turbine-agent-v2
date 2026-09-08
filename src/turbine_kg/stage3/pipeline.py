"""One deterministic, testable Stage 3 end-to-end path."""

from __future__ import annotations

from typing import Any

from .models import Claim, FixtureDocument, ScopeContext
from .retrieval import retrieve
from .validation import validate_claim


def _build_claim(hit, context: ScopeContext, *, high_risk_action: bool) -> Claim:
    statement = hit.statement
    return Claim(
        claim_id=f"claim-{statement.statement_id}",
        claim_type="action_authorization" if high_risk_action else (
            "fact" if statement.statement_type in {
                "inspection_requirement", "acceptance_requirement", "maintenance_procedure", "scope_definition"
            } else statement.statement_type
        ),
        text=statement.text,
        statement_id=statement.statement_id,
        evidence_ids=statement.evidence_ids,
        object_id=statement.object_id,
        context=context,
        value=statement.value,
        unit=statement.unit,
        quantities=statement.quantities,
    )


def _high_risk_intent(question: str) -> bool:
    lowered = question.lower()
    return any(
        token in lowered
        for token in ("authorize", "authorization", "lifting", "adjustment", "execute", "execution", "起吊", "吊装", "调整", "执行")
    )


def _apply_source_qualifier(hits, question: str):
    lowered = question.lower()
    roles = set()
    if any(token in lowered for token in ("manufacturer", "厂家", "制造商")):
        roles.add("manufacturer_manual")
    if any(token in lowered for token in ("generic", "baseline", "规范", "标准")):
        roles.add("standard_or_regulation")
    if any(token in lowered for token in ("training", "培训", "背景")):
        roles.add("training_background")
    if len(roles) != 1:
        return hits
    role = next(iter(roles))
    qualified = tuple(hit for hit in hits if hit.document.source_role == role)
    return qualified or hits


def _claim_result(claim: Claim, validation) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "claim_type": claim.claim_type,
        "text": claim.text,
        "statement_id": claim.statement_id,
        "object_id": claim.object_id,
        "evidence_ids": list(claim.evidence_ids),
        "value": claim.value,
        "unit": claim.unit,
        "quantities": [
            {"min": minimum, "max": maximum, "unit": unit}
            for minimum, maximum, unit in claim.quantities
        ],
        "validation": {
            "allowed": validation.allowed,
            "status": validation.status,
            "failures": list(validation.failures),
            "warnings": list(validation.warnings),
        },
    }


def answer_question(
    documents: tuple[FixtureDocument, ...],
    *,
    primary_question: str,
    context: ScopeContext,
    high_risk_action: bool = False,
) -> dict[str, Any]:
    hits = _apply_source_qualifier(
        retrieve(documents, question=primary_question, context=context),
        primary_question,
    )
    if not hits:
        return {
            "status": "evidence_gap",
            "primary_question": primary_question,
            "claims": [],
            "missing": ["applicable Evidence for the supplied context"],
        }
    high_risk_action = high_risk_action or _high_risk_intent(primary_question)
    hit = hits[0]
    statement = hit.statement
    candidate_values = {(item.statement.object_id, item.statement.value, item.statement.unit) for item in hits if item.statement.value is not None}
    values_by_object_unit: dict[tuple[str, str | None], set[float | None]] = {}
    for object_id, value, unit in candidate_values:
        values_by_object_unit.setdefault((object_id, unit), set()).add(value)
    has_conflicting_values = any(len(values) > 1 for values in values_by_object_unit.values())
    comparison_requested = any(token in primary_question.lower() for token in ("compare", "conflict", "比较", "冲突"))
    lowered_question = primary_question.lower()
    value_requested = (
        ("value" in lowered_question and "unit" in lowered_question)
        or any(token in lowered_question for token in ("how much", "多少", "数值", "单位", "限值", "limit", "间隙"))
    )
    if (comparison_requested or value_requested) and has_conflicting_values:
        claims = []
        for candidate in hits:
            if candidate.statement.value is None:
                continue
            candidate_claim = _build_claim(candidate, context, high_risk_action=high_risk_action)
            candidate_validation = validate_claim(candidate_claim, documents)
            if candidate_validation.allowed:
                claims.append(_claim_result(candidate_claim, candidate_validation))
        return {
            "status": "conflict",
            "primary_question": primary_question,
            "claims": claims,
            "retrieval": {
                "candidate_statement_ids": [item.statement.statement_id for item in hits],
                "conflicting_values": [
                    {"object_id": object_id, "value": value, "unit": unit}
                    for object_id, value, unit in sorted(candidate_values, key=str)
                ],
            },
        }
    claim = _build_claim(hit, context, high_risk_action=high_risk_action)
    validation = validate_claim(claim, documents)
    result: dict[str, Any] = {
        "status": validation.status,
        "primary_question": primary_question,
        "claim": _claim_result(claim, validation),
        "validation": {
            "allowed": validation.allowed,
            "failures": list(validation.failures),
            "warnings": list(validation.warnings),
        },
        "retrieval": {
            "selected_statement_id": statement.statement_id,
            "candidate_statement_ids": [item.statement.statement_id for item in hits],
            "source_document_id": hit.document.revision.document_logical_id,
            "source_role": hit.document.source_role,
        },
    }
    if not validation.allowed:
        result["status"] = "evidence_gap"
        result["claims"] = []
    else:
        result["claims"] = [result.pop("claim")]
    return result

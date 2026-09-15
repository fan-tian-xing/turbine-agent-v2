"""Claim-level validation for the Stage 3 research slice."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal

from .applicability import match_scope
from .models import Claim, FixtureDocument, ValidationResult


ALLOWED_CLAIM_TYPES = {"fact", "conditioned_inference", "candidate_recommendation", "action_authorization"}
_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_UNIT = r"(?:[A-Za-zµμ°%]+|[\u3400-\u9fff]+)(?:[0-9²³]+)?(?:[/·*](?:[A-Za-zµμ°%]+|[\u3400-\u9fff]+)(?:[0-9²³]+)?)?"
NUMBER_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_.+-])(?P<number>{_NUMBER})\s*(?P<unit>{_UNIT})?(?![A-Za-z0-9_]|\.\d)",
    re.IGNORECASE,
)
_RANGE = re.compile(
    rf"(?<![A-Za-z0-9_.+-])(?P<first>{_NUMBER})\s*(?:～|~|–|—|至|到|-)\s*(?P<last>{_NUMBER})\s*(?P<unit>{_UNIT})(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_UNIT_ALIASES = {"毫米": "mm", "厘米": "cm", "微米": "um", "μm": "um", "兆瓦": "mw", "千瓦": "kw", "摄氏度": "c", "°c": "c", "秒": "s", "inch": "in"}
_NON_UNITS = {"and", "or", "to", "is", "are", "was", "must", "should", "limit", "value", "baseline", "threshold"}


def _normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    normalized = unicodedata.normalize("NFKC", unit).lower()
    normalized = "/".join(_UNIT_ALIASES.get(part, part) for part in re.split(r"[/·*]", normalized))
    if normalized in _NON_UNITS:
        return None
    return _UNIT_ALIASES.get(normalized, normalized)


def _text_tokens(value: str) -> set[str]:
    stopwords = {"a", "an", "and", "after", "before", "by", "for", "in", "is", "of", "on", "or", "the", "to"}
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_-]+|[\u3400-\u9fff]{2,}", value)
        if token.lower() not in stopwords
    }
    for run in re.findall(r"[\u3400-\u9fff]{2,}", value):
        tokens.update(run[index:index + 2] for index in range(len(run) - 1))
    return tokens


def _text_supported(needle: str, source: str, *, minimum_overlap: float = 0.6) -> bool:
    needle_tokens = _text_tokens(needle)
    source_tokens = _text_tokens(source)
    if not needle_tokens:
        return False
    return len(needle_tokens & source_tokens) / len(needle_tokens) >= minimum_overlap


def _numeric_mentions(value: str) -> set[tuple[Decimal, str | None]]:
    value = unicodedata.normalize("NFKC", value).replace("−", "-")
    mentions = {
        (Decimal(match.group("number")), _normalize_unit(match.group("unit")))
        for match in NUMBER_PATTERN.finditer(value)
    }
    # A trailing unit applies to both endpoints of a written range.
    for match in _RANGE.finditer(value):
        unit = _normalize_unit(match.group("unit"))
        mentions.discard((Decimal(match.group("first")), None))
        mentions.update((Decimal(match.group(endpoint)), unit) for endpoint in ("first", "last"))
    return mentions


def _numbers_supported(text: str, source: str) -> bool:
    """Check prose quantities even when the model omits numeric metadata.

    Unitless restatements may omit a stored unit; a stated unit must match.
    This research validator does not authorize conversions or calculations.
    """
    supported = _numeric_mentions(source)
    return all(
        (number, unit) in supported or (unit is None and any(number == candidate for candidate, _ in supported))
        for number, unit in _numeric_mentions(text)
    )


def _quantity_supported(text: str, value: float | None, unit: str | None) -> bool:
    if value is None:
        return True
    expected_value = Decimal(str(value))
    expected_unit = _normalize_unit(unit)
    return any(
        mention_value == expected_value and (expected_unit is None or mention_unit == expected_unit)
        for mention_value, mention_unit in _numeric_mentions(text)
    )


def _quantities_supported(text: str, quantities: tuple[tuple[float | None, float | None, str], ...]) -> bool:
    mentions = _numeric_mentions(text)
    for minimum, maximum, unit in quantities:
        expected_unit = _normalize_unit(unit)
        expected = {
            number
            for number, mentioned_unit in mentions
            if mentioned_unit == expected_unit
        }
        if minimum is not None and Decimal(str(minimum)) not in expected:
            return False
        if maximum is not None and Decimal(str(maximum)) not in expected:
            return False
    return True


def _has_negation(text: str) -> bool:
    lowered = text.lower()
    return any(
        token in lowered
        for token in ("not", "no ", "never", "without", "cannot", "can't", "do not", "does not", "must not", "不", "未", "无", "禁止", "不得", "不能")
    )


def validate_claim(claim: Claim, documents: tuple[FixtureDocument, ...]) -> ValidationResult:
    if claim.claim_type not in ALLOWED_CLAIM_TYPES:
        return ValidationResult(False, "rejected", ("invalid_claim_type",))
    statement = None
    document = None
    evidence_by_id = {}
    for candidate in documents:
        evidence_by_id.update({item.evidence_id: item for item in candidate.evidence})
        for item in candidate.statements:
            if item.statement_id == claim.statement_id:
                statement, document = item, candidate
    if statement is None or document is None:
        return ValidationResult(False, "rejected", ("statement_not_found",))
    failures: list[str] = []
    if not claim.evidence_ids:
        failures.append("evidence_missing")
    for evidence_id in claim.evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            failures.append(f"evidence_not_found:{evidence_id}")
        elif evidence.statement_id != statement.statement_id:
            failures.append(f"evidence_not_supporting_statement:{evidence_id}")
        elif evidence.object_id != claim.object_id:
            failures.append(f"object_mismatch:{evidence_id}")
        else:
            span_by_id = {span.span_id: span for span in document.spans}
            source_text = " ".join(
                span_by_id[span_id].quote
                for span_id in evidence.source_span_ids
                if span_id in span_by_id
            )
            if not _text_supported(evidence.text, source_text):
                failures.append(f"evidence_text_not_supported:{evidence_id}")
            if not _numbers_supported(evidence.text, source_text):
                failures.append(f"evidence_prose_quantity_not_supported:{evidence_id}")
            if not _quantity_supported(evidence.text, evidence.value, evidence.unit):
                failures.append(f"evidence_quantity_not_in_text:{evidence_id}")
            if not _quantity_supported(source_text, evidence.value, evidence.unit):
                failures.append(f"source_span_quantity_not_in_text:{evidence_id}")
            if not _quantities_supported(evidence.text, evidence.quantities):
                failures.append(f"evidence_ranges_not_in_text:{evidence_id}")
            if not _quantities_supported(source_text, evidence.quantities):
                failures.append(f"source_span_ranges_not_in_text:{evidence_id}")
            if evidence.value != statement.value:
                failures.append(f"evidence_value_mismatch:{evidence_id}")
            if evidence.unit != statement.unit:
                failures.append(f"evidence_unit_mismatch:{evidence_id}")
    if statement.object_id != claim.object_id:
        failures.append("object_mismatch:statement")
    if claim.value is not None and statement.value != claim.value:
        failures.append("value_mismatch")
    if claim.unit is not None and statement.unit != claim.unit:
        failures.append("unit_mismatch")
    if not _quantity_supported(statement.text, statement.value, statement.unit):
        failures.append("statement_quantity_not_in_text")
    if not _quantities_supported(statement.text, statement.quantities):
        failures.append("statement_ranges_not_in_text")
    if not _quantity_supported(claim.text, claim.value, claim.unit):
        failures.append("claim_quantity_not_in_text")
    if not _quantities_supported(claim.text, claim.quantities):
        failures.append("claim_ranges_not_in_text")
    supporting_text = " ".join(
        [statement.text]
        + [evidence_by_id[item].text for item in claim.evidence_ids if item in evidence_by_id]
    )
    if not _text_supported(claim.text, supporting_text):
        failures.append("claim_text_not_supported")
    bound_evidence_text = " ".join(
        evidence_by_id[item].text for item in claim.evidence_ids
        if item in evidence_by_id and evidence_by_id[item].statement_id == claim.statement_id
    )
    if not _numbers_supported(claim.text, bound_evidence_text):
        failures.append("claim_prose_quantity_not_supported")
    if not _quantity_supported(bound_evidence_text, claim.value, claim.unit):
        failures.append("claim_quantity_not_in_evidence")
    if not _quantities_supported(bound_evidence_text, claim.quantities):
        failures.append("claim_ranges_not_in_evidence")
    if _has_negation(claim.text) != _has_negation(statement.text):
        failures.append("negation_mismatch")
    scope_result = match_scope(statement.scope, claim.context)
    if any(reason.startswith("mismatch:") for reason in scope_result.reasons):
        failures.append("applicability_mismatch")
    if failures:
        return ValidationResult(False, "rejected", tuple(failures))
    if claim.claim_type == "action_authorization":
        return ValidationResult(
            True,
            "downgraded_candidate",
            warnings=("not_authorized_for_execution",),
        )
    if scope_result.reasons:
        return ValidationResult(True, "conditional_reference", warnings=scope_result.reasons)
    return ValidationResult(True, "validated")

"""Deterministic applicability matching for the Stage 3 slice."""

from __future__ import annotations

from dataclasses import dataclass

from .models import ApplicabilityScope, ScopeContext


@dataclass(frozen=True, slots=True)
class ScopeMatch:
    matched: bool
    reasons: tuple[str, ...]
    specificity: int


def match_scope(scope: ApplicabilityScope, context: ScopeContext | ApplicabilityScope) -> ScopeMatch:
    query = context if isinstance(context, ApplicabilityScope) else context.as_scope()
    reasons: list[str] = []
    specificity = 0
    for field in ("model", "equipment", "lifecycle_stage", "activity", "operating_state", "condition"):
        expected = getattr(scope, field)
        actual = getattr(query, field)
        if expected is None:
            continue
        specificity += 1
        if actual is None:
            reasons.append(f"missing_context:{field}")
        elif actual != expected:
            reasons.append(f"mismatch:{field}")

    expected_range = scope.capacity_range
    actual_range = query.capacity_range
    if expected_range is not None:
        specificity += 1
        if actual_range is None:
            reasons.append("missing_context:capacity_range")
        else:
            if expected_range.minimum is not None and (
                actual_range.minimum is None or actual_range.minimum < expected_range.minimum
            ):
                reasons.append("mismatch:capacity_range_min")
            if expected_range.maximum is not None and (
                actual_range.maximum is None or actual_range.maximum > expected_range.maximum
            ):
                reasons.append("mismatch:capacity_range_max")
    return ScopeMatch(not reasons, tuple(reasons), specificity)


def scope_within_parent(
    parent: ApplicabilityScope, child: ApplicabilityScope
) -> tuple[bool, tuple[str, ...]]:
    """Ensure a child scope never broadens its parent scope."""

    errors: list[str] = []
    for field in ("model", "equipment", "lifecycle_stage", "activity", "operating_state", "condition"):
        parent_value = getattr(parent, field)
        child_value = getattr(child, field)
        if parent_value is not None and child_value != parent_value:
            errors.append(f"child_scope_broadens_parent:{field}")
    parent_range = parent.capacity_range
    child_range = child.capacity_range
    if parent_range is not None:
        if child_range is None:
            errors.append("child_scope_broadens_parent:capacity_range")
        else:
            if parent_range.minimum is not None and (
                child_range.minimum is None or child_range.minimum < parent_range.minimum
            ):
                errors.append("child_scope_broadens_parent:capacity_range_min")
            if parent_range.maximum is not None and (
                child_range.maximum is None or child_range.maximum > parent_range.maximum
            ):
                errors.append("child_scope_broadens_parent:capacity_range_max")
    return not errors, tuple(errors)


def statement_scope_within_source(
    source: ApplicabilityScope, statement: ApplicabilityScope
) -> tuple[bool, tuple[str, ...]]:
    """Compatibility wrapper preserving the Stage 3 contract error names."""
    valid, errors = scope_within_parent(source, statement)
    return valid, tuple(error.replace("child_scope_broadens_parent", "statement_scope_broadens_source") for error in errors)

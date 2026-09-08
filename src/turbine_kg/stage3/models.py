"""Small, explicit contracts used by the Stage 3 research slice.

These contracts intentionally remain independent from the later formal
Document IR, OWL and Release contracts.  The fixture corpus is synthetic and
is never treated as a production knowledge source.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SCOPE_FIELDS = (
    "model",
    "equipment",
    "lifecycle_stage",
    "activity",
    "operating_state",
    "condition",
)


@dataclass(frozen=True, slots=True)
class CapacityRange:
    minimum: float | None = None
    maximum: float | None = None

    @classmethod
    def from_value(cls, value: Any) -> "CapacityRange | None":
        if value is None:
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            return cls(number, number)
        if not isinstance(value, dict):
            raise ValueError("capacity_range must be a number or an object")
        minimum = value.get("min")
        maximum = value.get("max")
        if minimum is not None and not isinstance(minimum, (int, float)):
            raise ValueError("capacity_range.min must be numeric")
        if maximum is not None and not isinstance(maximum, (int, float)):
            raise ValueError("capacity_range.max must be numeric")
        if minimum is not None and maximum is not None and float(minimum) > float(maximum):
            raise ValueError("capacity_range.min cannot exceed capacity_range.max")
        return cls(None if minimum is None else float(minimum), None if maximum is None else float(maximum))

    def as_dict(self) -> dict[str, float] | None:
        if self.minimum is None and self.maximum is None:
            return None
        result: dict[str, float] = {}
        if self.minimum is not None:
            result["min"] = self.minimum
        if self.maximum is not None:
            result["max"] = self.maximum
        return result


@dataclass(frozen=True, slots=True)
class ApplicabilityScope:
    model: str | None = None
    equipment: str | None = None
    lifecycle_stage: str | None = None
    activity: str | None = None
    operating_state: str | None = None
    capacity_range: CapacityRange | None = None
    condition: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ApplicabilityScope":
        unknown = set(value) - set(SCOPE_FIELDS) - {"capacity_range"}
        if unknown:
            raise ValueError(f"unknown applicability fields: {sorted(unknown)}")
        kwargs = {field: value.get(field) for field in SCOPE_FIELDS}
        for field, item in kwargs.items():
            if item is not None and (not isinstance(item, str) or not item.strip()):
                raise ValueError(f"applicability field {field} must be a non-empty string or null")
        kwargs["capacity_range"] = CapacityRange.from_value(value.get("capacity_range"))
        return cls(**kwargs)

    def as_dict(self) -> dict[str, Any]:
        result = {field: getattr(self, field) for field in SCOPE_FIELDS if getattr(self, field) is not None}
        if self.capacity_range is not None:
            result["capacity_range"] = self.capacity_range.as_dict()
        return result


@dataclass(frozen=True, slots=True)
class Asset:
    asset_id: str
    relative_path: str
    asset_kind: str
    source_root_id: str
    registry_applicability_scope: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Revision:
    revision_id: str
    document_logical_id: str
    label: str


@dataclass(frozen=True, slots=True)
class LogicalDocument:
    document_logical_id: str
    title: str
    source_role: str
    source_scope: ApplicabilityScope


@dataclass(frozen=True, slots=True)
class Page:
    page_id: str
    revision_id: str
    page_number: int
    text: str


@dataclass(frozen=True, slots=True)
class SourceSpan:
    span_id: str
    page_id: str
    quote: str
    content_kind: str = "text"
    table_metadata: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    source_span_ids: tuple[str, ...]
    statement_id: str
    object_id: str
    text: str
    value: float | None = None
    unit: str | None = None
    quantities: tuple[tuple[float | None, float | None, str], ...] = ()


@dataclass(frozen=True, slots=True)
class EngineeringStatement:
    statement_id: str
    statement_type: str
    text: str
    evidence_ids: tuple[str, ...]
    object_id: str
    scope: ApplicabilityScope
    value: float | None = None
    unit: str | None = None
    risk_level: str = "normal"
    quantities: tuple[tuple[float | None, float | None, str], ...] = ()


@dataclass(frozen=True, slots=True)
class FixtureDocument:
    asset: Asset
    logical_document: LogicalDocument
    revision: Revision
    source_role: str
    title: str
    source_scope: ApplicabilityScope
    pages: tuple[Page, ...]
    spans: tuple[SourceSpan, ...]
    evidence: tuple[Evidence, ...]
    statements: tuple[EngineeringStatement, ...]


@dataclass(frozen=True, slots=True)
class ScopeContext:
    model: str | None = None
    equipment: str | None = None
    lifecycle_stage: str | None = None
    activity: str | None = None
    operating_state: str | None = None
    capacity_range: CapacityRange | None = None
    condition: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ScopeContext":
        scope = ApplicabilityScope.from_dict(value)
        return cls(
            model=scope.model,
            equipment=scope.equipment,
            lifecycle_stage=scope.lifecycle_stage,
            activity=scope.activity,
            operating_state=scope.operating_state,
            capacity_range=scope.capacity_range,
            condition=scope.condition,
        )

    def as_scope(self) -> ApplicabilityScope:
        return ApplicabilityScope(
            model=self.model,
            equipment=self.equipment,
            lifecycle_stage=self.lifecycle_stage,
            activity=self.activity,
            operating_state=self.operating_state,
            capacity_range=self.capacity_range,
            condition=self.condition,
        )


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    claim_type: str
    text: str
    statement_id: str
    evidence_ids: tuple[str, ...]
    object_id: str
    context: ScopeContext
    value: float | None = None
    unit: str | None = None
    quantities: tuple[tuple[float | None, float | None, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    statement: EngineeringStatement
    document: FixtureDocument
    score: float
    scope_specificity: int


@dataclass(frozen=True, slots=True)
class ValidationResult:
    allowed: bool
    status: str
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

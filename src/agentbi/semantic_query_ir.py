"""Vendor-neutral intermediate representation for governed semantic queries.

This module deliberately contains no SQL and no integration-specific identifiers.
It is the contract between future natural-language parsers, the ontology service,
and query planners.  The currently shipped SuperSonic workflow does not consume
this contract yet.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AggregateFunction(StrEnum):
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"
    COUNT = "COUNT"
    COUNT_DISTINCT = "COUNT_DISTINCT"


class PredicateOperator(StrEnum):
    EQ = "EQ"
    IN = "IN"
    GT = "GT"
    GTE = "GTE"
    LT = "LT"
    LTE = "LTE"


class SortDirection(StrEnum):
    ASC = "ASC"
    DESC = "DESC"


class TimeGrain(StrEnum):
    DAY = "DAY"
    MONTH = "MONTH"


class AnalysisIntent(StrEnum):
    """The business question the user is asking, independent of execution."""

    QUERY = "QUERY"
    # Kept as a source-compatible alias for the Phase 1 contract name.
    LOOKUP = "QUERY"
    TREND = "TREND"
    COMPARISON = "COMPARISON"
    ROOT_CAUSE = "ROOT_CAUSE"
    BREAKDOWN = "BREAKDOWN"


class AnalysisTargetKind(StrEnum):
    """Small parser-facing subset; this does not replace OntologyAssetKind."""

    METRIC = "METRIC"
    DIMENSION = "DIMENSION"
    ENTITY = "ENTITY"


class ResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
class ValueSource(StrEnum): USER_EXPLICIT="USER_EXPLICIT"; WORKBENCH_CONTEXT="WORKBENCH_CONTEXT"; PLANNER_DEFAULT="PLANNER_DEFAULT"


class TimeScopeKind(StrEnum):
    EXPLICIT = "EXPLICIT"
    RELATIVE = "RELATIVE"


class RelativeTimeReference(StrEnum):
    RECENT = "RECENT"
    LAST_N_DAYS = "LAST_N_DAYS"
    LAST_N_WEEKS = "LAST_N_WEEKS"
    LAST_N_MONTHS = "LAST_N_MONTHS"
    YESTERDAY = "YESTERDAY"
    THIS_WEEK = "THIS_WEEK"


class SignalDirection(StrEnum):
    DECLINE = "DECLINE"
    INCREASE = "INCREASE"
    CHANGE = "CHANGE"
    ANOMALY = "ANOMALY"


class ComparisonType(StrEnum):
    YOY = "YOY"
    MOM = "MOM"
    PREVIOUS_PERIOD = "PREVIOUS_PERIOD"
    CUSTOM = "CUSTOM"


class OntologyReference(BaseModel):
    """A stable reference to one published ontology asset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)


class AnalysisTarget(BaseModel):
    """A user-mentioned business object, optionally grounded to an ontology ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_text: str = Field(min_length=1, max_length=256)
    kind: AnalysisTargetKind
    asset_id: str | None = Field(default=None, min_length=1, max_length=128)
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    candidate_asset_ids: tuple[str, ...] = Field(default_factory=tuple, max_length=10)
    source: ValueSource = ValueSource.USER_EXPLICIT

    @model_validator(mode="after")
    def _validate_resolution(self) -> AnalysisTarget:
        if self.resolution_status is ResolutionStatus.RESOLVED and self.asset_id is None:
            raise ValueError("resolved analysis targets require an asset_id")
        if self.resolution_status is not ResolutionStatus.RESOLVED and self.asset_id is not None:
            raise ValueError("only resolved analysis targets may carry an asset_id")
        if self.resolution_status is ResolutionStatus.AMBIGUOUS and len(self.candidate_asset_ids) < 2:
            raise ValueError("ambiguous analysis targets require at least two candidates")
        if self.resolution_status is not ResolutionStatus.AMBIGUOUS and self.candidate_asset_ids:
            raise ValueError("candidate_asset_ids are only valid for ambiguous targets")
        return self


class SemanticScope(BaseModel):
    """One simple business range condition; it deliberately is not a predicate DSL."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: AnalysisTarget
    operator: PredicateOperator = PredicateOperator.EQ
    value: str | int | float | bool | list[str | int | float | bool]
    source: ValueSource = ValueSource.USER_EXPLICIT


class TimeScope(BaseModel):
    """User-expressed calendar or relative-time scope without execution dates."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_text: str = Field(min_length=1, max_length=128)
    kind: TimeScopeKind
    relative_reference: RelativeTimeReference | None = None
    amount: int | None = Field(default=None, ge=1, le=10_000)
    source: ValueSource = ValueSource.USER_EXPLICIT

    @model_validator(mode="after")
    def _validate_shape(self) -> TimeScope:
        if self.kind is TimeScopeKind.EXPLICIT and (
            self.relative_reference is not None or self.amount is not None
        ):
            raise ValueError("explicit time scopes cannot carry relative fields")
        if self.kind is TimeScopeKind.RELATIVE and self.relative_reference is None:
            raise ValueError("relative time scopes require a relative_reference")
        if self.amount is not None and self.relative_reference not in {
            RelativeTimeReference.LAST_N_DAYS,
            RelativeTimeReference.LAST_N_WEEKS,
            RelativeTimeReference.LAST_N_MONTHS,
        }:
            raise ValueError("amount is only valid for last-N relative time scopes")
        return self


class AnalysisSignal(BaseModel):
    """A user-observed or user-concerned business phenomenon."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    direction: SignalDirection
    raw_text: str = Field(min_length=1, max_length=128)


class ComparisonSpec(BaseModel):
    """An explicit comparison requested by the user, never a parser default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ComparisonType
    raw_text: str = Field(min_length=1, max_length=128)


class MetricRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: OntologyReference
    aggregation: AggregateFunction | None = None
    alias: str | None = Field(default=None, min_length=1, max_length=128)


class DimensionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: OntologyReference
    alias: str | None = Field(default=None, min_length=1, max_length=128)
    time_grain: TimeGrain | None = None


class SemanticPredicate(BaseModel):
    """A typed predicate whose field must resolve through the ontology."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: OntologyReference
    operator: PredicateOperator
    value: str | int | float | bool | list[str | int | float | bool]


class SemanticOrdering(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_alias: str = Field(min_length=1, max_length=128)
    direction: SortDirection = SortDirection.DESC


class SemanticQueryIR(BaseModel):
    """Canonical user-analysis request, with an optional legacy execution view.

    ``metrics``/``dimensions``/``predicates`` remain the resolved legacy query
    representation.  The Phase 2 fields record what the user said before
    grounding and do not select an analysis strategy or execution method.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ir_version: str = "2.0"
    ontology_version: str = Field(min_length=1, max_length=64)
    entities: list[OntologyReference] = Field(default_factory=list, max_length=20)
    metrics: list[MetricRequest] = Field(default_factory=list, max_length=20)
    dimensions: list[DimensionRequest] = Field(default_factory=list, max_length=20)
    predicates: list[SemanticPredicate] = Field(default_factory=list, max_length=50)
    order_by: list[SemanticOrdering] = Field(default_factory=list, max_length=10)
    limit: int | None = Field(default=None, ge=1, le=10_000)
    source_question: str = Field(min_length=1, max_length=2_000)
    intent: AnalysisIntent = AnalysisIntent.QUERY
    targets: list[AnalysisTarget] = Field(default_factory=list, max_length=20)
    scopes: list[SemanticScope] = Field(default_factory=list, max_length=50)
    time_scope: TimeScope | None = None
    signal: AnalysisSignal | None = None
    comparison: ComparisonSpec | None = None
    breakdown_hints: list[AnalysisTarget] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _validate_has_target(self) -> SemanticQueryIR:
        if not self.metrics and not self.targets:
            raise ValueError("semantic query IR requires a legacy metric or an analysis target")
        return self

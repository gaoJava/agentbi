"""Deterministic execution evidence transformed into runtime analysis observations."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agentbi.query_planner.contracts import ExecutionPlan

from .contracts import ExecutionResult, ExecutionStatus


class ObservationStatus(StrEnum):
    SUCCESS = "SUCCESS"
    NO_DATA = "NO_DATA"
    UNSUPPORTED = "UNSUPPORTED"
    EXECUTION_FAILED = "EXECUTION_FAILED"


class ObservationDirection(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"
    UNDEFINED = "UNDEFINED"


class ObservationValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    member: tuple[str, ...] = ()
    value: Decimal | None = None
    ordinal: int = Field(ge=1)
    share: Decimal | None = None


class ObservationProvenance(BaseModel):
    """Trace references only; credentials and tokens are never retained."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    analysis_plan_id: str | None = None
    analysis_step_id: str | None = None
    execution_id: str
    execution_status: ExecutionStatus
    snapshot_version: str | None = None
    binding_id: str | None = None
    backend: str | None = None
    connection_id: str | None = None
    semantic_model_id: int | None = None
    semantic_view_id: int | None = None
    structured_semantic_request: dict[str, Any] | None = None
    generated_sql: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class Observation(BaseModel):
    """A stable fact from one execution, not a business explanation or ontology asset."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    metric_asset_id: str
    dimensions: tuple[str, ...] = ()
    filters: tuple[dict[str, Any], ...] = ()
    status: ObservationStatus
    values: tuple[ObservationValue, ...] = ()
    provenance: ObservationProvenance


class ObservationMemberComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    member: tuple[str, ...] = ()
    current: Decimal | None = None
    baseline: Decimal | None = None
    delta: Decimal | None = None
    delta_pct: Decimal | None = None
    direction: ObservationDirection


class ObservationComparison(BaseModel):
    """Deterministic pairing of two compatible observations; never causal language."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    comparison_id: str = Field(default_factory=lambda: str(uuid4()))
    metric_asset_id: str
    dimensions: tuple[str, ...] = ()
    current_observation_id: str
    baseline_observation_id: str
    members: tuple[ObservationMemberComparison, ...]
    sort_basis: str = "ABSOLUTE_DELTA_DESC"


class ObservationBuilder:
    """Pure deterministic conversion from an executed plan/result pair."""

    @staticmethod
    def build(plan: ExecutionPlan, result: ExecutionResult) -> Observation:
        metric = plan.query.metrics[0].metric.asset_id
        request = result.evidence.get("structured_semantic_request")
        request = request if isinstance(request, dict) else None
        dimensions = tuple(item.dimension.asset_id for item in plan.query.dimensions)
        provenance = ObservationBuilder._provenance(result, request)
        if result.status is not ExecutionStatus.SUCCEEDED:
            unsupported = result.error is not None and result.error.code.value == "UNSUPPORTED_RUNTIME_CAPABILITY"
            return Observation(metric_asset_id=metric, dimensions=dimensions,
                               filters=ObservationBuilder._filters(plan),
                               status=ObservationStatus.UNSUPPORTED if unsupported else ObservationStatus.EXECUTION_FAILED,
                               provenance=provenance)
        if not result.rows:
            return Observation(metric_asset_id=metric, dimensions=dimensions,
                               filters=ObservationBuilder._filters(plan), status=ObservationStatus.NO_DATA,
                               provenance=provenance)
        metric_key = ObservationBuilder._metric_key(request, plan)
        dimension_keys = tuple(
            item.get("semantic_identifier", "") for item in (request or {}).get("dimensions", [])
        )
        raw_values: list[tuple[tuple[str, ...], Decimal | None]] = []
        for row in result.rows:
            raw_values.append((tuple(str(row.get(key)) for key in dimension_keys), _decimal(row.get(metric_key))))
        total = sum((value for _, value in raw_values if value is not None), Decimal(0))
        values = tuple(ObservationValue(member=member, value=value, ordinal=index + 1,
                                        share=(value / total if value is not None and total != 0 and dimension_keys else None))
                       for index, (member, value) in enumerate(raw_values))
        return Observation(metric_asset_id=metric, dimensions=dimensions,
                           filters=ObservationBuilder._filters(plan), status=ObservationStatus.SUCCESS,
                           values=values, provenance=provenance)

    @staticmethod
    def compare(current: Observation, baseline: Observation) -> ObservationComparison:
        if current.metric_asset_id != baseline.metric_asset_id or current.dimensions != baseline.dimensions:
            raise ValueError("observations must use the same metric and dimensions")
        current_by_member = {item.member: item.value for item in current.values}
        baseline_by_member = {item.member: item.value for item in baseline.values}
        members = []
        for member in sorted(set(current_by_member) | set(baseline_by_member)):
            before, after = baseline_by_member.get(member), current_by_member.get(member)
            delta = after - before if after is not None and before is not None else None
            percentage = delta / before if delta is not None and before not in (None, Decimal(0)) else None
            direction = _direction(delta)
            members.append(ObservationMemberComparison(member=member, current=after, baseline=before,
                                                       delta=delta, delta_pct=percentage, direction=direction))
        members.sort(key=lambda item: (abs(item.delta) if item.delta is not None else Decimal(-1), item.member), reverse=True)
        return ObservationComparison(metric_asset_id=current.metric_asset_id, dimensions=current.dimensions,
                                     current_observation_id=current.observation_id,
                                     baseline_observation_id=baseline.observation_id, members=tuple(members))

    @staticmethod
    def _filters(plan: ExecutionPlan) -> tuple[dict[str, Any], ...]:
        return tuple(item.model_dump(mode="json") for item in plan.query.predicates)

    @staticmethod
    def _metric_key(request: dict[str, Any] | None, plan: ExecutionPlan) -> str:
        if request and isinstance(request.get("metrics"), list) and request["metrics"]:
            metric = request["metrics"][0]
            if isinstance(metric, dict) and isinstance(metric.get("semantic_identifier"), str):
                return str(metric.get("alias") or metric["semantic_identifier"])
        return plan.query.metrics[0].alias or plan.query.metrics[0].metric.asset_id

    @staticmethod
    def _provenance(result: ExecutionResult, request: dict[str, Any] | None) -> ObservationProvenance:
        return ObservationProvenance(
            analysis_plan_id=result.analysis_plan_id, analysis_step_id=result.analysis_step_id,
            execution_id=result.execution_id, execution_status=result.status,
            snapshot_version=request.get("snapshot_version") if request else None,
            binding_id=request.get("binding_id") if request else None,
            backend=request.get("backend") if request else None,
            connection_id=request.get("connection_id") if request else None,
            semantic_model_id=request.get("semantic_model_id") if request else None,
            semantic_view_id=request.get("semantic_view_id") if request else None,
            structured_semantic_request=request, generated_sql=result.generated_sql,
            error_code=result.error.code.value if result.error else None,
            error_message=result.error.message if result.error else None,
        )


@dataclass
class AnalysisState:
    """Ephemeral per-analysis evidence store; it has no ontology persistence role."""

    observations: list[Observation] = field(default_factory=list)
    comparisons: list[ObservationComparison] = field(default_factory=list)

    def add(self, observation: Observation) -> Observation:
        self.observations.append(observation); return observation

    def by_step(self, step_id: str) -> tuple[Observation, ...]:
        return tuple(item for item in self.observations if item.provenance.analysis_step_id == step_id)

    def by_metric(self, metric_asset_id: str) -> tuple[Observation, ...]:
        return tuple(item for item in self.observations if item.metric_asset_id == metric_asset_id)

    def by_dimension(self, dimension_asset_id: str) -> tuple[Observation, ...]:
        return tuple(item for item in self.observations if dimension_asset_id in item.dimensions)

    def successful(self) -> tuple[Observation, ...]:
        return tuple(item for item in self.observations if item.status is ObservationStatus.SUCCESS)

    def unavailable(self) -> tuple[Observation, ...]:
        return tuple(item for item in self.observations if item.status in {ObservationStatus.UNSUPPORTED, ObservationStatus.EXECUTION_FAILED})

    def pair(self, current_id: str, baseline_id: str) -> ObservationComparison:
        index = {item.observation_id: item for item in self.observations}
        comparison = ObservationBuilder.compare(index[current_id], index[baseline_id])
        self.comparisons.append(comparison); return comparison


def _decimal(value: Any) -> Decimal | None:
    if value is None: return None
    try: return Decimal(str(value))
    except (InvalidOperation, ValueError): return None


def _direction(delta: Decimal | None) -> ObservationDirection:
    if delta is None: return ObservationDirection.UNDEFINED
    if delta > 0: return ObservationDirection.UP
    if delta < 0: return ObservationDirection.DOWN
    return ObservationDirection.FLAT

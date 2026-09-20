"""Published-runtime-binding request construction and SuperSonic translation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agentbi.ontology_service import (
    RuntimeBackend,
    RuntimeBinding,
    RuntimeBindingResolutionError,
    RuntimeBindingResolver,
)
from agentbi.query_planner.contracts import ExecutionPlan, ExecutionTarget
from agentbi.semantic_query_ir import AggregateFunction, AnalysisIntent, PredicateOperator

from .contracts import ExecutionErrorCode


class StructuredSemanticMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    asset_id: str
    semantic_identifier: str
    aggregation: AggregateFunction | None = None
    alias: str | None = None


class StructuredSemanticDimension(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    asset_id: str
    semantic_identifier: str
    alias: str | None = None
    time_grain: str | None = None


class StructuredSemanticFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    asset_id: str
    semantic_identifier: str
    operator: PredicateOperator
    value: str | int | float | bool | list[str | int | float | bool]


class StructuredSemanticRequest(BaseModel):
    """Structured, version-pinned request with no SQL or physical source fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    snapshot_version: str
    binding_id: str
    backend: RuntimeBackend
    connection_id: str
    semantic_model_id: int
    semantic_view_id: int
    metrics: tuple[StructuredSemanticMetric, ...]
    dimensions: tuple[StructuredSemanticDimension, ...] = ()
    filters: tuple[StructuredSemanticFilter, ...] = ()
    order_by: tuple[tuple[str, str], ...] = ()
    limit: int | None = None


class StructuredSemanticRequestBuilder:
    """Exact-ID builder; it never uses asset names, aliases, fields, or SQL hints."""

    def __init__(self, resolver: RuntimeBindingResolver):
        self._resolver = resolver

    async def build(self, plan: ExecutionPlan) -> StructuredSemanticRequest:
        if plan.target is not ExecutionTarget.SUPERSONIC:
            raise RuntimeBindingResolutionError(ExecutionErrorCode.RUNTIME_BACKEND_MISMATCH, "SuperSonic binding required")
        if not plan.query.metrics:
            raise RuntimeBindingResolutionError(ExecutionErrorCode.MISSING_RUNTIME_BINDING, "metric binding required")
        backend = RuntimeBackend.SUPERSONIC
        first = await self._resolver.resolve(snapshot_version=plan.ontology_version, asset_id=plan.query.metrics[0].metric.asset_id, backend=backend)
        binding = first.binding
        metrics = tuple([await self._metric(item.metric.asset_id, item.alias, item.aggregation, plan, binding, backend) for item in plan.query.metrics])
        dimensions = tuple([await self._dimension(item.dimension.asset_id, item.alias, item.time_grain, plan, binding, backend) for item in plan.query.dimensions])
        filters = tuple([await self._filter(item.dimension.asset_id, item.operator, item.value, plan, binding, backend) for item in plan.query.predicates])
        metric_ids = {item.metric.asset_id for item in plan.query.metrics}
        order_by = tuple(
            (next(item.semantic_identifier for item in metrics if item.asset_id == ordering.target_alias), ordering.direction.value)
            for ordering in plan.query.order_by if ordering.target_alias in metric_ids
        )
        if not order_by and plan.query.intent is AnalysisIntent.TREND and dimensions and dimensions[0].time_grain:
            order_by = ((dimensions[0].semantic_identifier, "ASC"),)
        return StructuredSemanticRequest(snapshot_version=first.snapshot_version, binding_id=binding.id, backend=backend, connection_id=binding.connection_id, semantic_model_id=binding.semantic_model_id, semantic_view_id=binding.semantic_view_id, metrics=metrics, dimensions=dimensions, filters=filters, order_by=order_by, limit=plan.query.limit)

    async def _metric(self, asset_id, alias, requested_aggregation, plan, binding, backend):
        resolved = await self._resolver.resolve(snapshot_version=plan.ontology_version, asset_id=asset_id, backend=backend)
        self._same(binding, resolved.binding, asset_id)
        entry = self._entry(binding.metric_bindings, asset_id)
        return StructuredSemanticMetric(asset_id=asset_id, semantic_identifier=entry.semantic_identifier, aggregation=requested_aggregation or entry.aggregation, alias=alias)

    async def _dimension(self, asset_id, alias, grain, plan, binding, backend):
        resolved = await self._resolver.resolve(snapshot_version=plan.ontology_version, asset_id=asset_id, backend=backend)
        self._same(binding, resolved.binding, asset_id)
        if grain is not None:
            entry = next((item for item in binding.time_grain_bindings if item.dimension_asset_id == asset_id and item.grain is grain), None)
            if entry is None:
                raise RuntimeBindingResolutionError(ExecutionErrorCode.UNSUPPORTED_RUNTIME_CAPABILITY, f"time grain {grain.value} is not bound for {asset_id}")
            return StructuredSemanticDimension(asset_id=asset_id, semantic_identifier=entry.semantic_identifier, alias=alias, time_grain=grain.value)
        entry = self._entry(binding.dimension_bindings, asset_id)
        return StructuredSemanticDimension(asset_id=asset_id, semantic_identifier=entry.semantic_identifier, alias=alias)

    async def _filter(self, asset_id, operator, value, plan, binding, backend):
        resolved = await self._resolver.resolve(snapshot_version=plan.ontology_version, asset_id=asset_id, backend=backend)
        self._same(binding, resolved.binding, asset_id)
        entry = self._entry(binding.dimension_bindings, asset_id)
        return StructuredSemanticFilter(asset_id=asset_id, semantic_identifier=entry.semantic_identifier, operator=operator, value=value)

    @staticmethod
    def _entry(entries, asset_id):
        return next(item for item in entries if item.asset_id == asset_id)

    @staticmethod
    def _same(expected: RuntimeBinding, actual: RuntimeBinding, asset_id: str) -> None:
        if expected.id != actual.id:
            raise RuntimeBindingResolutionError(ExecutionErrorCode.MISSING_RUNTIME_BINDING, f"asset {asset_id} is not bound to selected model")


def to_supersonic_semantic_sql(request: StructuredSemanticRequest) -> str:
    """Vendor adapter translation; aggregate semantics remain governed backend metrics."""
    projections = [item.semantic_identifier for item in request.dimensions] + [_metric_sql(item) for item in request.metrics]
    parts = ["SELECT " + ", ".join(projections), f"FROM s2_table_{request.semantic_view_id}"]
    if request.filters:
        parts.append("WHERE " + " AND ".join(_filter_sql(item) for item in request.filters))
    if request.dimensions:
        parts.append("GROUP BY " + ", ".join(item.semantic_identifier for item in request.dimensions))
    if request.order_by:
        parts.append("ORDER BY " + ", ".join(f"{identifier} {direction}" for identifier, direction in request.order_by))
    if request.limit is not None:
        parts.append(f"LIMIT {request.limit}")
    return " ".join(parts)


def _metric_sql(item: StructuredSemanticMetric) -> str:
    expr = item.semantic_identifier
    if item.aggregation is AggregateFunction.COUNT_DISTINCT:
        expr = f"COUNT(DISTINCT {expr})"
    elif item.aggregation is not None:
        expr = f"{item.aggregation.value}({expr})"
    return f"{expr} AS {item.alias or item.semantic_identifier}" if item.aggregation else expr


def _filter_sql(item: StructuredSemanticFilter) -> str:
    if item.operator is PredicateOperator.IN:
        values = item.value if isinstance(item.value, list) else [item.value]
        return f"{item.semantic_identifier} IN (" + ", ".join(_literal(value) for value in values) + ")"
    operators = {PredicateOperator.EQ: "=", PredicateOperator.GT: ">", PredicateOperator.GTE: ">=", PredicateOperator.LT: "<", PredicateOperator.LTE: "<="}
    return f"{item.semantic_identifier} {operators[item.operator]} {_literal(item.value)}"


def _literal(value: str | float | bool) -> str:
    if isinstance(value, bool): return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)): return str(value)
    return "'" + value.replace("'", "''") + "'"

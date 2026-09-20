"""Published-ontology grounding for the future AnalysisPlanner.

This module builds a finite PlanningContext.  It never selects a strategy,
constructs a plan, executes a capability, or accesses an integration backend.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from agentbi.semantic_query_ir import (
    AnalysisIntent,
    AnalysisSignal,
    AnalysisTarget,
    AnalysisTargetKind,
    ComparisonSpec,
    ResolutionStatus,
    SemanticQueryIR,
    SemanticScope,
    TimeScope,
)

from .contracts import (
    ExecutionMapping,
    OntologyAsset,
    OntologyAssetKind,
    OntologyRelationType,
    OntologyService,
)


class ScopeValueResolutionStatus(StrEnum):
    """Value Ontology is intentionally out of scope; raw values remain explicit."""

    RAW = "RAW"


class GroundedScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    requested: SemanticScope
    dimension: AnalysisTarget
    value_resolution: ScopeValueResolutionStatus = ScopeValueResolutionStatus.RAW


class PlanningGrounding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: AnalysisTarget
    scopes: tuple[GroundedScope, ...] = Field(default_factory=tuple)
    breakdown_hints: tuple[AnalysisTarget, ...] = Field(default_factory=tuple)


class StrategyApplicabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    MISSING_CAPABILITY = "MISSING_CAPABILITY"


class StrategyApplicability(BaseModel):
    """Pure condition result, deliberately not a strategy selection decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    strategy: OntologyAsset
    status: StrategyApplicabilityStatus
    reasons: tuple[str, ...] = Field(default_factory=tuple)


class PlanningContext(BaseModel):
    """Finite business knowledge relevant to one user analysis request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requested_ontology_version: str = Field(min_length=1, max_length=64)
    snapshot_version: str | None = Field(default=None, max_length=64)
    intent: AnalysisIntent
    time_scope: TimeScope | None = None
    signal: AnalysisSignal | None = None
    comparison: ComparisonSpec | None = None
    grounding: PlanningGrounding
    target_asset: OntologyAsset | None = None
    drivers: tuple[OntologyAsset, ...] = Field(default_factory=tuple)
    breakdown_dimensions: tuple[OntologyAsset, ...] = Field(default_factory=tuple)
    dimensions: tuple[OntologyAsset, ...] = Field(default_factory=tuple)
    time_dimensions: tuple[OntologyAsset, ...] = Field(default_factory=tuple)
    available_strategies: tuple[OntologyAsset, ...] = Field(default_factory=tuple)
    available_capabilities: tuple[OntologyAsset, ...] = Field(default_factory=tuple)
    execution_mapping: ExecutionMapping | None = None
    target_relation_types: tuple[OntologyRelationType | str, ...] = Field(default_factory=tuple)
    strategy_applicability: tuple[StrategyApplicability, ...] = Field(default_factory=tuple)
    runtime_metric_asset_ids: frozenset[str] = Field(default_factory=frozenset)
    runtime_dimension_asset_ids: frozenset[str] = Field(default_factory=frozenset)


class PlanningContextBuilder:
    """Compose only published OntologyService reads into a PlanningContext."""

    def __init__(self, ontology_service: OntologyService):
        self._ontology_service = ontology_service

    async def build(self, query: SemanticQueryIR) -> PlanningContext:
        snapshot = await self._ontology_service.published_snapshot(query.ontology_version)
        target = await self._ground_target(query.targets[0], query.ontology_version) if query.targets else None
        grounded_scopes = tuple(
            [await self._ground_scope(scope, query.ontology_version) for scope in query.scopes]
        )
        grounded_breakdown_hints = tuple(
            [await self._ground_target(hint, query.ontology_version) for hint in query.breakdown_hints]
        )
        grounding = PlanningGrounding(
            target=target or _legacy_metric_target(query),
            scopes=grounded_scopes,
            breakdown_hints=grounded_breakdown_hints,
        )
        target_asset = await self._target_asset(grounding.target, query.ontology_version)
        if snapshot is None or target_asset is None or target_asset.kind is not OntologyAssetKind.METRIC:
            return PlanningContext(
                requested_ontology_version=query.ontology_version,
                snapshot_version=snapshot.version if snapshot is not None else None,
                intent=query.intent,
                time_scope=query.time_scope,
                signal=query.signal,
                comparison=query.comparison,
                grounding=grounding,
                target_asset=target_asset,
            )

        drivers = await self._ontology_service.get_metric_drivers(
            target_asset.id, version=snapshot.version
        )
        breakdown_dimensions = await self._ontology_service.get_breakdown_dimensions(
            target_asset.id, version=snapshot.version
        )
        dimensions = await self._ontology_service.get_dimensions(target_asset.id, version=snapshot.version)
        strategies = await self._ontology_service.get_strategy_definitions(version=snapshot.version)
        capabilities = await self._ontology_service.get_capabilities(version=snapshot.version)
        relation_types = await self._ontology_service.get_relation_types(
            target_asset.id, version=snapshot.version
        )
        context = PlanningContext(
            requested_ontology_version=query.ontology_version,
            snapshot_version=snapshot.version,
            intent=query.intent,
            time_scope=query.time_scope,
            signal=query.signal,
            comparison=query.comparison,
            grounding=grounding,
            target_asset=target_asset,
            drivers=drivers,
            breakdown_dimensions=breakdown_dimensions,
            dimensions=dimensions,
            time_dimensions=tuple(dimension for dimension in dimensions if _is_time_dimension(dimension)),
            available_strategies=strategies,
            available_capabilities=capabilities,
            execution_mapping=await self._ontology_service.get_execution_mapping(
                target_asset.id, version=snapshot.version
            ),
            target_relation_types=relation_types,
            runtime_metric_asset_ids=frozenset(item.asset_id for binding in snapshot.runtime_bindings for item in binding.metric_bindings),
            runtime_dimension_asset_ids=frozenset(item.asset_id for binding in snapshot.runtime_bindings for item in binding.dimension_bindings),
        )
        return context.model_copy(update={
            "strategy_applicability": tuple(
                evaluate_strategy_applicability(strategy, context) for strategy in strategies
            )
        })

    async def _ground_scope(self, scope: SemanticScope, version: str) -> GroundedScope:
        dimension = await self._ground_target(scope.dimension, version)
        return GroundedScope(requested=scope, dimension=dimension)

    async def _ground_target(self, target: AnalysisTarget, version: str) -> AnalysisTarget:
        # A prior parser/interaction ambiguity is evidence.  Never silently
        # collapse it merely because a later snapshot lookup finds one alias.
        if target.resolution_status in {ResolutionStatus.RESOLVED, ResolutionStatus.AMBIGUOUS}:
            return target
        expected_kind = _ontology_kind(target.kind)
        candidates = await self._ontology_service.resolve_asset(
            target.raw_text, expected_kind=expected_kind, version=version
        )
        if len(candidates) == 1:
            return AnalysisTarget(
                raw_text=target.raw_text, kind=target.kind, asset_id=candidates[0].id,
                resolution_status=ResolutionStatus.RESOLVED,
            )
        if len(candidates) > 1:
            return AnalysisTarget(
                raw_text=target.raw_text, kind=target.kind,
                resolution_status=ResolutionStatus.AMBIGUOUS,
                candidate_asset_ids=tuple(asset.id for asset in candidates),
            )
        return target

    async def _target_asset(self, target: AnalysisTarget, version: str) -> OntologyAsset | None:
        if target.resolution_status is not ResolutionStatus.RESOLVED or target.asset_id is None:
            return None
        snapshot = await self._ontology_service.published_snapshot(version)
        if snapshot is None:
            return None
        return next((asset for asset in snapshot.assets if asset.id == target.asset_id), None)


def evaluate_strategy_applicability(
    strategy: OntologyAsset, context: PlanningContext
) -> StrategyApplicability:
    """Evaluate declarative constraints only; do not rank or select strategies."""

    definition = strategy.strategy_definition
    if definition is None or context.target_asset is None:
        return StrategyApplicability(
            strategy=strategy, status=StrategyApplicabilityStatus.NOT_APPLICABLE,
            reasons=("target is not resolved to a published asset",),
        )
    reasons: list[str] = []
    if context.intent not in definition.applicable_intents:
        reasons.append("analysis intent is not supported by strategy definition")
    if context.grounding.target.kind.value.lower() != definition.requires_target_kind.value:
        reasons.append("target kind does not satisfy strategy definition")
    if context.target_asset.kind is not definition.requires_target_kind:
        reasons.append("published target asset kind does not satisfy strategy definition")
    required_relations = set(definition.requires_relation)
    target_relations = set(context.target_relation_types)
    if not required_relations.issubset(target_relations):
        reasons.append("required ontology relation is absent")
    if definition.requires_any_relation and not (
        set(definition.requires_any_relation) & target_relations
    ):
        reasons.append("none of the alternative ontology relations is present")
    if definition.requires_time_dimension and not context.time_dimensions:
        reasons.append("target has no published time dimension")
    if reasons:
        return StrategyApplicability(
            strategy=strategy, status=StrategyApplicabilityStatus.NOT_APPLICABLE,
            reasons=tuple(reasons),
        )
    capability_ids = {capability.id for capability in context.available_capabilities}
    missing = tuple(
        capability_id for capability_id in definition.requires_capability
        if capability_id not in capability_ids
    )
    if missing:
        return StrategyApplicability(
            strategy=strategy, status=StrategyApplicabilityStatus.MISSING_CAPABILITY,
            reasons=tuple(f"missing capability: {capability_id}" for capability_id in missing),
        )
    return StrategyApplicability(strategy=strategy, status=StrategyApplicabilityStatus.AVAILABLE)


def _legacy_metric_target(query: SemanticQueryIR) -> AnalysisTarget:
    """Keep a context shape for legacy resolved query IR without changing it."""

    metric = query.metrics[0].metric
    return AnalysisTarget(
        raw_text=metric.asset_id, kind=AnalysisTargetKind.METRIC,
        asset_id=metric.asset_id, resolution_status=ResolutionStatus.RESOLVED,
    )


def _ontology_kind(kind: AnalysisTargetKind) -> OntologyAssetKind:
    return OntologyAssetKind(kind.value.lower())


def _is_time_dimension(dimension: OntologyAsset) -> bool:
    """Conservative temporary convention until time is structured Ontology metadata."""

    labels = (dimension.name, *dimension.aliases, dimension.id)
    return any(marker in label.casefold() for label in labels for marker in ("date", "time", "日期", "时间"))

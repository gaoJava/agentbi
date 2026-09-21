"""Phase 3 tests for published Ontology grounding and PlanningContext only."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from agentbi.ontology_service import (
    AnalysisStrategyDefinition,
    ExecutionMapping,
    InMemoryOntologyRepository,
    OntologyAsset,
    OntologyAssetKind,
    OntologyRelation,
    OntologyRelationType,
    OntologyService,
    OntologySnapshot,
    PlanningContextBuilder,
    PostgresOntologyRepository,
    PublicationState,
    StrategyApplicabilityStatus,
)
from agentbi.ontology_service.demo import verified_supersonic_sales_binding
from agentbi.semantic_query_ir import (
    AnalysisIntent,
    AnalysisSignal,
    AnalysisTarget,
    AnalysisTargetKind,
    RelativeTimeReference,
    ResolutionStatus,
    SemanticQueryIR,
    SemanticScope,
    SignalDirection,
    TimeScope,
    TimeScopeKind,
)

VERSION = "2026.30"


def asset(asset_id: str, kind: OntologyAssetKind, name: str, **kwargs: object) -> OntologyAsset:
    return OntologyAsset(
        id=asset_id, kind=kind, name=name, version=VERSION,
        state=PublicationState.PUBLISHED, **kwargs,
    )


def strategy(
    asset_id: str,
    definition: AnalysisStrategyDefinition,
) -> OntologyAsset:
    return asset(asset_id, OntologyAssetKind.ANALYSIS_STRATEGY, asset_id, strategy_definition=definition)


def published_snapshot() -> OntologySnapshot:
    return OntologySnapshot(
        version=VERSION,
        published_at=datetime(2026, 12, 1, tzinfo=UTC),
        assets=(
            asset("metric.revenue", OntologyAssetKind.METRIC, "销售额", aliases=("营收",)),
            asset("metric.customer_count", OntologyAssetKind.METRIC, "客户数"),
            asset("metric.order_count", OntologyAssetKind.METRIC, "订单数"),
            asset("metric.purchase_frequency", OntologyAssetKind.METRIC, "购买频次"),
            asset("metric.average_order_value", OntologyAssetKind.METRIC, "平均订单金额"),
            asset("dimension.region", OntologyAssetKind.DIMENSION, "区域"),
            asset("dimension.product", OntologyAssetKind.DIMENSION, "产品"),
            asset("dimension.customer", OntologyAssetKind.DIMENSION, "客户"),
            asset("dimension.channel", OntologyAssetKind.DIMENSION, "渠道"),
            asset("dimension.order_date", OntologyAssetKind.DIMENSION, "订单日期"),
            asset("datamodel.order_line", OntologyAssetKind.DATA_MODEL, "订单行模型"),
            asset("capability.metric_comparison", OntologyAssetKind.CAPABILITY, "指标对比"),
            asset("capability.dimension_contribution", OntologyAssetKind.CAPABILITY, "维度贡献"),
            asset("capability.time_series", OntologyAssetKind.CAPABILITY, "时间序列"),
            strategy("strategy.trend", AnalysisStrategyDefinition(
                applicable_intents=(AnalysisIntent.TREND, AnalysisIntent.ROOT_CAUSE),
                requires_target_kind=OntologyAssetKind.METRIC, requires_time_dimension=True,
                requires_capability=("capability.time_series",),
            )),
            strategy("strategy.yoy", AnalysisStrategyDefinition(
                applicable_intents=(AnalysisIntent.COMPARISON,),
                requires_target_kind=OntologyAssetKind.METRIC, requires_time_dimension=True,
                requires_capability=("capability.time_series",),
            )),
            strategy("strategy.breakdown", AnalysisStrategyDefinition(
                applicable_intents=(AnalysisIntent.BREAKDOWN,),
                requires_target_kind=OntologyAssetKind.METRIC,
                requires_any_relation=(OntologyRelationType.BREAKDOWN_BY,),
                requires_capability=("capability.dimension_contribution",),
            )),
            strategy("strategy.driver", AnalysisStrategyDefinition(
                applicable_intents=(AnalysisIntent.ROOT_CAUSE,),
                requires_target_kind=OntologyAssetKind.METRIC,
                requires_relation=(OntologyRelationType.DRIVEN_BY,),
                requires_capability=("capability.metric_comparison",),
            )),
            strategy("strategy.contribution", AnalysisStrategyDefinition(
                applicable_intents=(AnalysisIntent.ROOT_CAUSE,),
                requires_target_kind=OntologyAssetKind.METRIC,
                requires_any_relation=(OntologyRelationType.BREAKDOWN_BY,),
                requires_capability=("capability.dimension_contribution",),
            )),
            strategy("strategy.needs_formula", AnalysisStrategyDefinition(
                applicable_intents=(AnalysisIntent.ROOT_CAUSE,),
                requires_target_kind=OntologyAssetKind.METRIC,
                requires_relation=(OntologyRelationType.CALCULATED_BY,),
            )),
            strategy("strategy.missing_capability", AnalysisStrategyDefinition(
                applicable_intents=(AnalysisIntent.ROOT_CAUSE,),
                requires_target_kind=OntologyAssetKind.METRIC,
                requires_capability=("capability.not_published",),
            )),
        ),
        relations=(
            OntologyRelation(source_id="metric.revenue", relation="DRIVEN_BY",
                             target_id="metric.customer_count", version=VERSION),
            OntologyRelation(source_id="metric.revenue", relation="DRIVEN_BY",
                             target_id="metric.purchase_frequency", version=VERSION),
            OntologyRelation(source_id="metric.revenue", relation="DRIVEN_BY",
                             target_id="metric.average_order_value", version=VERSION),
            OntologyRelation(source_id="metric.revenue", relation="BREAKDOWN_BY",
                             target_id="dimension.region", version=VERSION),
            OntologyRelation(source_id="metric.revenue", relation="BREAKDOWN_BY",
                             target_id="dimension.product", version=VERSION),
            OntologyRelation(source_id="metric.revenue", relation="BREAKDOWN_BY",
                             target_id="dimension.customer", version=VERSION),
            OntologyRelation(source_id="metric.revenue", relation="BREAKDOWN_BY",
                             target_id="dimension.channel", version=VERSION),
            OntologyRelation(source_id="metric.revenue", relation="HAS_DIMENSION",
                             target_id="dimension.order_date", version=VERSION),
            OntologyRelation(source_id="metric.revenue", relation="MAPPED_TO",
                             target_id="datamodel.order_line", version=VERSION),
            # A legacy edge must be ignored safely rather than invalidate the context.
            OntologyRelation(source_id="metric.revenue", relation="RELATED_TO",
                             target_id="dimension.region", version=VERSION),
        ),
        runtime_bindings=(verified_supersonic_sales_binding().model_copy(update={"data_model_asset_id": "datamodel.order_line"}),),
    )


def query(target: AnalysisTarget | None = None) -> SemanticQueryIR:
    return SemanticQueryIR(
        ontology_version=VERSION,
        source_question="为什么最近华东地区销售额下降？",
        intent=AnalysisIntent.ROOT_CAUSE,
        targets=[target or AnalysisTarget(
            raw_text="销售额", kind=AnalysisTargetKind.METRIC,
            asset_id="metric.revenue", resolution_status=ResolutionStatus.RESOLVED,
        )],
        scopes=[SemanticScope(
            dimension=AnalysisTarget(raw_text="区域", kind=AnalysisTargetKind.DIMENSION),
            value="华东",
        )],
        time_scope=TimeScope(
            raw_text="最近", kind=TimeScopeKind.RELATIVE,
            relative_reference=RelativeTimeReference.RECENT,
        ),
        signal=AnalysisSignal(direction=SignalDirection.DECLINE, raw_text="下降"),
    )


def builder() -> tuple[OntologyService, PlanningContextBuilder]:
    service = OntologyService(InMemoryOntologyRepository((published_snapshot(),)))
    return service, PlanningContextBuilder(service)


def test_service_returns_direct_drivers_dimensions_capabilities_and_mapping() -> None:
    service, _ = builder()

    drivers = asyncio.run(service.get_metric_drivers("metric.revenue", version=VERSION))
    breakdowns = asyncio.run(service.get_breakdown_dimensions("metric.revenue", version=VERSION))
    dimensions = asyncio.run(service.get_dimensions("metric.revenue", version=VERSION))
    mapping = asyncio.run(service.get_execution_mapping("metric.revenue", version=VERSION))

    assert [item.id for item in drivers] == [
        "metric.customer_count", "metric.purchase_frequency", "metric.average_order_value",
    ]
    assert [item.id for item in breakdowns] == [
        "dimension.region", "dimension.product", "dimension.customer", "dimension.channel",
    ]
    assert [item.id for item in dimensions] == [
        "dimension.region", "dimension.product", "dimension.customer", "dimension.channel",
        "dimension.order_date",
    ]
    assert mapping == ExecutionMapping(
        asset_id="metric.revenue",
        mapped_assets=(asset("datamodel.order_line", OntologyAssetKind.DATA_MODEL, "订单行模型"),),
    )


def test_root_cause_context_contains_grounded_business_structure_and_candidates() -> None:
    _, context_builder = builder()
    context = asyncio.run(context_builder.build(query()))

    assert context.snapshot_version == VERSION
    assert context.time_scope is not None
    assert context.time_scope.relative_reference is RelativeTimeReference.RECENT
    assert context.signal is not None
    assert context.signal.direction is SignalDirection.DECLINE
    assert context.grounding.target.asset_id == "metric.revenue"
    assert context.grounding.scopes[0].dimension.asset_id == "dimension.region"
    assert context.grounding.scopes[0].value_resolution.value == "RAW"
    assert [item.id for item in context.drivers] == [
        "metric.customer_count", "metric.purchase_frequency", "metric.average_order_value",
    ]
    assert [item.id for item in context.breakdown_dimensions] == [
        "dimension.region", "dimension.product", "dimension.customer", "dimension.channel",
    ]
    assert [item.id for item in context.time_dimensions] == ["dimension.order_date"]
    assert context.execution_mapping is not None
    assert [item.id for item in context.execution_mapping.mapped_assets] == ["datamodel.order_line"]

    applicability = {item.strategy.id: item.status for item in context.strategy_applicability}
    assert applicability["strategy.driver"] is StrategyApplicabilityStatus.AVAILABLE
    assert applicability["strategy.contribution"] is StrategyApplicabilityStatus.AVAILABLE
    assert applicability["strategy.needs_formula"] is StrategyApplicabilityStatus.NOT_APPLICABLE
    assert applicability["strategy.missing_capability"] is StrategyApplicabilityStatus.MISSING_CAPABILITY


def test_unresolved_and_ambiguous_targets_remain_unselected() -> None:
    _, context_builder = builder()
    unresolved = asyncio.run(context_builder.build(query(AnalysisTarget(
        raw_text="毛利率", kind=AnalysisTargetKind.METRIC,
    ))))
    assert unresolved.target_asset is None
    assert unresolved.grounding.target.resolution_status is ResolutionStatus.UNRESOLVED
    assert not unresolved.drivers

    ambiguous = asyncio.run(context_builder.build(query(AnalysisTarget(
        raw_text="销售额", kind=AnalysisTargetKind.METRIC,
        resolution_status=ResolutionStatus.AMBIGUOUS,
        candidate_asset_ids=("metric.revenue", "metric.other_revenue"),
    ))))
    assert ambiguous.target_asset is None
    assert ambiguous.grounding.target.resolution_status is ResolutionStatus.AMBIGUOUS
    assert ambiguous.grounding.target.candidate_asset_ids == (
        "metric.revenue", "metric.other_revenue",
    )


def test_unpublished_change_set_is_not_visible_to_planning_context() -> None:
    repository = PostgresOntologyRepository("sqlite:///:memory:")
    repository.initialize()
    draft_asset = OntologyAsset(
        id="metric.draft_only", kind=OntologyAssetKind.METRIC, name="草稿指标",
        version="2026.31", state=PublicationState.DRAFT,
    )
    asyncio.run(repository.create_change_set(
        target_version="2026.31", title="未发布策略", created_by="owner",
        assets=(draft_asset,), relations=(),
    ))

    context = asyncio.run(PlanningContextBuilder(OntologyService(repository)).build(
        SemanticQueryIR(
            ontology_version="2026.31", source_question="草稿指标是多少？",
            targets=[AnalysisTarget(raw_text="草稿指标", kind=AnalysisTargetKind.METRIC)],
        )
    ))

    assert context.snapshot_version is None
    assert context.target_asset is None
    assert not context.available_strategies
    assert not context.available_capabilities

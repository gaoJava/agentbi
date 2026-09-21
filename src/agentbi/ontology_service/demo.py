"""The verified sales binding used only as a governed ChangeSet payload."""

from agentbi.semantic_query_ir import AggregateFunction, AnalysisIntent, TimeGrain

from .contracts import (
    AnalysisStrategyDefinition,
    OntologyAsset,
    OntologyAssetKind,
    OntologyRelation,
    OntologyRelationType,
    PublicationState,
    RuntimeAssetBinding,
    RuntimeBackend,
    RuntimeBinding,
    RuntimeTimeGrainBinding,
)


def verified_supersonic_sales_binding() -> RuntimeBinding:
    """Return the Phase 6.3A-verified binding; callers must still publish it."""

    return RuntimeBinding(
        id="runtime.supersonic.sales.v1",
        backend=RuntimeBackend.SUPERSONIC,
        connection_id="supersonic-primary",
        data_model_asset_id="datamodel.sales_order_line",
        semantic_model_id=13,
        semantic_view_id=8,
        metric_bindings=(
            RuntimeAssetBinding(asset_id="metric.revenue", semantic_identifier="net_amount", aggregation=AggregateFunction.SUM),
            RuntimeAssetBinding(asset_id="metric.customer_count", semantic_identifier="customer_count"),
            RuntimeAssetBinding(asset_id="metric.order_count", semantic_identifier="order_count"),
            RuntimeAssetBinding(asset_id="metric.purchase_frequency", semantic_identifier="purchase_frequency"),
            RuntimeAssetBinding(asset_id="metric.average_order_value", semantic_identifier="average_order_value"),
        ),
        time_grain_bindings=(
            RuntimeTimeGrainBinding(dimension_asset_id="dimension.order_date", grain=TimeGrain.DAY, semantic_identifier="sys_imp_date"),
            RuntimeTimeGrainBinding(dimension_asset_id="dimension.order_date", grain=TimeGrain.MONTH, semantic_identifier="sys_imp_month"),
        ),
        dimension_bindings=(
            RuntimeAssetBinding(asset_id="dimension.region", semantic_identifier="customer_region"),
            RuntimeAssetBinding(asset_id="dimension.product", semantic_identifier="product_name"),
            RuntimeAssetBinding(asset_id="dimension.customer", semantic_identifier="customer_name"),
            RuntimeAssetBinding(asset_id="dimension.order_date", semantic_identifier="order_date"),
        ),
        supported_time_grains=("DAY", "MONTH"),
        supports_date_filters=True,
    )


def verified_supersonic_sales_ontology(version: str) -> tuple[tuple[OntologyAsset, ...], tuple[OntologyRelation, ...]]:
    """Published sales vocabulary and relations for the verified binding.

    This returns a ChangeSet payload only; callers must still review and publish it.
    """
    def asset(asset_id: str, kind: OntologyAssetKind, name: str, aliases: tuple[str, ...] = (), **kwargs) -> OntologyAsset:
        return OntologyAsset(id=asset_id, kind=kind, name=name, aliases=aliases,
                             version=version, state=PublicationState.DRAFT, **kwargs)
    capability = (
        asset("capability.metric_comparison", OntologyAssetKind.CAPABILITY, "指标对比"),
        asset("capability.dimension_contribution", OntologyAssetKind.CAPABILITY, "维度贡献"),
        asset("capability.time_series", OntologyAssetKind.CAPABILITY, "时间序列"),
    )
    strategies = (
        asset("strategy.trend", OntologyAssetKind.ANALYSIS_STRATEGY, "趋势分析", strategy_definition=AnalysisStrategyDefinition(applicable_intents=(AnalysisIntent.TREND,), requires_target_kind=OntologyAssetKind.METRIC, requires_time_dimension=True, requires_capability=("capability.time_series",))),
        asset("strategy.comparison", OntologyAssetKind.ANALYSIS_STRATEGY, "指标比较", strategy_definition=AnalysisStrategyDefinition(applicable_intents=(AnalysisIntent.COMPARISON,), requires_target_kind=OntologyAssetKind.METRIC, requires_time_dimension=True, requires_capability=("capability.metric_comparison",))),
        asset("strategy.breakdown", OntologyAssetKind.ANALYSIS_STRATEGY, "维度拆分", strategy_definition=AnalysisStrategyDefinition(applicable_intents=(AnalysisIntent.BREAKDOWN,), requires_target_kind=OntologyAssetKind.METRIC, requires_any_relation=(OntologyRelationType.BREAKDOWN_BY,), requires_capability=("capability.dimension_contribution",))),
        asset("strategy.root_cause_baseline", OntologyAssetKind.ANALYSIS_STRATEGY, "根因基线", strategy_definition=AnalysisStrategyDefinition(applicable_intents=(AnalysisIntent.ROOT_CAUSE,), requires_target_kind=OntologyAssetKind.METRIC, requires_time_dimension=True, requires_capability=("capability.time_series",))),
        asset("strategy.contribution", OntologyAssetKind.ANALYSIS_STRATEGY, "贡献分析", strategy_definition=AnalysisStrategyDefinition(applicable_intents=(AnalysisIntent.ROOT_CAUSE,), requires_target_kind=OntologyAssetKind.METRIC, requires_any_relation=(OntologyRelationType.BREAKDOWN_BY,), requires_capability=("capability.dimension_contribution",))),
        asset("strategy.driver", OntologyAssetKind.ANALYSIS_STRATEGY, "驱动指标", strategy_definition=AnalysisStrategyDefinition(applicable_intents=(AnalysisIntent.ROOT_CAUSE,), requires_target_kind=OntologyAssetKind.METRIC, requires_relation=(OntologyRelationType.DRIVEN_BY,), requires_capability=("capability.metric_comparison",))),
    )
    assets = (
        asset("datamodel.sales_order_line", OntologyAssetKind.DATA_MODEL, "销售订单行"),
        asset("metric.revenue", OntologyAssetKind.METRIC, "收入", ("营收", "销售额", "Revenue")),
        asset("metric.customer_count", OntologyAssetKind.METRIC, "客户数", ("CustomerCount",)),
        asset("metric.order_count", OntologyAssetKind.METRIC, "订单数", ("OrderCount",)),
        asset("metric.purchase_frequency", OntologyAssetKind.METRIC, "购买频次", ("PurchaseFrequency",)),
        asset("metric.average_order_value", OntologyAssetKind.METRIC, "客单价", ("平均订单金额", "AverageOrderValue")),
        asset("dimension.region", OntologyAssetKind.DIMENSION, "区域", ("地区", "Region")),
        asset("dimension.product", OntologyAssetKind.DIMENSION, "产品", ("商品", "Product")),
        asset("dimension.customer", OntologyAssetKind.DIMENSION, "客户", ("顾客", "Customer")),
        asset("dimension.order_date", OntologyAssetKind.DIMENSION, "订单日期", ("日期", "下单日期", "OrderDate")),
        asset("dimension.channel", OntologyAssetKind.DIMENSION, "渠道", ("销售渠道", "Channel")),
        *capability, *strategies,
    )
    relation = lambda source, kind, target: OntologyRelation(source_id=source, relation=kind, target_id=target, version=version)
    relations = (
        *(relation("metric.revenue", OntologyRelationType.DRIVEN_BY, target) for target in ("metric.customer_count", "metric.purchase_frequency", "metric.average_order_value")),
        *(relation("metric.revenue", OntologyRelationType.BREAKDOWN_BY, target) for target in ("dimension.region", "dimension.product", "dimension.customer", "dimension.channel")),
        *(relation(metric, OntologyRelationType.HAS_DIMENSION, dimension) for metric in (
            "metric.revenue", "metric.customer_count", "metric.order_count",
            "metric.purchase_frequency", "metric.average_order_value",
        ) for dimension in (
            "dimension.region", "dimension.product", "dimension.customer", "dimension.order_date",
        )),
        *(relation(metric, OntologyRelationType.MAPPED_TO, "datamodel.sales_order_line") for metric in (
            "metric.revenue", "metric.customer_count", "metric.order_count",
            "metric.purchase_frequency", "metric.average_order_value",
        )),
    )
    return assets, relations

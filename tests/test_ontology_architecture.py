"""Contract tests for the not-yet-wired ontology architecture skeleton."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agentbi.ontology_service import (
    AnalysisIntent,
    AnalysisStrategyDefinition,
    InMemoryOntologyRepository,
    OntologyAsset,
    OntologyAssetKind,
    OntologyRelation,
    OntologyRelationType,
    OntologyService,
    OntologySnapshot,
    PostgresOntologyRepository,
    PublicationState,
)
from agentbi.query_planner import NoopQueryPlanner
from agentbi.semantic_parser import NoopSemanticParser, ParseRequest
from agentbi.semantic_query_ir import MetricRequest, OntologyReference, SemanticQueryIR


def test_noop_parser_is_non_executable() -> None:
    result = asyncio.run(NoopSemanticParser().parse(ParseRequest(
        question="销售额是多少", ontology_version="2026.09"
    )))

    assert result.query is None
    assert result.clarification is not None


def test_noop_planner_refuses_to_emit_execution_plan() -> None:
    query = SemanticQueryIR(
        ontology_version="2026.09",
        source_question="销售额是多少",
        metrics=[
            MetricRequest(metric=OntologyReference(asset_id="metric.sales", version="2026.09"))
        ],
    )

    result = asyncio.run(NoopQueryPlanner().plan(query, actor_id="user-1"))

    assert result.plan is None
    assert result.rejection_reason == "query planner is not enabled"


def test_published_snapshot_resolves_aliases_and_traverses_business_relations() -> None:
    version = "2026.10"
    snapshot = OntologySnapshot(
        version=version,
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
        assets=(
            OntologyAsset(
                id="entity.order",
                kind=OntologyAssetKind.ENTITY,
                name="订单",
                aliases=("销售订单",),
                version=version,
                state=PublicationState.PUBLISHED,
            ),
            OntologyAsset(id="metric.order.revenue", kind=OntologyAssetKind.METRIC,
                          name="订单收入", aliases=("销售额",), version=version,
                          state=PublicationState.PUBLISHED),
            OntologyAsset(id="dimension.customer.region", kind=OntologyAssetKind.DIMENSION,
                          name="客户区域", aliases=("区域",), version=version,
                          state=PublicationState.PUBLISHED),
        ),
        relations=(
            OntologyRelation(source_id="metric.order.revenue", relation="MEASURE_OF",
                             target_id="entity.order", version=version),
            OntologyRelation(source_id="entity.order", relation="RELATED_TO",
                             target_id="dimension.customer.region", version=version),
        ),
    )
    service = OntologyService(InMemoryOntologyRepository((snapshot,)))

    metrics = asyncio.run(service.resolve_alias(" 销售额 ", kind=OntologyAssetKind.METRIC))
    related = asyncio.run(service.related_assets("metric.order.revenue", max_hops=2))

    assert [asset.id for asset in metrics] == ["metric.order.revenue"]
    assert [asset.id for asset in related] == ["entity.order", "dimension.customer.region"]


def test_snapshot_rejects_cross_version_or_unpublished_graph_members() -> None:
    version = "2026.10"
    draft_asset = OntologyAsset(id="entity.order", kind=OntologyAssetKind.ENTITY, name="订单",
                                version=version, state=PublicationState.DRAFT)
    snapshot = OntologySnapshot(version=version, published_at=datetime(2026, 10, 1, tzinfo=UTC),
                                assets=(draft_asset,))

    with pytest.raises(ValueError, match="only published assets"):
        InMemoryOntologyRepository((snapshot,))


def test_postgres_repository_persists_an_immutable_versioned_snapshot() -> None:
    version = "2026.10"
    snapshot = OntologySnapshot(
        version=version,
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
        assets=(
            OntologyAsset(id="entity.customer", kind=OntologyAssetKind.ENTITY,
                          name="客户", aliases=("顾客",), version=version,
                          state=PublicationState.PUBLISHED),
            OntologyAsset(id="metric.customer.revenue", kind=OntologyAssetKind.METRIC,
                          name="客户收入", aliases=("客户销售额",), version=version,
                          state=PublicationState.PUBLISHED),
        ),
        relations=(
            # Existing releases may contain relations outside the v0.2 vocabulary.
            OntologyRelation(source_id="entity.customer", relation="RELATED_TO",
                             target_id="metric.customer.revenue", version=version),
            OntologyRelation(source_id="metric.customer.revenue", relation="MEASURE_OF",
                             target_id="entity.customer", version=version),
        ),
    )
    repository = PostgresOntologyRepository("sqlite:///:memory:")
    repository.initialize()
    asyncio.run(repository.publish(snapshot, published_by="admin"))

    restored = asyncio.run(repository.get_published_snapshot())
    resolved = asyncio.run(repository.find_assets_by_alias(" 顾客 "))

    assert restored == snapshot
    assert [asset.id for asset in resolved] == ["entity.customer"]
    with pytest.raises(ValueError, match="already published"):
        asyncio.run(repository.publish(snapshot))


def test_change_set_requires_independent_review_before_atomic_publication() -> None:
    version = "2026.11"
    assets = (
        OntologyAsset(id="entity.order", kind=OntologyAssetKind.ENTITY, name="订单",
                      version=version, state=PublicationState.DRAFT),
        OntologyAsset(id="metric.order.revenue", kind=OntologyAssetKind.METRIC,
                      name="订单收入", version=version, state=PublicationState.DRAFT),
    )
    relations = (OntologyRelation(source_id="metric.order.revenue", relation="MEASURE_OF",
                                  target_id="entity.order", version=version),)
    repository = PostgresOntologyRepository("sqlite:///:memory:")
    repository.initialize()

    draft = asyncio.run(repository.create_change_set(
        target_version=version, title="销售域初版", created_by="data-owner",
        assets=assets, relations=relations,
    ))
    reviewed = asyncio.run(repository.submit_for_review(draft.id, actor="data-owner"))
    assert reviewed.state.value == "in_review"
    with pytest.raises(ValueError, match="cannot approve"):
        asyncio.run(repository.record_review(draft.id, reviewer="data-owner", approved=True))
    approved = asyncio.run(repository.record_review(
        draft.id, reviewer="business-owner", approved=True, comment="口径已确认"
    ))
    assert approved.state.value == "approved"

    published = asyncio.run(repository.publish_change_set(
        draft.id, publisher="release-manager", published_at=datetime(2026, 11, 1, tzinfo=UTC)
    ))

    assert all(asset.state is PublicationState.PUBLISHED for asset in published.assets)
    assert asyncio.run(repository.get_published_snapshot(version)) == published


def test_v02_contracts_keep_legacy_relations_and_normalize_controlled_vocabulary() -> None:
    version = "2026.12"
    controlled = OntologyRelation(
        source_id="metric.revenue", relation="DRIVEN_BY",
        target_id="metric.customer_count", version=version,
    )
    legacy = OntologyRelation(
        source_id="entity.order", relation="PLACED_BY",
        target_id="entity.customer", version=version,
    )

    assert controlled.relation is OntologyRelationType.DRIVEN_BY
    assert legacy.relation == "PLACED_BY"
    assert legacy.model_dump(mode="json")["relation"] == "PLACED_BY"


def test_analysis_strategy_definition_serializes_as_a_small_controlled_contract() -> None:
    definition = AnalysisStrategyDefinition(
        applicable_intents=(AnalysisIntent.ROOT_CAUSE,),
        requires_target_kind=OntologyAssetKind.METRIC,
        requires_relation=(OntologyRelationType.DRIVEN_BY,),
        requires_capability=("capability.metric_comparison",),
    )
    asset = OntologyAsset(
        id="strategy.driver_analysis",
        kind=OntologyAssetKind.ANALYSIS_STRATEGY,
        name="驱动分析",
        version="2026.12",
        state=PublicationState.DRAFT,
        strategy_definition=definition,
    )

    restored = OntologyAsset.model_validate(asset.model_dump(mode="json"))

    assert restored == asset
    assert restored.strategy_definition is not None
    assert restored.strategy_definition.requires_relation == (OntologyRelationType.DRIVEN_BY,)
    with pytest.raises(ValueError, match="require a strategy_definition"):
        OntologyAsset(
            id="strategy.invalid", kind=OntologyAssetKind.ANALYSIS_STRATEGY,
            name="缺少定义的策略", version="2026.12", state=PublicationState.DRAFT,
        )


def test_v02_assets_relations_and_definitions_survive_changeset_review_publish() -> None:
    version = "2026.12"
    driver_definition = AnalysisStrategyDefinition(
        applicable_intents=(AnalysisIntent.ROOT_CAUSE,),
        requires_target_kind=OntologyAssetKind.METRIC,
        requires_relation=(OntologyRelationType.DRIVEN_BY,),
        requires_capability=("capability.metric_comparison",),
    )
    assets = (
        OntologyAsset(id="metric.revenue", kind=OntologyAssetKind.METRIC, name="销售额",
                      version=version, state=PublicationState.DRAFT),
        OntologyAsset(id="metric.customer_count", kind=OntologyAssetKind.METRIC, name="客户数",
                      version=version, state=PublicationState.DRAFT),
        OntologyAsset(id="capability.metric_comparison", kind=OntologyAssetKind.CAPABILITY,
                      name="指标对比", version=version, state=PublicationState.DRAFT),
        OntologyAsset(id="strategy.driver_analysis", kind=OntologyAssetKind.ANALYSIS_STRATEGY,
                      name="驱动分析", version=version, state=PublicationState.DRAFT,
                      strategy_definition=driver_definition),
    )
    relations = (
        OntologyRelation(source_id="metric.revenue", relation=OntologyRelationType.DRIVEN_BY,
                         target_id="metric.customer_count", version=version),
        OntologyRelation(source_id="strategy.driver_analysis", relation=OntologyRelationType.REQUIRES,
                         target_id="capability.metric_comparison", version=version),
    )
    repository = PostgresOntologyRepository("sqlite:///:memory:")
    repository.initialize()

    draft = asyncio.run(repository.create_change_set(
        target_version=version, title="策略与能力词表", created_by="data-owner",
        assets=assets, relations=relations,
    ))
    draft_strategy = next(asset for asset in draft.assets if asset.id == "strategy.driver_analysis")
    assert draft_strategy.strategy_definition == driver_definition
    asyncio.run(repository.submit_for_review(draft.id, actor="data-owner"))
    asyncio.run(repository.record_review(draft.id, reviewer="business-owner", approved=True))
    published = asyncio.run(repository.publish_change_set(
        draft.id, publisher="release-manager", published_at=datetime(2026, 12, 1, tzinfo=UTC)
    ))
    restored = asyncio.run(repository.get_published_snapshot(version))

    assert restored == published
    assert {asset.kind for asset in restored.assets} >= {
        OntologyAssetKind.ANALYSIS_STRATEGY, OntologyAssetKind.CAPABILITY,
    }
    assert {relation.relation for relation in restored.relations} == {
        OntologyRelationType.DRIVEN_BY, OntologyRelationType.REQUIRES,
    }
    strategy = next(asset for asset in restored.assets if asset.id == "strategy.driver_analysis")
    assert strategy.strategy_definition == driver_definition

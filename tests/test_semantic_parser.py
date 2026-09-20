"""Phase 2 tests for parsing business questions into execution-independent IR."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agentbi.ontology_service import (
    InMemoryOntologyRepository,
    OntologyAsset,
    OntologyAssetKind,
    OntologyService,
    OntologySnapshot,
    PublicationState,
)
from agentbi.semantic_parser import ParseRequest, RuleBasedSemanticParser
from agentbi.semantic_query_ir import (
    AnalysisIntent,
    ComparisonType,
    RelativeTimeReference,
    ResolutionStatus,
    SignalDirection,
    TimeScopeKind,
)

VERSION = "2026.20"


@pytest.fixture
def parser() -> RuleBasedSemanticParser:
    snapshot = OntologySnapshot(
        version=VERSION,
        published_at=datetime(2026, 12, 1, tzinfo=UTC),
        assets=(
            OntologyAsset(id="metric.revenue", kind=OntologyAssetKind.METRIC, name="销售额",
            aliases=("营收", "收入", "销售额", "Revenue"), version=VERSION, state=PublicationState.PUBLISHED),
            OntologyAsset(id="metric.profit", kind=OntologyAssetKind.METRIC, name="利润",
                          version=VERSION, state=PublicationState.PUBLISHED),
            OntologyAsset(id="dimension.region", kind=OntologyAssetKind.DIMENSION, name="客户区域",
                          aliases=("区域", "地区"), version=VERSION, state=PublicationState.PUBLISHED),
            OntologyAsset(id="dimension.channel", kind=OntologyAssetKind.DIMENSION, name="销售渠道",
                          aliases=("渠道",), version=VERSION, state=PublicationState.PUBLISHED),
            OntologyAsset(id="dimension.product", kind=OntologyAssetKind.DIMENSION, name="产品",
                          aliases=("商品",), version=VERSION, state=PublicationState.PUBLISHED),
        ),
    )
    return RuleBasedSemanticParser(OntologyService(InMemoryOntologyRepository((snapshot,))))


def parse(parser: RuleBasedSemanticParser, question: str):
    result = asyncio.run(parser.parse(ParseRequest(question=question, ontology_version=VERSION)))
    assert result.query is not None
    return result.query


def test_parses_query_metric(parser: RuleBasedSemanticParser) -> None:
    query = parse(parser, "销售额是多少？")

    assert query.intent is AnalysisIntent.QUERY
    assert query.targets[0].asset_id == "metric.revenue"
    assert query.targets[0].resolution_status is ResolutionStatus.RESOLVED


def test_parses_trend_and_relative_time(parser: RuleBasedSemanticParser) -> None:
    query = parse(parser, "最近30天销售额趋势怎么样？")

    assert query.intent is AnalysisIntent.TREND
    assert query.targets[0].asset_id == "metric.revenue"
    assert query.time_scope is not None
    assert query.time_scope.kind is TimeScopeKind.RELATIVE
    assert query.time_scope.relative_reference is RelativeTimeReference.LAST_N_DAYS
    assert query.time_scope.amount == 30


def test_parses_explicit_yoy_comparison(parser: RuleBasedSemanticParser) -> None:
    query = parse(parser, "今年销售额和去年相比怎么样？")

    assert query.intent is AnalysisIntent.COMPARISON
    assert query.comparison is not None
    assert query.comparison.type is ComparisonType.YOY
    assert query.time_scope is not None
    assert query.time_scope.kind is TimeScopeKind.EXPLICIT


def test_parses_root_cause_scope_relative_time_and_decline(parser: RuleBasedSemanticParser) -> None:
    query = parse(parser, "为什么最近华东地区销售额下降？")

    assert query.intent is AnalysisIntent.ROOT_CAUSE
    assert query.targets[0].asset_id == "metric.revenue"
    assert query.scopes[0].dimension.asset_id == "dimension.region"
    assert query.scopes[0].value == "华东"
    assert query.time_scope is not None
    assert query.time_scope.relative_reference is RelativeTimeReference.RECENT
    assert query.signal is not None
    assert query.signal.direction is SignalDirection.DECLINE
    assert query.comparison is None


def test_parses_breakdown_dimension_hint(parser: RuleBasedSemanticParser) -> None:
    query = parse(parser, "各渠道销售额是多少？")

    assert query.intent is AnalysisIntent.BREAKDOWN
    assert query.targets[0].asset_id == "metric.revenue"
    assert [item.asset_id for item in query.breakdown_hints] == ["dimension.channel"]


def test_parses_product_revenue_top_one_as_ranked_breakdown(parser: RuleBasedSemanticParser) -> None:
    query = parse(parser, "2025年1月哪个产品收入最高？")

    assert query.intent is AnalysisIntent.BREAKDOWN
    assert query.targets[0].asset_id == "metric.revenue"
    assert [item.asset_id for item in query.breakdown_hints] == ["dimension.product"]
    assert query.limit == 1
    assert [(item.target_alias, item.direction.value) for item in query.order_by] == [("metric.revenue", "DESC")]


def test_parses_root_cause_profit_change(parser: RuleBasedSemanticParser) -> None:
    result = asyncio.run(parser.parse(ParseRequest(question="为什么利润变化这么大？", ontology_version=VERSION)))

    assert result.query is None
    assert result.clarification is not None
    assert "时间" in result.clarification.reason


def test_root_cause_remains_primary_intent_when_breakdown_is_requested(parser: RuleBasedSemanticParser) -> None:
    result = asyncio.run(parser.parse(ParseRequest(question="销售额为什么下降？按渠道看看。", ontology_version=VERSION)))

    assert result.query is None
    assert result.clarification is not None
    assert "时间" in result.clarification.reason


def test_unresolved_and_ambiguous_targets_are_not_guessed() -> None:
    unresolved = asyncio.run(RuleBasedSemanticParser().parse(
        ParseRequest(question="销售额是多少？", ontology_version=VERSION)
    ))
    assert unresolved.query is not None
    assert unresolved.query.targets[0].resolution_status is ResolutionStatus.UNRESOLVED

    snapshot = OntologySnapshot(
        version=VERSION,
        published_at=datetime(2026, 12, 1, tzinfo=UTC),
        assets=(
            OntologyAsset(id="metric.revenue.gross", kind=OntologyAssetKind.METRIC, name="销售额",
                          version=VERSION, state=PublicationState.PUBLISHED),
            OntologyAsset(id="metric.revenue.net", kind=OntologyAssetKind.METRIC, name="销售额",
                          version=VERSION, state=PublicationState.PUBLISHED),
        ),
    )
    ambiguous = asyncio.run(RuleBasedSemanticParser(
        OntologyService(InMemoryOntologyRepository((snapshot,)))
    ).parse(ParseRequest(question="销售额是多少？", ontology_version=VERSION)))
    assert ambiguous.query is not None
    target = ambiguous.query.targets[0]
    assert target.resolution_status is ResolutionStatus.AMBIGUOUS
    assert target.candidate_asset_ids == ("metric.revenue.gross", "metric.revenue.net")

"""Phase 5.1 contract and semantic-boundary tests."""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentbi.dashboard_context import DashboardContext, DashboardContextProvenance, DashboardFilter
from agentbi.ontology_service import (
    InMemoryOntologyRepository,
    OntologyAsset,
    OntologyAssetKind,
    OntologyService,
    OntologySnapshot,
    PublicationState,
)
from agentbi.semantic_parser import ParseRequest, RuleBasedSemanticParser
from agentbi.semantic_query_ir import ValueSource

VERSION = "2026.51"
_ADAPTER_PATH = Path(__file__).parents[1] / "integrations" / "superset-extension" / "backend" / "src" / "agentbi" / "insight_pilot" / "validation.py"
_SPEC = importlib.util.spec_from_file_location("phase5_superset_validation", _ADAPTER_PATH)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
SupersetContextAdapter = _MODULE.SupersetContextAdapter


@pytest.fixture
def parser() -> RuleBasedSemanticParser:
    snapshot = OntologySnapshot(
        version=VERSION,
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        assets=(
            OntologyAsset(id="metric.revenue", kind=OntologyAssetKind.METRIC, name="销售额", aliases=("收入",), version=VERSION, state=PublicationState.PUBLISHED),
            OntologyAsset(id="metric.profit", kind=OntologyAssetKind.METRIC, name="利润", version=VERSION, state=PublicationState.PUBLISHED),
            OntologyAsset(id="dimension.region", kind=OntologyAssetKind.DIMENSION, name="区域", aliases=("region",), version=VERSION, state=PublicationState.PUBLISHED),
        ),
    )
    return RuleBasedSemanticParser(OntologyService(InMemoryOntologyRepository((snapshot,))))


def dashboard_context(**overrides) -> DashboardContext:
    values = {
        "dashboard_id": "sales-overview",
        "time_range": "最近7天",
        "filters": (DashboardFilter(field="region", value="华东"),),
        "focused_metric": "销售额",
        "selected_chart_id": "42",
        "selected_dataset_id": "21",
        "provenance": DashboardContextProvenance(provider="superset_extension", verified_fields=("dashboard_id",)),
    }
    values.update(overrides)
    return DashboardContext(**values)


def test_core_dashboard_contract_is_vendor_neutral_and_bounded() -> None:
    source = (Path(__file__).parents[1] / "src" / "agentbi" / "dashboard_context.py").read_text(encoding="utf-8").casefold()
    assert "superset" not in source
    with pytest.raises(ValidationError):
        DashboardContext.model_validate({"dashboard_id": "d", "provenance": {"provider": "host"}, "role": "admin"})


def test_superset_adapter_maps_only_allowlisted_dashboard_state() -> None:
    result = SupersetContextAdapter().adapt({
        "dashboard_id": "sales-overview", "chart_id": "42", "dataset_id": "21",
        "semantic_model_id": 1, "time_range": "最近7天", "focused_metric": "revenue",
        "filters": [{"field": "区域", "operator": "EQ", "value": "华东"}],
    })
    context = DashboardContext.model_validate(result)
    assert context.focused_metric == "销售额"
    assert context.filters[0].field == "region"
    assert context.provenance.provider == "superset_extension"
    with pytest.raises(ValueError, match="not supported"):
        SupersetContextAdapter().adapt({
            "dashboard_id": "sales-overview", "semantic_model_id": 1, "time_range": "最近7天",
            "filters": [{"field": "sql", "operator": "EQ", "value": "x"}],
        })


def test_explicit_user_question_overrides_dashboard_metric_filter_and_time(parser: RuleBasedSemanticParser) -> None:
    result = asyncio.run(parser.parse(ParseRequest(
        question="华北2025年1月利润是多少？", ontology_version=VERSION,
        dashboard_context=dashboard_context(),
    )))
    assert result.query is not None
    assert result.query.targets[0].asset_id == "metric.profit"
    assert result.query.scopes[0].value == "华北"
    assert result.query.time_scope is not None and result.query.time_scope.raw_text == "2025年1月"
    assert result.query.targets[0].source is ValueSource.USER_EXPLICIT


def test_dashboard_context_supplements_only_omitted_semantics(parser: RuleBasedSemanticParser) -> None:
    result = asyncio.run(parser.parse(ParseRequest(
        question="是多少？", ontology_version=VERSION, dashboard_context=dashboard_context(),
    )))
    assert result.query is not None
    assert result.query.targets[0].asset_id == "metric.revenue"
    assert result.query.targets[0].source is ValueSource.DASHBOARD_CONTEXT
    assert result.query.scopes[0].value == "华东"
    assert result.query.scopes[0].source is ValueSource.DASHBOARD_CONTEXT
    assert result.query.time_scope is not None
    assert result.query.time_scope.source is ValueSource.DASHBOARD_CONTEXT


def test_missing_or_invalid_context_does_not_silently_supply_semantics(parser: RuleBasedSemanticParser) -> None:
    missing = asyncio.run(parser.parse(ParseRequest(question="是多少？", ontology_version=VERSION)))
    assert missing.query is None and missing.clarification is not None
    with pytest.raises(ValidationError):
        DashboardContext.model_validate({"dashboard_id": "d", "provenance": {"provider": "host"}, "filters": [{"field": "x", "value": {"nested": "no"}}]})

"""Phase 6.3B structured execution tests; no backend identifiers enter the planner."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from agentbi.execution import ExecutionRuntime, ExecutionStatus, StructuredSemanticRequestBuilder
from agentbi.ontology_service import (
    OntologyAsset,
    OntologyAssetKind,
    OntologyService,
    PostgresOntologyRepository,
    PublicationState,
    RuntimeBindingResolver,
    verified_supersonic_sales_binding,
)
from agentbi.query_planner import ExecutionPlan, ExecutionTarget, StepExecutionPlan
from agentbi.semantic_query_ir import (
    DimensionRequest,
    MetricRequest,
    OntologyReference,
    PredicateOperator,
    SemanticOrdering,
    SemanticPredicate,
    SemanticQueryIR,
    SortDirection,
    TimeGrain,
)

VERSION = "2026.3b.test"


def _ref(asset_id: str) -> OntologyReference:
    return OntologyReference(asset_id=asset_id, version=VERSION)


def _assets():
    values = [
        ("datamodel.sales_order_line", OntologyAssetKind.DATA_MODEL),
        *( (f"metric.{name}", OntologyAssetKind.METRIC) for name in ("revenue", "customer_count", "order_count", "purchase_frequency", "average_order_value") ),
        *( (f"dimension.{name}", OntologyAssetKind.DIMENSION) for name in ("region", "product", "customer", "order_date", "channel") ),
    ]
    return tuple(OntologyAsset(id=asset_id, kind=kind, name=asset_id, version=VERSION,
                               state=PublicationState.DRAFT) for asset_id, kind in values)


async def _repository(published: bool = True) -> PostgresOntologyRepository:
    repository = PostgresOntologyRepository("sqlite:///:memory:"); repository.initialize()
    proposal = await repository.create_change_set(target_version=VERSION, title="binding", created_by="owner", assets=_assets(), relations=(), runtime_bindings=(verified_supersonic_sales_binding(),))
    if published:
        await repository.submit_for_review(proposal.id, actor="owner")
        await repository.record_review(proposal.id, reviewer="reviewer", approved=True)
        await repository.publish_change_set(proposal.id, publisher="publisher", published_at=datetime(2026, 9, 16, tzinfo=UTC))
    return repository


def _plan(metric: str, *, dimensions=(), predicates=()) -> ExecutionPlan:
    return ExecutionPlan(target=ExecutionTarget.SUPERSONIC, ontology_version=VERSION,
                         analysis_plan_id="plan", analysis_step_id=metric,
                         query=SemanticQueryIR(ontology_version=VERSION, source_question=metric,
                                               metrics=[MetricRequest(metric=_ref(metric))],
                                               dimensions=list(dimensions), predicates=list(predicates)))


class StructuredAdapter:
    def __init__(self): self.requests = []
    async def execute_structured_request(self, request):
        self.requests.append(request)
        return {"queryResults": [{"value": 1}], "querySql": "SELECT governed" , "structuredSemanticRequest": request.model_dump(mode="json")}
    async def execute_execution_plan(self, *_args, **_kwargs):
        return {"queryResults": [{"legacy": 1}], "querySql": "SELECT legacy"}


def test_builder_preserves_metric_dimension_date_and_month_semantics() -> None:
    repo = asyncio.run(_repository()); builder = StructuredSemanticRequestBuilder(RuntimeBindingResolver(OntologyService(repo)))
    date = (SemanticPredicate(dimension=_ref("dimension.order_date"), operator=PredicateOperator.GTE, value="2025-01-01"), SemanticPredicate(dimension=_ref("dimension.order_date"), operator=PredicateOperator.LT, value="2025-02-01"))
    request = asyncio.run(builder.build(_plan("metric.revenue", dimensions=(DimensionRequest(dimension=_ref("dimension.region")),), predicates=date)))
    month = asyncio.run(builder.build(_plan("metric.revenue", dimensions=(DimensionRequest(dimension=_ref("dimension.order_date"), time_grain=TimeGrain.MONTH),))))
    derived = asyncio.run(builder.build(_plan("metric.purchase_frequency")))
    derived_filtered = asyncio.run(builder.build(_plan("metric.purchase_frequency", dimensions=(DimensionRequest(dimension=_ref("dimension.region")),), predicates=date)))
    assert request.semantic_model_id == 13 and request.semantic_view_id == 8
    assert request.metrics[0].semantic_identifier == "net_amount"
    assert request.dimensions[0].semantic_identifier == "customer_region"
    assert [(item.operator.value, item.value) for item in request.filters] == [("GTE", "2025-01-01"), ("LT", "2025-02-01")]
    assert month.dimensions[0].semantic_identifier == "sys_imp_month"
    assert derived.metrics[0].semantic_identifier == "purchase_frequency" and derived.metrics[0].aggregation is None
    assert derived_filtered.dimensions[0].semantic_identifier == "customer_region" and len(derived_filtered.filters) == 2


def test_builder_preserves_governed_metric_ranking() -> None:
    repo = asyncio.run(_repository()); builder = StructuredSemanticRequestBuilder(RuntimeBindingResolver(OntologyService(repo)))
    query = _plan("metric.revenue", dimensions=(DimensionRequest(dimension=_ref("dimension.product")),)).query.model_copy(
        update={"order_by": [SemanticOrdering(target_alias="metric.revenue", direction=SortDirection.DESC)], "limit": 1}
    )
    request = asyncio.run(builder.build(_plan("metric.revenue").model_copy(update={"query": query})))
    assert request.order_by == (("net_amount", "DESC"),) and request.limit == 1


def test_runtime_governed_path_needs_no_static_text_and_rejects_channel() -> None:
    repo = asyncio.run(_repository()); adapter = StructuredAdapter()
    runtime = ExecutionRuntime(adapter, structured_request_builder=StructuredSemanticRequestBuilder(RuntimeBindingResolver(OntologyService(repo))))
    success = asyncio.run(runtime.execute(_plan("metric.average_order_value")))
    channel = asyncio.run(runtime.execute(_plan("metric.revenue", dimensions=(DimensionRequest(dimension=_ref("dimension.channel")),))))
    assert success.status is ExecutionStatus.SUCCEEDED and "supersonic_query_text" not in success.evidence["executor_hints"]
    assert len(adapter.requests) == 1 and adapter.requests[0].metrics[0].semantic_identifier == "average_order_value"
    assert channel.status is ExecutionStatus.FAILED and channel.error.code.value == "UNSUPPORTED_RUNTIME_CAPABILITY"
    assert len(adapter.requests) == 1


def test_unpublished_binding_fails_without_call_and_partial_step_remains_partial() -> None:
    draft = asyncio.run(_repository(published=False)); adapter = StructuredAdapter()
    runtime = ExecutionRuntime(adapter, structured_request_builder=StructuredSemanticRequestBuilder(RuntimeBindingResolver(OntologyService(draft))))
    failed = asyncio.run(runtime.execute(_plan("metric.revenue")))
    assert failed.status is ExecutionStatus.FAILED and not adapter.requests
    published = asyncio.run(_repository()); runtime = ExecutionRuntime(adapter, structured_request_builder=StructuredSemanticRequestBuilder(RuntimeBindingResolver(OntologyService(published))))
    step = StepExecutionPlan(analysis_plan_id="plan", analysis_step_id="driver", executions=(_plan("metric.revenue"), _plan("metric.revenue", dimensions=(DimensionRequest(dimension=_ref("dimension.channel")),))))
    assert asyncio.run(runtime.execute_step(step)).status is ExecutionStatus.PARTIAL


def test_legacy_static_adapter_path_remains_compatible_without_builder() -> None:
    result = asyncio.run(ExecutionRuntime(StructuredAdapter()).execute(_plan("metric.revenue")))
    assert result.status is ExecutionStatus.SUCCEEDED and result.generated_sql == "SELECT legacy"

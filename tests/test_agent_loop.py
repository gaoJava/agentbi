from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from agentbi.execution import (
    AnalysisState,
    DeterministicAgentLoop,
    DeterministicNextActionPolicy,
    ExecutionError,
    ExecutionErrorCode,
    ExecutionResult,
    ExecutionStatus,
    ObservationBuilder,
    ObservationStatus,
    RuntimeCapabilityCoverage,
    StopReason,
)
from agentbi.ontology_service import (
    InMemoryOntologyRepository,
    OntologyAsset,
    OntologyAssetKind,
    OntologyRelation,
    OntologyService,
    OntologySnapshot,
    PlanningContextBuilder,
    PublicationState,
    verified_supersonic_sales_binding,
)
from agentbi.query_planner import ExecutionPlan, ExecutionTarget
from agentbi.semantic_query_ir import (
    AnalysisIntent,
    AnalysisTarget,
    AnalysisTargetKind,
    MetricRequest,
    OntologyReference,
    ResolutionStatus,
    SemanticQueryIR,
    SemanticScope,
)

V = "loop-test"


def _asset(asset_id, kind): return OntologyAsset(id=asset_id, kind=kind, name=asset_id, version=V, state=PublicationState.PUBLISHED)


def _context():
    assets = tuple([_asset("datamodel.sales_order_line", OntologyAssetKind.DATA_MODEL)] + [_asset(f"metric.{x}", OntologyAssetKind.METRIC) for x in ("revenue", "customer_count", "order_count", "purchase_frequency", "average_order_value")] + [_asset(f"dimension.{x}", OntologyAssetKind.DIMENSION) for x in ("region", "product", "customer", "channel", "order_date")])
    relations = tuple(OntologyRelation(source_id="metric.revenue", relation=kind, target_id=target, version=V) for kind, target in (("DRIVEN_BY", "metric.customer_count"), ("DRIVEN_BY", "metric.purchase_frequency"), ("DRIVEN_BY", "metric.average_order_value"), ("BREAKDOWN_BY", "dimension.region"), ("BREAKDOWN_BY", "dimension.product"), ("BREAKDOWN_BY", "dimension.customer"), ("BREAKDOWN_BY", "dimension.channel"), ("HAS_DIMENSION", "dimension.order_date"), ("MAPPED_TO", "datamodel.sales_order_line")))
    snapshot = OntologySnapshot(version=V, published_at=datetime(2026, 9, 17, tzinfo=UTC), assets=assets, relations=relations, runtime_bindings=(verified_supersonic_sales_binding(),))
    query = SemanticQueryIR(ontology_version=V, source_question="why", intent=AnalysisIntent.ROOT_CAUSE,
                            targets=[AnalysisTarget(raw_text="Revenue", kind=AnalysisTargetKind.METRIC, asset_id="metric.revenue", resolution_status=ResolutionStatus.RESOLVED)],
                            scopes=[SemanticScope(dimension=AnalysisTarget(raw_text="Region", kind=AnalysisTargetKind.DIMENSION, asset_id="dimension.region", resolution_status=ResolutionStatus.RESOLVED), value="华东")])
    return asyncio.run(PlanningContextBuilder(OntologyService(InMemoryOntologyRepository((snapshot,)))).build(query)), query


def _plan(query, step, metric="metric.revenue"):
    return ExecutionPlan(target=ExecutionTarget.SUPERSONIC, ontology_version=V, analysis_plan_id="plan", analysis_step_id=step,
                         query=query.model_copy(update={"metrics": [MetricRequest(metric=OntologyReference(asset_id=metric, version=V))]}))


def _initial_state(query):
    current, baseline = _plan(query, "current"), _plan(query, "baseline")
    def result(plan, value):
        return ExecutionResult(status=ExecutionStatus.SUCCEEDED, analysis_plan_id="plan", analysis_step_id=plan.analysis_step_id,
                               rows=({"metric.revenue": value},), row_count=1,
                               evidence={"structured_semantic_request": {"metrics": [{"semantic_identifier": "metric.revenue"}], "dimensions": []}})
    state = AnalysisState(); a, b = ObservationBuilder.build(current, result(current, 10)), ObservationBuilder.build(baseline, result(baseline, 20)); state.add(a); state.add(b); state.pair(a.observation_id, b.observation_id)
    return state, current, baseline


class FakeRuntime:
    def __init__(self, fail_metric=None): self.fail_metric = fail_metric
    async def execute(self, plan):
        metric = plan.query.metrics[0].metric.asset_id; dimensions = plan.query.dimensions
        if metric == self.fail_metric:
            return ExecutionResult(status=ExecutionStatus.FAILED, analysis_plan_id=plan.analysis_plan_id,
                                   analysis_step_id=plan.analysis_step_id,
                                   error=ExecutionError(code=ExecutionErrorCode.SUPERSONIC_QUERY_ERROR, message="test failure"))
        key = metric; rows = ({key: 1},) if not dimensions else ({dimensions[0].dimension.asset_id: "member", key: 1},)
        return ExecutionResult(status=ExecutionStatus.SUCCEEDED, analysis_plan_id=plan.analysis_plan_id, analysis_step_id=plan.analysis_step_id, rows=rows, row_count=1,
                               evidence={"structured_semantic_request": {"metrics": [{"semantic_identifier": key}], "dimensions": [{"semantic_identifier": dimensions[0].dimension.asset_id}] if dimensions else []}})


def _policy(): return DeterministicNextActionPolicy(RuntimeCapabilityCoverage.from_binding(verified_supersonic_sales_binding()))


def test_policy_is_ontology_driven_excludes_fixed_and_unsupported_dimensions():
    context, query = _context(); state, _, _ = _initial_state(query)
    actions = _policy().decide(context=context, state=state)
    contribution = {item.dimensions for item in actions if item.evidence_kind and item.evidence_kind.value == "CONTRIBUTION"}
    drivers = {item.target_metric_id for item in actions if item.evidence_kind and item.evidence_kind.value == "DRIVER_MOVEMENT"}
    assert contribution == {("dimension.product",), ("dimension.customer",)}
    assert drivers == {"metric.customer_count", "metric.purchase_frequency", "metric.average_order_value"}


def test_loop_collects_first_level_evidence_once_then_stops_with_trace():
    context, query = _context(); state, current, baseline = _initial_state(query)
    _, traces = asyncio.run(DeterministicAgentLoop(_policy(), FakeRuntime()).run(context=context, state=state, current_template=current, baseline_template=baseline))
    assert traces[-1].stop_reason is StopReason.FIRST_LEVEL_EVIDENCE_COMPLETE
    assert len(traces[0].actions) == 5 and len(state.comparisons) == 6
    assert len({item.key for item in traces[0].actions}) == 5 and traces[0].execution_statuses


def test_terminal_evidence_no_action_and_max_guard_are_deterministic():
    context, query = _context(); state, current, baseline = _initial_state(query)
    loop = DeterministicAgentLoop(_policy(), FakeRuntime(), max_iterations=1)
    _, traces = asyncio.run(loop.run(context=context, state=state, current_template=current, baseline_template=baseline))
    assert traces[-1].stop_reason is StopReason.MAX_ITERATIONS
    empty = DeterministicNextActionPolicy(RuntimeCapabilityCoverage(metric_asset_ids=frozenset(), dimension_asset_ids=frozenset()))
    assert empty.decide(context=context, state=_initial_state(query)[0])[0].stop_reason is StopReason.NO_NEW_ACTION


def test_partial_execution_failure_is_retained_as_terminal_evidence():
    context, query = _context(); state, current, baseline = _initial_state(query)
    state, traces = asyncio.run(DeterministicAgentLoop(_policy(), FakeRuntime("metric.customer_count")).run(
        context=context, state=state, current_template=current, baseline_template=baseline
    ))
    assert traces[-1].stop_reason is StopReason.FIRST_LEVEL_EVIDENCE_COMPLETE
    assert any(item.status is ObservationStatus.EXECUTION_FAILED for item in state.observations)

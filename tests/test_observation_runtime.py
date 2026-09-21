from __future__ import annotations

from decimal import Decimal

from agentbi.execution import (
    AnalysisState,
    ExecutionError,
    ExecutionErrorCode,
    ExecutionResult,
    ExecutionStatus,
    ObservationBuilder,
    ObservationDirection,
    ObservationStatus,
)
from agentbi.query_planner import ExecutionPlan, ExecutionTarget
from agentbi.semantic_query_ir import (
    DimensionRequest,
    MetricRequest,
    OntologyReference,
    SemanticQueryIR,
)

VERSION = "observation-test"


def _plan(metric="metric.revenue", dimensions=()):
    return ExecutionPlan(target=ExecutionTarget.SUPERSONIC, ontology_version=VERSION,
                         analysis_plan_id="analysis-1", analysis_step_id="step-1",
                         query=SemanticQueryIR(ontology_version=VERSION, source_question="test",
                                               metrics=[MetricRequest(metric=OntologyReference(asset_id=metric, version=VERSION))],
                                               dimensions=list(dimensions)))


def _result(rows, *, status=ExecutionStatus.SUCCEEDED, error=None, metric="net_amount", dimensions=(), step="step-1"):
    request = {"snapshot_version": VERSION, "binding_id": "binding-1", "backend": "SUPERSONIC",
               "connection_id": "primary", "semantic_model_id": 13, "semantic_view_id": 8,
               "metrics": [{"asset_id": "metric.revenue", "semantic_identifier": metric, "aggregation": "SUM"}],
               "dimensions": [{"semantic_identifier": value} for value in dimensions]}
    return ExecutionResult(status=status, analysis_plan_id="analysis-1", analysis_step_id=step,
                           rows=tuple(rows), row_count=len(rows), generated_sql="SELECT governed",
                           evidence={"structured_semantic_request": request}, error=error)


def test_scalar_and_current_baseline_decimal_comparison() -> None:
    plan = _plan(); current = ObservationBuilder.build(plan, _result([{"net_amount": 5196770}]))
    baseline = ObservationBuilder.build(plan, _result([{"net_amount": 6295965}], step="baseline"))
    comparison = ObservationBuilder.compare(current, baseline)
    member = comparison.members[0]
    assert current.status is ObservationStatus.SUCCESS and current.values[0].value == 5196770
    assert member.delta == -1099195
    assert member.delta_pct == Decimal(-1099195) / Decimal(6295965)
    assert member.direction is ObservationDirection.DOWN
    zero = ObservationBuilder.compare(current, ObservationBuilder.build(plan, _result([{"net_amount": 0}], step="zero")))
    assert zero.members[0].delta_pct is None and zero.members[0].direction is ObservationDirection.UP


def test_breakdown_alignment_driver_and_state_retrieval() -> None:
    region = DimensionRequest(dimension=OntologyReference(asset_id="dimension.region", version=VERSION))
    plan = _plan(dimensions=(region,))
    current = ObservationBuilder.build(plan, _result([{"customer_region": "华北", "net_amount": 10}, {"customer_region": "华东", "net_amount": 5}], dimensions=("customer_region",)))
    baseline = ObservationBuilder.build(plan, _result([{"customer_region": "华北", "net_amount": 8}, {"customer_region": "华南", "net_amount": 4}], dimensions=("customer_region",), step="baseline"))
    comparison = ObservationBuilder.compare(current, baseline)
    assert {item.member for item in comparison.members} == {("华北",), ("华东",), ("华南",)}
    assert next(item for item in comparison.members if item.member == ("华东",)).baseline is None
    drivers = []
    for metric, current_value, baseline_value in (
        ("metric.customer_count", 180, 200),
        ("metric.purchase_frequency", "17.005555555555556", "18.0"),
        ("metric.average_order_value", "35268.203201568115", "35000"),
    ):
        key = metric.split(".")[-1]
        current_driver = ObservationBuilder.build(_plan(metric), _result([{key: current_value}], metric=key))
        baseline_driver = ObservationBuilder.build(_plan(metric), _result([{key: baseline_value}], metric=key, step=f"{key}-baseline"))
        assert ObservationBuilder.compare(current_driver, baseline_driver).members[0].direction is not ObservationDirection.UNDEFINED
        drivers.extend((current_driver, baseline_driver))
    state = AnalysisState(); state.add(current); state.add(baseline)
    for item in drivers: state.add(item)
    assert state.by_step("step-1") and state.by_metric("metric.customer_count")
    assert state.by_dimension("dimension.region") == (current, baseline)
    assert state.pair(current.observation_id, baseline.observation_id).members


def test_failure_unsupported_provenance_and_determinism() -> None:
    plan = _plan()
    unsupported = ObservationBuilder.build(plan, _result([], status=ExecutionStatus.FAILED, error=ExecutionError(code=ExecutionErrorCode.UNSUPPORTED_RUNTIME_CAPABILITY, message="Channel absent")))
    failed = ObservationBuilder.build(plan, _result([], status=ExecutionStatus.FAILED, error=ExecutionError(code=ExecutionErrorCode.SUPERSONIC_QUERY_ERROR, message="upstream")))
    one = ObservationBuilder.build(plan, _result([{"net_amount": 1}]))
    two = ObservationBuilder.build(plan, _result([{"net_amount": 1}]))
    assert unsupported.status is ObservationStatus.UNSUPPORTED and not unsupported.values
    assert failed.status is ObservationStatus.EXECUTION_FAILED
    assert one.provenance.snapshot_version == VERSION and one.provenance.binding_id == "binding-1"
    assert one.provenance.backend == "SUPERSONIC" and one.provenance.semantic_view_id == 8
    assert one.model_dump(exclude={"observation_id", "provenance"}) == two.model_dump(exclude={"observation_id", "provenance"})

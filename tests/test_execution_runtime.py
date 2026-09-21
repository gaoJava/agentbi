from __future__ import annotations

import asyncio

import httpx

from agentbi.analysis_planner.contracts import (
    AnalysisPlan,
    AnalysisStep,
    PlanningReason,
    PlanningReasonType,
    StepKind,
)
from agentbi.execution import ExecutionErrorCode, ExecutionRuntime, ExecutionStatus
from agentbi.query_planner import ExecutionPlan, ExecutionTarget, StepExecutionPlan
from agentbi.semantic_query_ir import (
    AnalysisIntent,
    ComparisonSpec,
    ComparisonType,
    MetricRequest,
    OntologyReference,
    RelativeTimeReference,
    SemanticQueryIR,
    TimeScope,
    TimeScopeKind,
)

VERSION = "2026.1"


class FakeSuperSonic:
    def __init__(self, failing_metric: str | None = None, error: Exception | None = None):
        self.calls: list[tuple[ExecutionPlan, str | None]] = []
        self.failing_metric, self.error = failing_metric, error

    async def execute_execution_plan(self, plan: ExecutionPlan, *, time_expression: str | None):
        self.calls.append((plan, time_expression))
        if self.error:
            raise self.error
        if self.failing_metric and plan.query.metrics[0].metric.asset_id == self.failing_metric:
            raise RuntimeError("simulated upstream failure")
        return {"queryId": f"q-{len(self.calls)}", "queryResults": [{"metric": 100}], "querySql": "SELECT 100"}


def execution(metric: str = "metric.revenue", *, step: str = "step-1", time: TimeScope | None = None, comparison: ComparisonSpec | None = None) -> ExecutionPlan:
    return ExecutionPlan(target=ExecutionTarget.SUPERSONIC, ontology_version=VERSION, analysis_plan_id="plan-1", analysis_step_id=step, query=SemanticQueryIR(ontology_version=VERSION, source_question="销售额是多少？", intent=AnalysisIntent.QUERY, metrics=[MetricRequest(metric=OntologyReference(asset_id=metric, version=VERSION))], time_scope=time, comparison=comparison))


def test_lookup_trend_and_comparison_are_normalized_with_trace_and_time() -> None:
    fake = FakeSuperSonic(); runtime = ExecutionRuntime(fake)
    lookup = asyncio.run(runtime.execute(execution()))
    trend = asyncio.run(runtime.execute(execution(time=TimeScope(raw_text="最近30天", kind=TimeScopeKind.RELATIVE, relative_reference=RelativeTimeReference.LAST_N_DAYS, amount=30))))
    yoy = asyncio.run(runtime.execute(execution(comparison=ComparisonSpec(type=ComparisonType.YOY, raw_text="同比"))))
    assert [item.status for item in (lookup, trend, yoy)] == [ExecutionStatus.SUCCEEDED] * 3
    assert trend.evidence["time_expression"] == "LAST_N_DAYS:30"
    assert yoy.evidence["comparison"]["type"] == "YOY"
    assert lookup.analysis_plan_id == "plan-1" and lookup.analysis_step_id == "step-1" and lookup.execution_id and lookup.query_id


def test_step_partial_failure_and_driver_batch_keep_successful_evidence() -> None:
    fake = FakeSuperSonic(failing_metric="metric.customer"); runtime = ExecutionRuntime(fake)
    step = StepExecutionPlan(analysis_plan_id="plan-1", analysis_step_id="step-2", executions=(execution("metric.product", step="step-2"), execution("metric.customer", step="step-2"), execution("metric.channel", step="step-2")))
    result = asyncio.run(runtime.execute_step(step))
    assert result.status is ExecutionStatus.PARTIAL
    assert [item.status for item in result.results] == [ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED, ExecutionStatus.SUCCEEDED]
    assert result.results[1].error.code is ExecutionErrorCode.SUPERSONIC_QUERY_ERROR


def test_unresolved_time_and_transport_errors_are_typed() -> None:
    recent = TimeScope(raw_text="最近", kind=TimeScopeKind.RELATIVE, relative_reference=RelativeTimeReference.RECENT)
    assert asyncio.run(ExecutionRuntime(FakeSuperSonic()).execute(execution(time=recent))).error.code is ExecutionErrorCode.UNRESOLVED_TIME_SCOPE
    timeout = asyncio.run(ExecutionRuntime(FakeSuperSonic(error=httpx.TimeoutException("slow"))).execute(execution()))
    network = asyncio.run(ExecutionRuntime(FakeSuperSonic(error=httpx.ConnectError("down"))).execute(execution()))
    assert timeout.error.code is ExecutionErrorCode.SUPERSONIC_TIMEOUT
    assert network.error.code is ExecutionErrorCode.SUPERSONIC_NETWORK_ERROR


def test_plan_dependency_failure_skips_downstream_step_deterministically() -> None:
    reason = PlanningReason(reason_type=PlanningReasonType.USER_INTENT, source_asset_ids=("metric.revenue",), message="test")
    plan = AnalysisPlan(ontology_version=VERSION, intent=AnalysisIntent.ROOT_CAUSE, target_asset_id="metric.revenue", goal="test", steps=(AnalysisStep(step_id="step-1", kind=StepKind.BASELINE, target_asset_id="metric.revenue", reason=reason), AnalysisStep(step_id="step-2", kind=StepKind.DRIVER, target_asset_id="metric.revenue", reason=reason, dependencies=("step-1",))))
    failed = StepExecutionPlan(analysis_plan_id=plan.plan_id, analysis_step_id="step-1", executions=(execution(step="step-1", time=TimeScope(raw_text="最近", kind=TimeScopeKind.RELATIVE, relative_reference=RelativeTimeReference.RECENT)),))
    downstream = StepExecutionPlan(analysis_plan_id=plan.plan_id, analysis_step_id="step-2", executions=(execution(step="step-2"),))
    result = asyncio.run(ExecutionRuntime(FakeSuperSonic()).execute_plan(plan, (failed, downstream)))
    assert [item.status for item in result] == [ExecutionStatus.FAILED, ExecutionStatus.SKIPPED]

"""Execute immutable query plans without adding analysis semantics."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

import httpx

from agentbi.analysis_planner.contracts import AnalysisPlan
from agentbi.ontology_service import RuntimeBindingResolutionError
from agentbi.query_planner.contracts import ExecutionPlan, ExecutionTarget, StepExecutionPlan
from agentbi.semantic_query_ir import ComparisonType, RelativeTimeReference, TimeScopeKind

from .contracts import (
    ExecutionError,
    ExecutionErrorCode,
    ExecutionResult,
    ExecutionStatus,
    StepExecutionResult,
)
from .structured import StructuredSemanticRequestBuilder


class SuperSonicExecutionAdapter(Protocol):
    async def execute_execution_plan(self, plan: ExecutionPlan, *, time_expression: str | None) -> dict[str, Any]: ...

    async def execute_structured_request(self, request: Any) -> dict[str, Any]: ...


class ExecutionRuntime:
    """Small deterministic runtime over the Phase 5 execution-plan contract."""

    def __init__(self, supersonic: SuperSonicExecutionAdapter, *, structured_request_builder: StructuredSemanticRequestBuilder | None = None):
        self._supersonic = supersonic
        self._structured_request_builder = structured_request_builder

    async def execute(self, plan: ExecutionPlan) -> ExecutionResult:
        execution_id = str(uuid4())
        started = datetime.now(UTC)
        tick = time.perf_counter()
        invalid = self._validate(plan)
        if invalid:
            return self._failed(plan, execution_id, started, tick, invalid)
        try:
            if self._structured_request_builder is not None:
                request = await self._structured_request_builder.build(plan)
                raw = await self._supersonic.execute_structured_request(request)
            else:
                raw = await self._supersonic.execute_execution_plan(
                    plan, time_expression=self._time_expression(plan)
                )
            return self._normalize(plan, execution_id, started, tick, raw)
        except RuntimeBindingResolutionError as exc:
            return self._failed(plan, execution_id, started, tick, ExecutionError(
                code=exc.code, message=str(exc), technical_message=type(exc).__name__))
        except TimeoutError as exc:
            return self._failed(plan, execution_id, started, tick, self._error(
                ExecutionErrorCode.SUPERSONIC_TIMEOUT, "SuperSonic query timed out", exc))
        except httpx.TimeoutException as exc:
            return self._failed(plan, execution_id, started, tick, self._error(
                ExecutionErrorCode.SUPERSONIC_TIMEOUT, "SuperSonic query timed out", exc))
        except httpx.RequestError as exc:
            return self._failed(plan, execution_id, started, tick, self._error(
                ExecutionErrorCode.SUPERSONIC_NETWORK_ERROR, "SuperSonic network request failed", exc))
        except Exception as exc:  # noqa: BLE001 - adapter failures become typed evidence.
            if type(exc).__name__ == "ExecutionBindingError":
                return self._failed(plan, execution_id, started, tick, self._error(
                    ExecutionErrorCode.MISSING_EXECUTION_MAPPING,
                    "published SuperSonic runtime binding is required", exc))
            return self._failed(plan, execution_id, started, tick, self._error(
                ExecutionErrorCode.SUPERSONIC_QUERY_ERROR, "SuperSonic query failed", exc))

    async def execute_step(self, step: StepExecutionPlan) -> StepExecutionResult:
        results = tuple([await self.execute(plan) for plan in step.executions])
        status = self._aggregate(results)
        return StepExecutionResult(
            analysis_plan_id=step.analysis_plan_id,
            analysis_step_id=step.analysis_step_id,
            status=status,
            results=results,
        )

    async def execute_plan(self, plan: AnalysisPlan, steps: tuple[StepExecutionPlan, ...]) -> tuple[StepExecutionResult, ...]:
        by_id = {step.analysis_step_id: step for step in steps}
        completed: dict[str, StepExecutionResult] = {}
        ordered: list[StepExecutionResult] = []
        for analysis_step in plan.steps:
            blocked = any(completed[dependency].status is ExecutionStatus.FAILED for dependency in analysis_step.dependencies)
            if blocked:
                result = StepExecutionResult(
                    analysis_plan_id=plan.plan_id, analysis_step_id=analysis_step.step_id,
                    status=ExecutionStatus.SKIPPED, dependency_warning="a required dependency failed",
                )
            elif analysis_step.step_id not in by_id:
                result = StepExecutionResult(
                    analysis_plan_id=plan.plan_id, analysis_step_id=analysis_step.step_id,
                    status=ExecutionStatus.FAILED,
                )
            else:
                result = await self.execute_step(by_id[analysis_step.step_id])
            completed[analysis_step.step_id] = result
            ordered.append(result)
        return tuple(ordered)

    @staticmethod
    def _aggregate(results: tuple[ExecutionResult, ...]) -> ExecutionStatus:
        if not results:
            return ExecutionStatus.FAILED
        successes = sum(result.status is ExecutionStatus.SUCCEEDED for result in results)
        if successes == len(results):
            return ExecutionStatus.SUCCEEDED
        return ExecutionStatus.PARTIAL if successes else ExecutionStatus.FAILED

    @staticmethod
    def _error(code: ExecutionErrorCode, message: str, exc: Exception) -> ExecutionError:
        return ExecutionError(code=code, message=message, technical_message=f"{type(exc).__name__}: {exc}")

    def _validate(self, plan: ExecutionPlan) -> ExecutionError | None:
        if plan.target is not ExecutionTarget.SUPERSONIC or not plan.analysis_plan_id or not plan.analysis_step_id:
            return ExecutionError(code=ExecutionErrorCode.INVALID_EXECUTION_PLAN, message="execution plan lacks a valid target or trace")
        if not plan.query.metrics:
            return ExecutionError(code=ExecutionErrorCode.INVALID_EXECUTION_PLAN, message="execution plan has no metric")
        scope = plan.query.time_scope
        if scope and scope.kind is TimeScopeKind.RELATIVE and scope.relative_reference is RelativeTimeReference.RECENT:
            return ExecutionError(code=ExecutionErrorCode.UNRESOLVED_TIME_SCOPE, message="RECENT has no governed execution window")
        comparison = plan.query.comparison
        if comparison and comparison.type not in {ComparisonType.YOY, ComparisonType.MOM, ComparisonType.PREVIOUS_PERIOD, ComparisonType.CUSTOM}:
            return ExecutionError(code=ExecutionErrorCode.UNSUPPORTED_COMPARISON, message="comparison is not supported by the execution runtime")
        return None

    @staticmethod
    def _time_expression(plan: ExecutionPlan) -> str | None:
        scope = plan.query.time_scope
        if scope is None:
            return None
        if scope.kind is TimeScopeKind.EXPLICIT:
            return {"今年": "THIS_YEAR", "去年": "LAST_YEAR"}.get(scope.raw_text, scope.raw_text)
        reference = scope.relative_reference
        if reference is RelativeTimeReference.LAST_N_DAYS:
            return f"LAST_N_DAYS:{scope.amount}"
        if reference is RelativeTimeReference.LAST_N_WEEKS:
            return f"LAST_N_WEEKS:{scope.amount}"
        if reference is RelativeTimeReference.LAST_N_MONTHS:
            return f"LAST_N_MONTHS:{scope.amount}"
        return reference.value if reference else None

    def _normalize(self, plan: ExecutionPlan, execution_id: str, started: datetime, tick: float, raw: dict[str, Any]) -> ExecutionResult:
        rows = raw.get("queryResults")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return self._failed(plan, execution_id, started, tick, ExecutionError(
                code=ExecutionErrorCode.INVALID_RESPONSE, message="SuperSonic returned invalid query results"))
        safe_rows = tuple(rows)
        columns = tuple(str(column) for column in safe_rows[0]) if safe_rows else ()
        sql = raw.get("querySql") if isinstance(raw.get("querySql"), str) else None
        return ExecutionResult(
            execution_id=execution_id, analysis_plan_id=plan.analysis_plan_id,
            analysis_step_id=plan.analysis_step_id, status=ExecutionStatus.SUCCEEDED,
            started_at=started, finished_at=datetime.now(UTC),
            duration_ms=max(0, round((time.perf_counter() - tick) * 1000)),
            query_id=str(raw.get("queryId")) if raw.get("queryId") is not None else None,
            rows=safe_rows, row_count=len(safe_rows), columns=columns, generated_sql=sql,
            evidence={"time_expression": self._time_expression(plan), "comparison": plan.query.comparison.model_dump(mode="json") if plan.query.comparison else None, "executor_hints": plan.executor_hints, "structured_semantic_request": raw.get("structuredSemanticRequest")},
        )

    @staticmethod
    def _failed(plan: ExecutionPlan, execution_id: str, started: datetime, tick: float, error: ExecutionError) -> ExecutionResult:
        return ExecutionResult(
            execution_id=execution_id, analysis_plan_id=plan.analysis_plan_id,
            analysis_step_id=plan.analysis_step_id, status=ExecutionStatus.FAILED,
            started_at=started, finished_at=datetime.now(UTC),
            duration_ms=max(0, round((time.perf_counter() - tick) * 1000)), error=error,
        )

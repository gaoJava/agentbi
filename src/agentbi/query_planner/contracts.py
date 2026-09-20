"""Planning boundary between the ontology-aware IR and execution adapters."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from agentbi.semantic_query_ir import SemanticQueryIR


class ExecutionTarget(StrEnum):
    SUPERSONIC = "supersonic"
    SUPERSET = "superset"
    DIRECT_WAREHOUSE = "direct_warehouse"


class ExecutionPlan(BaseModel):
    """Validated plan metadata; physical SQL belongs only to an executor implementation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_version: str = "1.0"
    target: ExecutionTarget
    ontology_version: str = Field(min_length=1, max_length=64)
    query: SemanticQueryIR
    policy_decisions: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    executor_hints: dict[str, str | int | bool] = Field(default_factory=dict, max_length=30)
    analysis_plan_id: str | None = None
    analysis_step_id: str | None = None


class QueryPlanningIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str
    message: str


class StepExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    analysis_plan_id: str
    analysis_step_id: str
    executions: tuple[ExecutionPlan, ...] = ()
    issues: tuple[QueryPlanningIssue, ...] = ()


class PlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: ExecutionPlan | None = None
    rejection_reason: str | None = Field(default=None, max_length=512)


class QueryPlanner(Protocol):
    async def plan(self, query: SemanticQueryIR, *, actor_id: str) -> PlanResult: ...


class NoopQueryPlanner:
    """Migration-safe default.  It explicitly refuses to emit an executable plan."""

    async def plan(self, query: SemanticQueryIR, *, actor_id: str) -> PlanResult:
        return PlanResult(rejection_reason="query planner is not enabled")

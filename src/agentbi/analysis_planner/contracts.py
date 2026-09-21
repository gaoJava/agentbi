from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentbi.semantic_query_ir import AnalysisIntent, ComparisonSpec


class PlanStatus(StrEnum): PLANNED="PLANNED"; RUNNING="RUNNING"; COMPLETED="COMPLETED"; FAILED="FAILED"
class StepStatus(StrEnum): PLANNED="PLANNED"
class StepKind(StrEnum): LOOKUP="LOOKUP"; BASELINE="BASELINE"; TREND="TREND"; COMPARISON="COMPARISON"; BREAKDOWN="BREAKDOWN"; CONTRIBUTION="CONTRIBUTION"; DRIVER="DRIVER"
class BaselineSource(StrEnum): USER_SPECIFIED="USER_SPECIFIED"; PLANNER_DEFAULT="PLANNER_DEFAULT"; STRATEGY_DEFAULT="STRATEGY_DEFAULT"
class PlanningReasonType(StrEnum): USER_INTENT="USER_INTENT"; ONTOLOGY_RELATION="ONTOLOGY_RELATION"; PLANNER_DEFAULT="PLANNER_DEFAULT"
class PlanningIssueCode(StrEnum): UNRESOLVED_TARGET="UNRESOLVED_TARGET"; AMBIGUOUS_TARGET="AMBIGUOUS_TARGET"; MISSING_CAPABILITY="MISSING_CAPABILITY"; NO_APPLICABLE_STRATEGY="NO_APPLICABLE_STRATEGY"; MISSING_BREAKDOWN_DIMENSION="MISSING_BREAKDOWN_DIMENSION"; MISSING_TIME_CONTEXT="MISSING_TIME_CONTEXT"

class PlanningReason(BaseModel):
    model_config=ConfigDict(extra="forbid", frozen=True)
    reason_type: PlanningReasonType
    source_asset_ids: tuple[str,...]=()
    source_relation: str|None=None
    source_strategy_id: str|None=None
    message: str

class AnalysisStep(BaseModel):
    model_config=ConfigDict(extra="forbid", frozen=True)
    step_id: str
    kind: StepKind
    strategy_id: str|None=None
    capability_id: str|None=None
    target_asset_id: str
    dimensions: tuple[str,...]=()
    driver_asset_ids: tuple[str,...]=()
    dependencies: tuple[str,...]=()
    reason: PlanningReason
    baseline: ComparisonSpec|None=None
    baseline_source: BaselineSource|None=None
    status: StepStatus=StepStatus.PLANNED

class AnalysisPlan(BaseModel):
    model_config=ConfigDict(extra="forbid", frozen=True)
    plan_id: str=Field(default_factory=lambda: str(uuid4()))
    ontology_version: str
    intent: AnalysisIntent
    target_asset_id: str
    goal: str
    steps: tuple[AnalysisStep,...]
    status: PlanStatus=PlanStatus.PLANNED
    @model_validator(mode="after")
    def _dag(self):
        ids={s.step_id for s in self.steps}
        if len(ids)!=len(self.steps) or any(not set(s.dependencies)<=ids-{s.step_id} for s in self.steps): raise ValueError("invalid analysis-step dependencies")
        seen=set(); active=set(); graph={s.step_id:s.dependencies for s in self.steps}
        def visit(n):
            if n in active: raise ValueError("analysis-plan dependencies contain a cycle")
            if n not in seen:
                active.add(n); [visit(x) for x in graph[n]]; active.remove(n); seen.add(n)
        [visit(n) for n in graph]; return self

class PlanningIssue(BaseModel):
    model_config=ConfigDict(extra="forbid", frozen=True)
    code: PlanningIssueCode
    message: str
    blocking: bool=False

class PlanningResult(BaseModel):
    model_config=ConfigDict(extra="forbid", frozen=True)
    plan: AnalysisPlan|None=None
    issues: tuple[PlanningIssue,...]=()

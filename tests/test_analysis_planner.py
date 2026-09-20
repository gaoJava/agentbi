from __future__ import annotations

import asyncio
import runpy

from agentbi.analysis_planner import DeterministicAnalysisPlanner
from agentbi.analysis_planner.contracts import BaselineSource, PlanningIssueCode, StepKind
from agentbi.ontology_service import StrategyApplicabilityStatus
from agentbi.semantic_query_ir import (
    AnalysisIntent,
    AnalysisTarget,
    AnalysisTargetKind,
    ComparisonSpec,
    ComparisonType,
    ResolutionStatus,
)

_fixture=runpy.run_path("tests/test_planning_context.py")
def _context(q): return asyncio.run(_fixture["builder"]()[1].build(q))
def _query(**updates): return _fixture["query"]().model_copy(update=updates)
def _plan(q): return asyncio.run(DeterministicAnalysisPlanner().plan(q,_context(q)))

def test_root_cause_plan_is_deterministic_and_excludes_fixed_scope_dimension():
    result=_plan(_query())
    assert result.plan is not None
    steps=result.plan.steps
    assert [s.kind for s in steps]==[StepKind.BASELINE,StepKind.CONTRIBUTION,StepKind.DRIVER]
    assert steps[0].baseline_source is BaselineSource.PLANNER_DEFAULT
    assert steps[1].dimensions==("dimension.product","dimension.customer")
    assert steps[1].dependencies==("step-1",) and steps[2].dependencies==("step-1",)
    assert steps[2].reason.source_relation=="DRIVEN_BY"
    assert _plan(_query()).plan.plan_id == result.plan.plan_id

def test_query_trend_comparison_and_breakdown_plans():
    query=_query(intent=AnalysisIntent.QUERY,source_question="销售额是多少？")
    assert [s.kind for s in _plan(query).plan.steps]==[StepKind.LOOKUP]
    trend=_query(intent=AnalysisIntent.TREND,source_question="销售额趋势")
    assert [s.kind for s in _plan(trend).plan.steps]==[StepKind.TREND]
    comparison=_query(intent=AnalysisIntent.COMPARISON,comparison=ComparisonSpec(type=ComparisonType.YOY,raw_text="同比"))
    step=_plan(comparison).plan.steps[0]; assert step.kind is StepKind.COMPARISON and step.baseline_source is BaselineSource.USER_SPECIFIED
    breakdown=_query(intent=AnalysisIntent.BREAKDOWN,breakdown_hints=[AnalysisTarget(raw_text="渠道",kind=AnalysisTargetKind.DIMENSION,asset_id="dimension.channel",resolution_status=ResolutionStatus.RESOLVED)])
    step=_plan(breakdown).plan.steps[0]; assert step.kind is StepKind.BREAKDOWN and step.dimensions==("dimension.channel",)

def test_blocking_target_and_missing_capability_results():
    ambiguous=_query(targets=[AnalysisTarget(raw_text="销售额",kind=AnalysisTargetKind.METRIC,resolution_status=ResolutionStatus.AMBIGUOUS,candidate_asset_ids=("a","b"))])
    assert _plan(ambiguous).plan is None and _plan(ambiguous).issues[0].code is PlanningIssueCode.AMBIGUOUS_TARGET
    context=_context(_query()); context=context.model_copy(update={"strategy_applicability":tuple(item.model_copy(update={"status":StrategyApplicabilityStatus.MISSING_CAPABILITY}) if item.strategy.id=="strategy.driver" else item for item in context.strategy_applicability)})
    result=asyncio.run(DeterministicAnalysisPlanner().plan(_query(),context))
    assert any(issue.code is PlanningIssueCode.MISSING_CAPABILITY for issue in result.issues)

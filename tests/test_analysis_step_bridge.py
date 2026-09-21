from __future__ import annotations

import asyncio
import runpy

from agentbi.analysis_planner import AnalysisStepQueryPlanner, DeterministicAnalysisPlanner
from agentbi.semantic_query_ir import (
 AnalysisIntent,
 AnalysisTarget,
 AnalysisTargetKind,
 ResolutionStatus,
 TimeScope,
 TimeScopeKind,
)

_f=runpy.run_path("tests/test_planning_context.py")
def result(q):
 c=asyncio.run(_f["builder"]()[1].build(q)); p=asyncio.run(DeterministicAnalysisPlanner().plan(q,c)).plan; return c,p
def test_root_cause_bridge_preserves_scope_and_splits_queries():
 q=_f["query"]().model_copy(update={"time_scope": TimeScope(raw_text="2025年1月", kind=TimeScopeKind.EXPLICIT)}); c,p=result(q); b=AnalysisStepQueryPlanner()
 plans=[b.plan(p,s,q,c) for s in p.steps]
 assert [len(x.executions) for x in plans]==[2,2,3]
 assert all(e.query.predicates[0].value=="华东" for x in plans for e in x.executions)
 assert plans[0].executions[0].query.comparison.type.value=="PREVIOUS_PERIOD"
 assert all(e.analysis_plan_id==p.plan_id and e.analysis_step_id for x in plans for e in x.executions)
def test_lookup_and_breakdown_bridge():
 q=_f["query"]().model_copy(update={"intent":AnalysisIntent.QUERY,"source_question":"销售额是多少","time_scope": TimeScope(raw_text="2025年1月", kind=TimeScopeKind.EXPLICIT)}); c,p=result(q); assert len(AnalysisStepQueryPlanner().plan(p,p.steps[0],q,c).executions)==1
 q=_f["query"]().model_copy(update={"intent":AnalysisIntent.BREAKDOWN,"time_scope": TimeScope(raw_text="2025年1月", kind=TimeScopeKind.EXPLICIT),"breakdown_hints":[AnalysisTarget(raw_text="渠道",kind=AnalysisTargetKind.DIMENSION,asset_id="dimension.channel",resolution_status=ResolutionStatus.RESOLVED)]}); c,p=result(q); x=AnalysisStepQueryPlanner().plan(p,p.steps[0],q,c); assert x.executions[0].query.dimensions[0].dimension.asset_id=="dimension.channel"

"""Translate internal planning contracts into stable Workbench-facing DTOs."""
from __future__ import annotations

from agentbi.models import (
 AnalysisIssueDTO,
 AnalysisProgressDTO,
 AnalysisProgressStatus,
 AnalysisProgressStep,
 ClarificationDTO,
 ClarificationOptionDTO,
 DeveloperTraceDTO,
)
from agentbi.semantic_query_ir import ResolutionStatus

from .contracts import PlanningIssueCode

_TITLES={"BASELINE":"确认销售额变化","CONTRIBUTION":"分析业务维度贡献","DRIVER":"检查关键驱动因素","LOOKUP":"获取指标结果","TREND":"查看变化趋势","COMPARISON":"比较指标表现","BREAKDOWN":"按业务维度查看"}
class AnalysisUIPresenter:
 def present(self, query, context, result, executions=()):
  if context.grounding.target.resolution_status is ResolutionStatus.AMBIGUOUS:
   return None, ClarificationDTO(clarification_id="metric-clarification",question=f"你说的“{context.grounding.target.raw_text}”指的是哪个指标？",options=[ClarificationOptionDTO(id=x,label=x,description="可用业务指标",value=x) for x in context.grounding.target.candidate_asset_ids])
  if not result.plan: return None,None
  steps=[]
  for step in result.plan.steps:
   title=_TITLES[step.kind.value]; desc=step.reason.message
   if step.kind.value=="CONTRIBUTION": desc="检查"+"、".join(next(d.name for d in context.breakdown_dimensions if d.id==x) for x in step.dimensions)+"的变化"
   if step.kind.value=="DRIVER": desc="分析"+"、".join(next(d.name for d in context.drivers if d.id==x) for x in step.driver_asset_ids)
   steps.append(AnalysisProgressStep(id=step.step_id,title=title,description=desc,user_explanation=step.reason.message))
  issues=[_issue(x) for x in result.issues]
  status=AnalysisProgressStatus.PARTIAL if issues else AnalysisProgressStatus.PLANNED
  progress=AnalysisProgressDTO(analysis_id=result.plan.plan_id,status=status,goal=result.plan.goal,steps=steps,partial_issues=issues)
  trace=DeveloperTraceDTO(ontology_version=context.snapshot_version,semantic_query_ir=query.model_dump(mode="json"),planning_context=context.model_dump(mode="json"),analysis_plan=result.plan.model_dump(mode="json"),execution_plans=[x.model_dump(mode="json") for x in executions])
  return progress,trace
def _issue(issue):
 titles={PlanningIssueCode.MISSING_CAPABILITY:"部分分析暂不可用",PlanningIssueCode.AMBIGUOUS_TARGET:"需要确认指标口径",PlanningIssueCode.UNRESOLVED_TARGET:"未识别分析指标"}
 return AnalysisIssueDTO(code=issue.code.value,severity="warning",title=titles.get(issue.code,"分析条件不足"),message=issue.message,recoverable=not issue.blocking,technical_code=issue.code.value)

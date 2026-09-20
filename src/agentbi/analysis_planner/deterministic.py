from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from agentbi.ontology_service.planning import PlanningContext, StrategyApplicabilityStatus
from agentbi.semantic_query_ir import (
    AnalysisIntent,
    ComparisonSpec,
    ComparisonType,
    PredicateOperator,
    ResolutionStatus,
)

from .contracts import *

_PRIORITY={"trend":0,"yoy":0,"mom":0,"contribution":1,"dimension":1,"driver":2}
class DeterministicAnalysisPlanner:
    async def plan(self, query, context: PlanningContext) -> PlanningResult:
        target=context.grounding.target
        if target.resolution_status is ResolutionStatus.AMBIGUOUS: return PlanningResult(issues=(PlanningIssue(code=PlanningIssueCode.AMBIGUOUS_TARGET,message="target remains ambiguous",blocking=True),))
        if target.resolution_status is not ResolutionStatus.RESOLVED or context.target_asset is None: return PlanningResult(issues=(PlanningIssue(code=PlanningIssueCode.UNRESOLVED_TARGET,message="target is not resolved",blocking=True),))
        available={x.strategy.id:x for x in context.strategy_applicability if x.status is StrategyApplicabilityStatus.AVAILABLE}
        issues=[]; steps=[]; tid=context.target_asset.id
        def choose(words):
            ids=sorted((i for i in available if any(w in i.casefold() for w in words)),key=lambda i:(_PRIORITY.get(next((w for w in words if w in i.casefold()),""),9),i))
            return available[ids[0]].strategy if ids else None
        def add(kind,strategy,reason,**kw):
            cap=(strategy.strategy_definition.requires_capability[0] if strategy and strategy.strategy_definition and strategy.strategy_definition.requires_capability else None)
            steps.append(AnalysisStep(step_id=f"step-{len(steps)+1}",kind=kind,strategy_id=strategy.id if strategy else None,capability_id=cap,target_asset_id=tid,reason=reason,**kw))
        def unavailable(label):
            statuses=[x.status for x in context.strategy_applicability if label in x.strategy.id.casefold()]
            code=PlanningIssueCode.MISSING_CAPABILITY if StrategyApplicabilityStatus.MISSING_CAPABILITY in statuses else PlanningIssueCode.NO_APPLICABLE_STRATEGY
            issues.append(PlanningIssue(code=code,message=f"{label} strategy is unavailable"))
        if query.intent is AnalysisIntent.QUERY:
            add(StepKind.LOOKUP,None,PlanningReason(reason_type=PlanningReasonType.USER_INTENT,source_asset_ids=(tid,),message="user requested current metric value"))
        elif query.intent is AnalysisIntent.TREND:
            s=choose(("trend",));
            if s: add(StepKind.TREND,s,PlanningReason(reason_type=PlanningReasonType.USER_INTENT,source_asset_ids=(tid,),source_strategy_id=s.id,message="user requested trend"))
            else: unavailable("trend")
        elif query.intent is AnalysisIntent.COMPARISON:
            word="yoy" if query.comparison and query.comparison.type is ComparisonType.YOY else "mom" if query.comparison and query.comparison.type is ComparisonType.MOM else "comparison"
            s=choose((word,"comparison"));
            if s: add(StepKind.COMPARISON,s,PlanningReason(reason_type=PlanningReasonType.USER_INTENT,source_asset_ids=(tid,),source_strategy_id=s.id,message="user specified comparison"),baseline=query.comparison,baseline_source=BaselineSource.USER_SPECIFIED)
            else: unavailable(word)
        elif query.intent is AnalysisIntent.BREAKDOWN:
            requested=[h.asset_id for h in context.grounding.breakdown_hints if h.asset_id]
            dims=tuple(x for x in requested if x in {d.id for d in context.breakdown_dimensions})
            if not dims: issues.append(PlanningIssue(code=PlanningIssueCode.MISSING_BREAKDOWN_DIMENSION,message="no governed requested breakdown dimension",blocking=True))
            else: add(StepKind.BREAKDOWN,choose(("dimension","breakdown")),PlanningReason(reason_type=PlanningReasonType.USER_INTENT,source_asset_ids=(tid,*dims),source_relation="BREAKDOWN_BY",message="user requested breakdown dimension"),dimensions=dims)
        else:
            baseline=next((item.strategy for item in context.strategy_applicability if item.status is StrategyApplicabilityStatus.AVAILABLE and item.strategy.strategy_definition and query.intent in item.strategy.strategy_definition.applicable_intents and item.strategy.strategy_definition.requires_time_dimension), None)
            if baseline: add(StepKind.BASELINE,baseline,PlanningReason(reason_type=PlanningReasonType.PLANNER_DEFAULT,source_asset_ids=(tid,),source_strategy_id=baseline.id,message="root-cause analysis needs a comparison baseline"),baseline=ComparisonSpec(type=ComparisonType.PREVIOUS_PERIOD,raw_text="previous period"),baseline_source=BaselineSource.PLANNER_DEFAULT)
            else: issues.append(PlanningIssue(code=PlanningIssueCode.MISSING_TIME_CONTEXT,message="no applicable baseline strategy"))
            dep=(steps[0].step_id,) if steps else ()
            scoped={s.dimension.asset_id for s in context.grounding.scopes if s.requested.operator is PredicateOperator.EQ and s.dimension.asset_id}
            requested=tuple(h.asset_id for h in context.grounding.breakdown_hints if h.asset_id)
            governed={d.id for d in context.breakdown_dimensions}
            # Explicit user requests remain executable candidates so Runtime can
            # return a governed unsupported-capability result.  Automatic root
            # cause candidates are restricted to the published binding coverage.
            candidates=requested or tuple(d.id for d in context.breakdown_dimensions)
            dims=tuple(d for d in candidates if d in governed and d not in scoped and (bool(requested) or d in context.runtime_dimension_asset_ids))
            s=choose(("contribution","dimension"))
            if s and dims: add(StepKind.CONTRIBUTION,s,PlanningReason(reason_type=PlanningReasonType.ONTOLOGY_RELATION,source_asset_ids=(tid,*dims),source_relation="BREAKDOWN_BY",source_strategy_id=s.id,message="target supports governed breakdown dimensions"),dimensions=dims,dependencies=dep)
            elif not dims: issues.append(PlanningIssue(code=PlanningIssueCode.MISSING_BREAKDOWN_DIMENSION,message="no unscoped breakdown dimension"))
            else: unavailable("contribution")
            s=choose(("driver",)); drivers=tuple(d.id for d in context.drivers)
            if s and drivers: add(StepKind.DRIVER,s,PlanningReason(reason_type=PlanningReasonType.ONTOLOGY_RELATION,source_asset_ids=(tid,*drivers),source_relation="DRIVEN_BY",source_strategy_id=s.id,message="target has governed drivers"),driver_asset_ids=drivers,dependencies=dep)
            elif not drivers: issues.append(PlanningIssue(code=PlanningIssueCode.NO_APPLICABLE_STRATEGY,message="target has no governed drivers"))
            else: unavailable("driver")
        if not steps: return PlanningResult(issues=tuple(issues or [PlanningIssue(code=PlanningIssueCode.NO_APPLICABLE_STRATEGY,message="no deterministic plan")]))
        plan_id=str(uuid5(NAMESPACE_URL, f"{context.snapshot_version}|{query.intent}|{tid}|{query.source_question}"))
        return PlanningResult(plan=AnalysisPlan(plan_id=plan_id,ontology_version=context.snapshot_version or query.ontology_version,intent=query.intent,target_asset_id=tid,goal=query.source_question,steps=tuple(steps)),issues=tuple(issues))

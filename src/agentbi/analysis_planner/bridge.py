"""Lower already chosen analysis steps into non-executing QueryPlanner contracts."""
from __future__ import annotations

import re

from agentbi.query_planner import (
    ExecutionPlan,
    ExecutionTarget,
    QueryPlanningIssue,
    StepExecutionPlan,
)
from agentbi.query_planner.time_lowering import lower_explicit_time_scope, previous_year_scope
from agentbi.semantic_query_ir import (
    DimensionRequest,
    MetricRequest,
    OntologyReference,
    SemanticPredicate,
    SemanticQueryIR,
    TimeGrain,
    TimeScope,
    TimeScopeKind,
)

from .contracts import AnalysisPlan, AnalysisStep, StepKind


class AnalysisStepQueryPlanner:
    def plan(self, plan: AnalysisPlan, step: AnalysisStep, original: SemanticQueryIR, context) -> StepExecutionPlan:
        if not context.execution_mapping or not context.execution_mapping.mapped_assets:
            return StepExecutionPlan(analysis_plan_id=plan.plan_id,analysis_step_id=step.step_id,issues=(QueryPlanningIssue(code="MISSING_EXECUTION_MAPPING",message="published execution mapping is required"),))
        if step.kind in {StepKind.TREND,StepKind.BASELINE,StepKind.COMPARISON} and not context.time_dimensions:
            return StepExecutionPlan(analysis_plan_id=plan.plan_id,analysis_step_id=step.step_id,issues=(QueryPlanningIssue(code="MISSING_TIME_DIMENSION",message="published time dimension is required"),))
        dimensions=step.dimensions or ()
        if step.kind in {StepKind.BREAKDOWN,StepKind.CONTRIBUTION} and not dimensions:
            return StepExecutionPlan(analysis_plan_id=plan.plan_id,analysis_step_id=step.step_id,issues=(QueryPlanningIssue(code="MISSING_REQUIRED_DIMENSION",message="step requires dimensions"),))
        metrics=step.driver_asset_ids or (step.target_asset_id,)
        execution=[]
        units=dimensions if step.kind in {StepKind.CONTRIBUTION, StepKind.BREAKDOWN} else (context.time_dimensions[0].id if step.kind is StepKind.TREND else None,)
        for dimension in units:
            for metric in metrics:
                scope_predicates=[SemanticPredicate(dimension=OntologyReference(asset_id=s.dimension.asset_id,version=original.ontology_version),operator=s.requested.operator,value=s.requested.value) for s in context.grounding.scopes if s.dimension.asset_id]
                date_id=context.time_dimensions[0].id if original.time_scope and context.time_dimensions else None
                lowered=lower_explicit_time_scope(original.time_scope,date_dimension_id=date_id,version=original.ontology_version) if date_id else ()
                query=SemanticQueryIR(ontology_version=original.ontology_version,source_question=original.source_question,intent=original.intent,metrics=[MetricRequest(metric=OntologyReference(asset_id=metric,version=original.ontology_version))],dimensions=[DimensionRequest(dimension=OntologyReference(asset_id=dimension,version=original.ontology_version),time_grain=TimeGrain.MONTH if step.kind is StepKind.TREND else None)] if dimension else [],predicates=[*scope_predicates,*lowered],order_by=original.order_by,limit=original.limit,time_scope=original.time_scope,signal=original.signal,comparison=step.baseline or original.comparison,targets=original.targets)
                hint={"step_kind":step.kind.value,"baseline_source":step.baseline_source.value if step.baseline_source else ""}
                execution.append(ExecutionPlan(target=ExecutionTarget.SUPERSONIC,ontology_version=original.ontology_version,query=query,analysis_plan_id=plan.plan_id,analysis_step_id=f"{step.step_id}:current" if step.kind is StepKind.BASELINE else step.step_id,executor_hints=hint))
                if step.kind in {StepKind.BASELINE, StepKind.COMPARISON}:
                    baseline_scope = previous_year_scope(original.time_scope)
                    if step.kind is StepKind.COMPARISON and original.comparison is not None:
                        months = re.findall(r"\d{4}年\d{1,2}月", original.comparison.raw_text)
                        if len(months) >= 2:
                            baseline_scope = TimeScope(raw_text=months[1], kind=TimeScopeKind.EXPLICIT)
                    baseline=query.model_copy(update={"predicates":[*scope_predicates,*lower_explicit_time_scope(baseline_scope,date_dimension_id=date_id,version=original.ontology_version)],"time_scope":baseline_scope})
                    execution.append(ExecutionPlan(target=ExecutionTarget.SUPERSONIC,ontology_version=original.ontology_version,query=baseline,analysis_plan_id=plan.plan_id,analysis_step_id=f"{step.step_id}:baseline",executor_hints=hint))
        return StepExecutionPlan(analysis_plan_id=plan.plan_id,analysis_step_id=step.step_id,executions=tuple(execution))

"""Deterministic evidence-gap loop; it plans no SQL and makes no causal claims."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from agentbi.ontology_service import PlanningContext, RuntimeBinding
from agentbi.query_planner.contracts import ExecutionPlan
from agentbi.semantic_query_ir import (
    DimensionRequest,
    MetricRequest,
    OntologyReference,
)

from .observation import AnalysisState, ObservationBuilder, ObservationDirection


class NextActionType(StrEnum):
    EXECUTE_ANALYSIS = "EXECUTE_ANALYSIS"
    CLARIFY = "CLARIFY"
    STOP = "STOP"


class EvidenceKind(StrEnum):
    CONTRIBUTION = "CONTRIBUTION"
    DRIVER_MOVEMENT = "DRIVER_MOVEMENT"


class StopReason(StrEnum):
    MAX_ITERATIONS = "MAX_ITERATIONS"
    NO_NEW_ACTION = "NO_NEW_ACTION"
    FIRST_LEVEL_EVIDENCE_COMPLETE = "FIRST_LEVEL_EVIDENCE_COMPLETE"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    FATAL_PLANNING_ERROR = "FATAL_PLANNING_ERROR"


class NextAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action_type: NextActionType
    reason: str
    target_metric_id: str
    evidence_kind: EvidenceKind | None = None
    dimensions: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    stop_reason: StopReason | None = None

    @property
    def key(self) -> str:
        return ":".join((self.action_type.value, self.evidence_kind.value if self.evidence_kind else "", self.target_metric_id, *self.dimensions))


class AgentLoopTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    iteration: int
    evidence_gaps: tuple[str, ...] = ()
    actions: tuple[NextAction, ...] = ()
    execution_statuses: tuple[str, ...] = ()
    observation_ids: tuple[str, ...] = ()
    stop_reason: StopReason | None = None


class RuntimeCapabilityCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    metric_asset_ids: frozenset[str]
    dimension_asset_ids: frozenset[str]

    @classmethod
    def from_binding(cls, binding: RuntimeBinding) -> RuntimeCapabilityCoverage:
        return cls(metric_asset_ids=frozenset(item.asset_id for item in binding.metric_bindings),
                   dimension_asset_ids=frozenset(item.asset_id for item in binding.dimension_bindings))


class DeterministicNextActionPolicy:
    """Uses relations and published binding coverage, never metric names or source fields."""

    def __init__(self, coverage: RuntimeCapabilityCoverage): self._coverage = coverage

    def decide(self, *, context: PlanningContext, state: AnalysisState, attempted: frozenset[str] = frozenset(), fixed_dimension_ids: frozenset[str] = frozenset()) -> tuple[NextAction, ...]:
        target = context.target_asset
        if target is None:
            return (self._stop("published target metric is unresolved", "", StopReason.CLARIFICATION_REQUIRED),)
        comparison = next((item for item in state.comparisons if item.metric_asset_id == target.id and not item.dimensions), None)
        if comparison is None or not comparison.members:
            return (self._stop("current/baseline evidence is required", target.id, StopReason.CLARIFICATION_REQUIRED),)
        if comparison.members[0].direction not in {ObservationDirection.DOWN, ObservationDirection.UP}:
            return (self._stop("no directional movement requires first-level analysis", target.id, StopReason.FIRST_LEVEL_EVIDENCE_COMPLETE),)
        fixed = {scope.dimension.asset_id for scope in context.grounding.scopes if scope.dimension.asset_id} | set(fixed_dimension_ids)
        requested = {item.asset_id for item in context.grounding.breakdown_hints if item.asset_id}
        breakdown_dimensions = tuple(item for item in context.breakdown_dimensions if not requested or item.id in requested)
        actions: list[NextAction] = []
        for dimension in breakdown_dimensions:
            if dimension.id in fixed or dimension.id not in self._coverage.dimension_asset_ids:
                continue
            action = NextAction(action_type=NextActionType.EXECUTE_ANALYSIS, evidence_kind=EvidenceKind.CONTRIBUTION,
                                target_metric_id=target.id, dimensions=(dimension.id,),
                                reason="published BREAKDOWN_BY evidence is missing", required_evidence=("current_breakdown", "baseline_breakdown"),
                                provenance=(target.id, dimension.id, "BREAKDOWN_BY"))
            if not self._terminal(state, action) and action.key not in attempted: actions.append(action)
        for driver in (() if requested else context.drivers):
            if driver.id not in self._coverage.metric_asset_ids: continue
            action = NextAction(action_type=NextActionType.EXECUTE_ANALYSIS, evidence_kind=EvidenceKind.DRIVER_MOVEMENT,
                                target_metric_id=driver.id, reason="published DRIVEN_BY evidence is missing",
                                required_evidence=("current_driver", "baseline_driver"), provenance=(target.id, driver.id, "DRIVEN_BY"))
            if not self._terminal(state, action) and action.key not in attempted: actions.append(action)
        if actions: return tuple(actions)
        terminal = self._terminal_actions(context, state, fixed)
        return (self._stop("first-level evidence is terminal" if terminal else "no supported new action", target.id,
                           StopReason.FIRST_LEVEL_EVIDENCE_COMPLETE if terminal else StopReason.NO_NEW_ACTION),)

    def _terminal(self, state: AnalysisState, action: NextAction) -> bool:
        dimensions = action.dimensions
        return any(item.metric_asset_id == action.target_metric_id and item.dimensions == dimensions for item in state.comparisons)

    def _terminal_actions(self, context: PlanningContext, state: AnalysisState, fixed: set[str]) -> bool:
        requested = {item.asset_id for item in context.grounding.breakdown_hints if item.asset_id}
        dimensions = tuple(item for item in context.breakdown_dimensions if not requested or item.id in requested)
        candidates = [
            (context.target_asset.id, (item.id,)) for item in dimensions
            if item.id not in fixed and item.id in self._coverage.dimension_asset_ids
        ] + ([] if requested else [(item.id, ()) for item in context.drivers if item.id in self._coverage.metric_asset_ids])
        return bool(candidates) and all(any(comp.metric_asset_id == metric and comp.dimensions == dimensions for comp in state.comparisons) for metric, dimensions in candidates)

    @staticmethod
    def _stop(reason: str, metric: str, stop: StopReason) -> NextAction:
        return NextAction(action_type=NextActionType.STOP, target_metric_id=metric, reason=reason, stop_reason=stop)


class IncrementalActionPlanner:
    """Minimal QueryPlanner adapter: clone governed templates, never build backend requests."""

    @staticmethod
    def compile(action: NextAction, current: ExecutionPlan, baseline: ExecutionPlan) -> tuple[ExecutionPlan, ExecutionPlan]:
        def plan(template: ExecutionPlan, side: str) -> ExecutionPlan:
            query = template.query.model_copy(update={
                "metrics": [MetricRequest(metric=OntologyReference(asset_id=action.target_metric_id, version=template.ontology_version))],
                "dimensions": [DimensionRequest(dimension=OntologyReference(asset_id=item, version=template.ontology_version)) for item in action.dimensions],
            })
            return template.model_copy(update={"analysis_step_id": f"{action.key}:{side}", "query": query})
        return plan(current, "current"), plan(baseline, "baseline")


class RuntimeExecutor(Protocol):
    async def execute(self, plan: ExecutionPlan): ...


class DeterministicAgentLoop:
    def __init__(self, policy: DeterministicNextActionPolicy, runtime: RuntimeExecutor, *, max_iterations: int = 5):
        self._policy, self._runtime, self._max_iterations = policy, runtime, max_iterations

    async def run(self, *, context: PlanningContext, state: AnalysisState, current_template: ExecutionPlan, baseline_template: ExecutionPlan) -> tuple[AnalysisState, tuple[AgentLoopTrace, ...]]:
        traces: list[AgentLoopTrace] = []; attempted: set[str] = set()
        for iteration in range(1, self._max_iterations + 1):
            fixed = frozenset(item.dimension.asset_id for item in current_template.query.predicates)
            actions = self._policy.decide(context=context, state=state, attempted=frozenset(attempted), fixed_dimension_ids=fixed)
            gaps = tuple(action.key for action in actions if action.action_type is NextActionType.EXECUTE_ANALYSIS)
            if actions[0].action_type is not NextActionType.EXECUTE_ANALYSIS:
                traces.append(AgentLoopTrace(iteration=iteration, actions=actions, evidence_gaps=gaps, stop_reason=actions[0].stop_reason)); return state, tuple(traces)
            statuses: list[str] = []; observation_ids: list[str] = []
            for action in actions:
                attempted.add(action.key)
                try:
                    current, baseline = IncrementalActionPlanner.compile(action, current_template, baseline_template)
                    current_result, baseline_result = await self._runtime.execute(current), await self._runtime.execute(baseline)
                    current_observation, baseline_observation = ObservationBuilder.build(current, current_result), ObservationBuilder.build(baseline, baseline_result)
                    state.add(current_observation); state.add(baseline_observation); state.pair(current_observation.observation_id, baseline_observation.observation_id)
                    statuses.extend((current_result.status.value, baseline_result.status.value)); observation_ids.extend((current_observation.observation_id, baseline_observation.observation_id))
                except Exception:  # noqa: BLE001 - loop failures become governed evidence.
                    traces.append(AgentLoopTrace(iteration=iteration, actions=(action,), evidence_gaps=gaps, stop_reason=StopReason.FATAL_PLANNING_ERROR)); return state, tuple(traces)
            traces.append(AgentLoopTrace(iteration=iteration, actions=actions, evidence_gaps=gaps, execution_statuses=tuple(statuses), observation_ids=tuple(observation_ids)))
        traces.append(AgentLoopTrace(iteration=self._max_iterations, stop_reason=StopReason.MAX_ITERATIONS)); return state, tuple(traces)

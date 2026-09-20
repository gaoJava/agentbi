from __future__ import annotations

import asyncio

from agentbi.execution import (
    AnalysisState,
    DeterministicFallbackSynthesizer,
    EvidenceGroundedSynthesizer,
    EvidencePackageBuilder,
    ExecutionResult,
    ExecutionStatus,
    GroundingValidator,
    ObservationBuilder,
    StopReason,
)
from agentbi.query_planner import ExecutionPlan, ExecutionTarget
from agentbi.semantic_query_ir import (
    DimensionRequest,
    MetricRequest,
    OntologyReference,
    SemanticQueryIR,
)

V = "synthesis-test"


def _plan(metric, step, dimension=None):
    return ExecutionPlan(target=ExecutionTarget.SUPERSONIC, ontology_version=V, analysis_plan_id="analysis",
                         analysis_step_id=step, query=SemanticQueryIR(ontology_version=V, source_question="why",
                         metrics=[MetricRequest(metric=OntologyReference(asset_id=metric, version=V))],
                         dimensions=[DimensionRequest(dimension=OntologyReference(asset_id=dimension, version=V))] if dimension else []))


def _state():
    state = AnalysisState()
    def pair(metric, dimension, current_rows, baseline_rows):
        current, baseline = _plan(metric, f"{metric}-current", dimension), _plan(metric, f"{metric}-baseline", dimension)
        key = metric
        dims = [{"semantic_identifier": dimension}] if dimension else []
        def result(plan, rows): return ExecutionResult(status=ExecutionStatus.SUCCEEDED, analysis_plan_id="analysis", analysis_step_id=plan.analysis_step_id, rows=tuple(rows), row_count=len(rows), generated_sql="SELECT governed", evidence={"structured_semantic_request":{"snapshot_version":V,"binding_id":"binding","backend":"SUPERSONIC","semantic_model_id":13,"semantic_view_id":8,"metrics":[{"semantic_identifier":key}],"dimensions":dims}})
        a, b = ObservationBuilder.build(current, result(current, current_rows)), ObservationBuilder.build(baseline, result(baseline, baseline_rows)); state.add(a); state.add(b); state.pair(a.observation_id,b.observation_id)
    pair("metric.revenue", None, [{"metric.revenue":100}], [{"metric.revenue":120}])
    pair("metric.revenue", "dimension.product", [{"dimension.product":"P1","metric.revenue":10},{"dimension.product":"P2","metric.revenue":20}], [{"dimension.product":"P1","metric.revenue":20},{"dimension.product":"P2","metric.revenue":5}])
    pair("metric.revenue", "dimension.customer", [{"dimension.customer":"C1","metric.revenue":15}], [{"dimension.customer":"C1","metric.revenue":30}])
    pair("metric.customer_count", None, [{"metric.customer_count":24}], [{"metric.customer_count":24}])
    pair("metric.purchase_frequency", None, [{"metric.purchase_frequency":"1.0"}], [{"metric.purchase_frequency":"1.1"}])
    pair("metric.average_order_value", None, [{"metric.average_order_value":30}], [{"metric.average_order_value":40}])
    return state


def test_package_ranks_delta_and_carries_drivers_and_provenance():
    package = EvidencePackageBuilder(top_n=3).build(_state(), target_metric_id="metric.revenue", stop_reason=StopReason.FIRST_LEVEL_EVIDENCE_COMPLETE)
    product = next(item for item in package.breakdowns if item.dimension_asset_id == "dimension.product")
    assert package.primary_metric.delta == -20 and product.ranking_basis == "DELTA"
    assert product.negative_contributors[0].member == ("P1",) and product.negative_contributors[0].delta == -10
    assert product.positive_offsets[0].member == ("P2",) and product.positive_offsets[0].delta == 15
    assert product.negative_contributors[0].share_of_total_decline == 0.5
    assert {item.metric_asset_id for item in package.driver_movements} == {"metric.customer_count","metric.purchase_frequency","metric.average_order_value"}
    assert package.primary_metric.evidence.observation_ids


def test_grounding_validator_rejects_number_member_channel_and_scope_drift():
    package = EvidencePackageBuilder().build(_state(), target_metric_id="metric.revenue", stop_reason=StopReason.FIRST_LEVEL_EVIDENCE_COMPLETE)
    answer = DeterministicFallbackSynthesizer().synthesize(package)
    valid, errors = GroundingValidator().validate(answer, package)
    assert valid and not errors
    finding = answer.key_findings[0]
    unknown_number = answer.model_copy(update={"key_findings": (finding.model_copy(update={"numeric_values": ("999999",)}),)})
    unknown_member = answer.model_copy(update={"breakdown_findings": (answer.breakdown_findings[0].model_copy(update={"mentioned_members": ("UNKNOWN",)}),)})
    channel = answer.model_copy(update={"summary": "Channel caused decline"})
    drift = answer.model_copy(update={"scope_filters": ({"changed":True},)})
    assert not GroundingValidator().validate(unknown_number, package)[0]
    assert not GroundingValidator().validate(unknown_member, package)[0]
    assert not GroundingValidator().validate(channel, package)[0]
    assert not GroundingValidator().validate(drift, package)[0]


def test_unavailable_evidence_and_llm_failure_use_grounded_fallback():
    package = EvidencePackageBuilder().build(_state(), target_metric_id="metric.revenue", stop_reason=StopReason.FIRST_LEVEL_EVIDENCE_COMPLETE)
    class BrokenNarrator:
        async def synthesize(self, _): raise RuntimeError("unavailable")
    answer, errors = asyncio.run(EvidenceGroundedSynthesizer(BrokenNarrator()).synthesize(package))
    assert answer.synthesis_mode == "DETERMINISTIC_FALLBACK" and errors == ("LLM_UNAVAILABLE",)
    valid, _ = GroundingValidator().validate(answer, package)
    assert valid and set(answer.evidence_refs)

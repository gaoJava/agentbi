from __future__ import annotations

import asyncio

from sqlalchemy import create_engine

from agentbi.analysis_planner import AnalysisStepQueryPlanner, DeterministicAnalysisPlanner
from agentbi.config import Settings
from agentbi.execution import (
    AnalysisState,
    DeterministicAgentLoop,
    DeterministicNextActionPolicy,
    EvidenceGroundedSynthesizer,
    EvidencePackageBuilder,
    ExecutionRuntime,
    ObservationBuilder,
    RuntimeCapabilityCoverage,
    StructuredSemanticRequestBuilder,
)
from agentbi.ontology_service import (
    OntologyService,
    PlanningContextBuilder,
    PostgresOntologyRepository,
    RuntimeBindingResolver,
)
from agentbi.semantic_parser import ParseRequest, RuleBasedSemanticParser
from agentbi.supersonic import SuperSonicClient


async def main():
    settings=Settings.from_env(); service=OntologyService(PostgresOntologyRepository(create_engine(settings.database_url)))
    snapshot=await service.published_snapshot(); parsed=await RuleBasedSemanticParser(service).parse(ParseRequest(question="华东区2025年1月收入为什么下降？",ontology_version=snapshot.version)); assert parsed.query
    context=await PlanningContextBuilder(service).build(parsed.query); planned=await DeterministicAnalysisPlanner().plan(parsed.query,context); assert planned.plan
    steps=[AnalysisStepQueryPlanner().plan(planned.plan,step,parsed.query,context) for step in planned.plan.steps]
    initial=next(step for step in steps if step.analysis_step_id=="step-1").executions
    current,baseline=initial
    client=SuperSonicClient(settings); runtime=ExecutionRuntime(client,structured_request_builder=StructuredSemanticRequestBuilder(RuntimeBindingResolver(service)))
    cr,br=await runtime.execute(current),await runtime.execute(baseline); state=AnalysisState(); co=state.add(ObservationBuilder.build(current,cr)); bo=state.add(ObservationBuilder.build(baseline,br)); state.pair(co.observation_id,bo.observation_id)
    loop=DeterministicAgentLoop(DeterministicNextActionPolicy(RuntimeCapabilityCoverage.from_binding(snapshot.runtime_bindings[0])),runtime)
    state,traces=await loop.run(context=context,state=state,current_template=current,baseline_template=baseline)
    package=EvidencePackageBuilder().build(state,target_metric_id="metric.revenue",stop_reason=traces[-1].stop_reason); answer,errors=await EvidenceGroundedSynthesizer().synthesize(package)
    print("CURRENT",cr.rows,"BASELINE",br.rows,"STOP",traces[-1].stop_reason,"COMPARISONS",[(x.metric_asset_id,x.dimensions) for x in state.comparisons],"SYNTH",answer.synthesis_mode,errors)
    await client.close()
asyncio.run(main())

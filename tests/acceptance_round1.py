"""Read-only End-to-End Product Acceptance Round 1 harness.

This is deliberately not a product entry point and contains no question-to-asset
mapping.  Each case starts with the production semantic parser and only advances
through existing production contracts when the preceding stage succeeds.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import create_engine

from agentbi.analysis_planner import AnalysisStepQueryPlanner, DeterministicAnalysisPlanner
from agentbi.config import Settings
from agentbi.execution import ExecutionRuntime, ObservationBuilder, StructuredSemanticRequestBuilder
from agentbi.ontology_service import (
    OntologyService,
    PlanningContextBuilder,
    PostgresOntologyRepository,
    RuntimeBindingResolver,
)
from agentbi.semantic_parser import ParseRequest, RuleBasedSemanticParser
from agentbi.supersonic import SuperSonicClient

CASES = (
    "华东区2025年1月收入为什么下降？",
    "华北区2025年1月收入为什么变化？",
    "2025年1月哪个产品收入最高？",
    "2025年1月各区域收入是多少？",
    "2025年1月收入是多少？",
    "2025年1月购买频次是多少？",
    "2025年每月收入趋势如何？",
    "华东区2025年1月客户数是多少？",
    "华东区2025年1月客单价是多少？",
    "华东区2025年1月收入按产品拆分",
    "华东区2025年1月收入按渠道拆分",
    "华东区2025年1月收入下降最多的客户有哪些？",
    "华东区2025年1月收入下降主要受哪些因素影响？",
    "2025年1月收入比2024年1月变化多少？",
    "华东区收入怎么样？",
)


@dataclass
class CaseRecord:
    case_id: str
    question: str
    parser_result: dict[str, Any]
    planning_result: dict[str, Any] | None = None
    execution_result: dict[str, Any] | None = None
    observation_result: dict[str, Any] | None = None
    agent_loop_result: str = "NOT_REACHED"
    synthesis_result: str = "NOT_REACHED"
    final_answer: str = "NOT_AVAILABLE"
    status: str = "PARTIAL"
    first_failure_stage: str | None = None
    failure_class: str | None = None


def _compact(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _compact(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_compact(v) for v in value]
    return value


async def run() -> list[CaseRecord]:
    settings = Settings.from_env()
    repository = PostgresOntologyRepository(create_engine(settings.database_url))
    ontology = OntologyService(repository)
    try:
        snapshot = await ontology.published_snapshot()
    except Exception as exc:  # noqa: BLE001 - acceptance harness records environment failures.
        reason = f"{type(exc).__name__}: {exc}"
        return [CaseRecord(
            case_id=f"CASE {number:02d}", question=question,
            parser_result={"status": "NOT_REACHED", "reason": reason},
            planning_result={"status": "NOT_REACHED"},
            execution_result={"status": "NOT_REACHED"},
            observation_result={"status": "NOT_REACHED"},
            status="PARTIAL", first_failure_stage="Ontology",
            failure_class="PRODUCT_INTEGRATION_GAP",
            final_answer="NOT_RETURNED: published RuntimeBinding snapshot cannot be read",
        ) for number, question in enumerate(CASES, 1)]
    if snapshot is None:
        return [CaseRecord(
            case_id=f"CASE {number:02d}", question=question,
            parser_result={"status": "NOT_REACHED", "reason": "no published ontology"},
            planning_result={"status": "NOT_REACHED"},
            execution_result={"status": "NOT_REACHED"},
            observation_result={"status": "NOT_REACHED"},
            status="PARTIAL", first_failure_stage="Ontology",
            failure_class="PRODUCT_INTEGRATION_GAP",
            final_answer="NOT_RETURNED: no published ontology snapshot",
        ) for number, question in enumerate(CASES, 1)]

    parser = RuleBasedSemanticParser(ontology)
    context_builder = PlanningContextBuilder(ontology)
    planner = DeterministicAnalysisPlanner()
    bridge = AnalysisStepQueryPlanner()
    runtime = ExecutionRuntime(
        SuperSonicClient(settings),
        structured_request_builder=StructuredSemanticRequestBuilder(
            RuntimeBindingResolver(ontology)
        ),
    )
    records: list[CaseRecord] = []
    try:
        for number, question in enumerate(CASES, 1):
            parsed = await parser.parse(ParseRequest(
                question=question, ontology_version=snapshot.version
            ))
            record = CaseRecord(
                case_id=f"CASE {number:02d}", question=question,
                parser_result=_compact(parsed),
            )
            if parsed.clarification is not None:
                record.status = "CLARIFICATION"
                record.first_failure_stage = "Semantic Parser"
                record.failure_class = "PARSER_GAP"
                record.final_answer = parsed.clarification.reason
                records.append(record)
                continue
            assert parsed.query is not None
            context = await context_builder.build(parsed.query)
            plan_result = await planner.plan(parsed.query, context)
            record.planning_result = _compact(plan_result)
            if plan_result.plan is None:
                record.first_failure_stage = "AnalysisPlanner"
                record.failure_class = "PLANNER_GAP"
                records.append(record)
                continue

            step_plans = tuple(
                bridge.plan(plan_result.plan, step, parsed.query, context)
                for step in plan_result.plan.steps
            )
            record.planning_result["step_execution_plans"] = _compact(step_plans)
            plans = tuple(item for step in step_plans for item in step.executions)
            issues = tuple(issue for step in step_plans for issue in step.issues)
            if issues or not plans:
                record.first_failure_stage = "QueryPlanner"
                record.failure_class = "PLANNER_GAP"
                records.append(record)
                continue

            results = tuple([await runtime.execute(plan) for plan in plans])
            record.execution_result = _compact(results)
            observations = tuple(
                ObservationBuilder.build(plan, result)
                for plan, result in zip(plans, results, strict=True)
            )
            record.observation_result = _compact(observations)
            if any(result.status.value != "SUCCEEDED" for result in results):
                record.first_failure_stage = "ExecutionRuntime"
                record.failure_class = "EXECUTION_GAP"
                records.append(record)
                continue

            # This is an acceptance finding, not a generated answer: the
            # production composition from natural query to loop/synthesis does
            # not exist yet, and raw explicit time scopes are absent from the
            # emitted predicate set.
            predicates = tuple(
                predicate for plan in plans for predicate in plan.query.predicates
            )
            if parsed.query.time_scope is not None and not predicates:
                record.status = "INCORRECT"
                record.first_failure_stage = "QueryPlanner"
                record.failure_class = "PRODUCT_INTEGRATION_GAP"
                record.final_answer = "NOT_RETURNED: parsed time scope was not lowered to governed predicates"
            else:
                record.first_failure_stage = "AgentLoop"
                record.failure_class = "PRODUCT_INTEGRATION_GAP"
            records.append(record)
    finally:
        await runtime._supersonic.close()  # harness-owned adapter lifecycle only
    return records


if __name__ == "__main__":
    output = asyncio.run(run())
    print(json.dumps([asdict(item) for item in output], ensure_ascii=False, indent=2, default=str))

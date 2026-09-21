"""Deterministic AgentBI workflow with evidence construction and bounded output."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
from urllib.parse import quote

from agentbi.analysis_planner import AnalysisStepQueryPlanner, DeterministicAnalysisPlanner
from agentbi.config import Settings
from agentbi.execution import (
    AnalysisState,
    DeterministicAgentLoop,
    DeterministicNextActionPolicy,
    EvidenceGroundedSynthesizer,
    EvidencePackageBuilder,
    ExecutionRuntime,
    GroundingValidator,
    ObservationBuilder,
    ObservationStatus,
    RuntimeCapabilityCoverage,
)
from agentbi.models import (
    AnalysisReport,
    AnalysisStep,
    AnalyzeRequest,
    AnalyzeResponse,
    Evidence,
    StepStatus,
)
from agentbi.ontology_service import OntologyService, PlanningContextBuilder
from agentbi.security import SlidingWindowRateLimiter, enforce_request_policy, sql_fingerprint
from agentbi.semantic_parser import ParseRequest, RuleBasedSemanticParser
from agentbi.supersonic import SuperSonicClient


class GovernedAnalysisError(RuntimeError):
    """A user-visible governed refusal; it is never eligible for legacy retry."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        supersonic: SuperSonicClient,
        *,
        ontology: OntologyService | None = None,
        parser: RuleBasedSemanticParser | None = None,
        context_builder: PlanningContextBuilder | None = None,
        planner: DeterministicAnalysisPlanner | None = None,
        bridge: AnalysisStepQueryPlanner | None = None,
        runtime: ExecutionRuntime | None = None,
    ):
        self._settings = settings
        self._supersonic = supersonic
        self._rate_limiter = SlidingWindowRateLimiter(settings.requests_per_minute)
        self._idempotency_lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task[AnalyzeResponse]] = {}
        self._completed: dict[str, tuple[float, AnalyzeResponse]] = {}
        # All dependencies are supplied by the application factory.  Keeping
        # this explicit makes an incomplete migration fail closed rather than
        # falling through to the old free-form SuperSonic query endpoint.
        self._ontology = ontology
        self._parser = parser
        self._context_builder = context_builder
        self._planner = planner
        self._bridge = bridge
        self._runtime = runtime

    async def analyze(self, request: AnalyzeRequest) -> AnalyzeResponse:
        if not request.client_request_id:
            return await self._execute(request)

        key = f"{request.actor.subject}:{request.client_request_id}"
        now = time.monotonic()
        async with self._idempotency_lock:
            self._prune_completed(now)
            cached = self._completed.get(key)
            if cached:
                return cached[1].model_copy(deep=True)
            task = self._inflight.get(key)
            owner = task is None
            if task is None:
                task = asyncio.create_task(self._execute(request))
                self._inflight[key] = task

        try:
            result = await task
        except Exception:
            if owner:
                async with self._idempotency_lock:
                    self._inflight.pop(key, None)
            raise
        if owner:
            async with self._idempotency_lock:
                self._inflight.pop(key, None)
                self._completed[key] = (
                    time.monotonic() + self._settings.idempotency_ttl_seconds,
                    result.model_copy(deep=True),
                )
                while len(self._completed) > 1000:
                    self._completed.pop(next(iter(self._completed)))
        return result

    async def _execute(self, request: AnalyzeRequest) -> AnalyzeResponse:
        request_id = str(uuid.uuid4())
        steps: list[AnalysisStep] = []

        started = time.perf_counter()
        enforce_request_policy(request)
        self._rate_limiter.enforce(request.actor.subject)
        steps.append(self._step("authorize", started, "request policy accepted"))

        return await self._execute_governed(request, request_id, steps)

    async def _execute_governed(
        self, request: AnalyzeRequest, request_id: str, steps: list[AnalysisStep]
    ) -> AnalyzeResponse:
        if not all((self._ontology, self._parser, self._context_builder, self._planner, self._bridge, self._runtime)):
            raise GovernedAnalysisError("EXECUTION_FAILED", "governed analysis composition is not configured")
        snapshot = await self._ontology.published_snapshot()
        if snapshot is None:
            raise GovernedAnalysisError("EXECUTION_FAILED", "no published ontology snapshot is available")
        started = time.perf_counter()
        parsed = await self._parser.parse(ParseRequest(
            question=request.question, ontology_version=snapshot.version,
            dashboard_id=request.context.dashboard_id, actor_id=request.actor.subject,
            dashboard_context=request.dashboard_context,
        ))
        steps.append(self._step("semantic_parser", started, f"snapshot={snapshot.version}"))
        if parsed.clarification is not None:
            raise GovernedAnalysisError("CLARIFICATION_REQUIRED", parsed.clarification.reason)
        assert parsed.query is not None

        started = time.perf_counter()
        context = await self._context_builder.build(parsed.query)
        planning = await self._planner.plan(parsed.query, context)
        steps.append(self._step("analysis_planner", started, f"intent={parsed.query.intent.value}"))
        if planning.plan is None:
            issue = planning.issues[0] if planning.issues else None
            code = "UNSUPPORTED_RUNTIME_CAPABILITY" if issue and issue.code.value == "MISSING_CAPABILITY" else "CLARIFICATION_REQUIRED"
            raise GovernedAnalysisError(code, issue.message if issue else "no governed analysis plan")

        step_plans = tuple(self._bridge.plan(planning.plan, step, parsed.query, context) for step in planning.plan.steps)
        bridge_issues = tuple(issue for item in step_plans for issue in item.issues)
        if bridge_issues:
            raise GovernedAnalysisError("EXECUTION_FAILED", bridge_issues[0].message)

        # Root-cause plans deliberately execute only their current/baseline
        # seed.  The deterministic loop owns all subsequent governed actions.
        initial_steps = step_plans[:1] if parsed.query.intent.value == "ROOT_CAUSE" else step_plans
        executions = tuple(plan for item in initial_steps for plan in item.executions)
        if not executions:
            raise GovernedAnalysisError("EXECUTION_FAILED", "query planner emitted no execution plan")
        started = time.perf_counter()
        results = tuple([await self._runtime.execute(plan) for plan in executions])
        observations = tuple(ObservationBuilder.build(plan, result) for plan, result in zip(executions, results, strict=True))
        steps.append(self._step("governed_execution", started, f"executions={len(results)}"))
        self._raise_for_observations(observations)

        state = AnalysisState()
        for observation in observations:
            state.add(observation)
        if len(observations) == 2 and observations[0].metric_asset_id == observations[1].metric_asset_id and observations[0].dimensions == observations[1].dimensions:
            state.pair(observations[0].observation_id, observations[1].observation_id)

        answer: str
        if parsed.query.intent.value == "ROOT_CAUSE":
            if not state.comparisons:
                raise GovernedAnalysisError("EXECUTION_FAILED", "root-cause seed comparison was not produced")
            binding = snapshot.runtime_bindings[0] if snapshot.runtime_bindings else None
            if binding is None:
                raise GovernedAnalysisError("EXECUTION_FAILED", "published runtime binding is unavailable")
            started = time.perf_counter()
            loop = DeterministicAgentLoop(
                DeterministicNextActionPolicy(RuntimeCapabilityCoverage.from_binding(binding)), self._runtime
            )
            state, loop_trace = await loop.run(
                context=context, state=state,
                current_template=executions[0], baseline_template=executions[1],
            )
            stop_reason = loop_trace[-1].stop_reason if loop_trace else None
            steps.append(self._step("agent_loop", started, f"stop_reason={stop_reason.value if stop_reason else 'NONE'}"))
            self._raise_for_observations(tuple(state.unavailable()))
            package = EvidencePackageBuilder().build(
                state, target_metric_id=planning.plan.target_asset_id, stop_reason=stop_reason
            )
            final, synthesis_errors = await EvidenceGroundedSynthesizer().synthesize(package)
            valid, grounding_errors = GroundingValidator().validate(final, package)
            if not valid:
                raise GovernedAnalysisError("EXECUTION_FAILED", f"grounding validation failed: {', '.join(grounding_errors)}")
            answer = "\n".join((final.summary, *(item.text for item in final.breakdown_findings), *(item.text for item in final.driver_findings)))
            steps.append(self._step("synthesis", time.perf_counter(), f"mode={final.synthesis_mode}; grounding_errors=none; synthesis_errors={','.join(synthesis_errors) or 'none'}"))
        elif parsed.query.intent.value == "COMPARISON":
            if not state.comparisons:
                raise GovernedAnalysisError("EXECUTION_FAILED", "explicit comparison did not produce a paired observation")
            comparison = state.comparisons[0]
            member = comparison.members[0] if comparison.members else None
            if member is None or member.current is None or member.baseline is None or member.delta is None:
                raise GovernedAnalysisError("EXECUTION_FAILED", "explicit comparison evidence is incomplete")
            pct = member.delta_pct
            answer = f"当前期 {member.current}，对比期 {member.baseline}，变化 {member.delta}（{pct}）。"
            steps.append(self._step("synthesis", time.perf_counter(), "mode=DETERMINISTIC_COMPARISON; provenance=USER_SPECIFIED"))
        else:
            answer = self._governed_answer(observations)

        rows = [dict(row) for result in results for row in result.rows][:self._settings.max_result_rows]
        first = results[0]
        evidence = Evidence(query_id=first.query_id or request_id, semantic_model_id=request.context.semantic_model_id,
                            question=request.question, time_range=parsed.query.time_scope.raw_text if parsed.query.time_scope else request.context.time_range,
                            filters=request.context.filters, row_count=len(rows), query_time_ms=sum(result.duration_ms for result in results),
                            sql_fingerprint=sql_fingerprint(first.generated_sql), generated_sql=self._safe_generated_sql(first.generated_sql))
        steps.append(self._step("validate_evidence", time.perf_counter(), f"snapshot={snapshot.version}; rows={len(rows)}"))
        return AnalyzeResponse(request_id=request_id, chat_id=None, answer=answer, data=rows, evidence=evidence,
                               report=self._build_report(answer, rows, evidence, request.context.dashboard_id), steps=steps,
                               warnings=["GOVERNED_CORE_IS_PRIMARY=YES", f"SNAPSHOT={snapshot.version}"])

    @staticmethod
    def _raise_for_observations(observations) -> None:
        for observation in observations:
            if observation.status is ObservationStatus.UNSUPPORTED:
                raise GovernedAnalysisError("UNSUPPORTED_RUNTIME_CAPABILITY", observation.provenance.error_message or "published runtime capability is unsupported")
            if observation.status is ObservationStatus.NO_DATA:
                raise GovernedAnalysisError("NO_DATA", "the governed query returned no data")
            if observation.status is ObservationStatus.EXECUTION_FAILED:
                raise GovernedAnalysisError("EXECUTION_FAILED", observation.provenance.error_message or "governed execution failed")

    @staticmethod
    def _governed_answer(observations) -> str:
        observation = observations[0]
        if observation.dimensions:
            return f"已完成受治理拆分，返回 {len(observation.values)} 个数据桶。"
        value = observation.values[0].value if observation.values else None
        return f"受治理查询结果：{value}。"

    def _prune_completed(self, now: float) -> None:
        expired = [key for key, (expires_at, _) in self._completed.items() if expires_at <= now]
        for key in expired:
            self._completed.pop(key, None)

    @staticmethod
    def _step(name: str, started: float, detail: str) -> AnalysisStep:
        return AnalysisStep(
            name=name,
            status=StepStatus.COMPLETED,
            duration_ms=max(0, round((time.perf_counter() - started) * 1000)),
            detail=detail,
        )

    @staticmethod
    def _extract_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
        rows = result.get("queryResults") or []
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return []
        return rows

    @staticmethod
    def _extract_answer(result: dict[str, Any], row_count: int) -> str:
        response = result.get("response")
        if isinstance(response, str) and response.strip():
            return response.strip()
        return f"语义查询已完成，共返回 {row_count} 行可验证数据。"

    @staticmethod
    def _non_negative_int(value: Any) -> int | None:
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
        return None

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return None

    @staticmethod
    def _safe_generated_sql(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        compact = " ".join(value.split())
        if not compact.lower().startswith(("select ", "with ")):
            return None
        return compact[:2000]

    @classmethod
    def _semantic_detail(cls, original_question: str, result: dict[str, Any]) -> str:
        resolved = str(result.get("resolvedQuestion") or original_question).strip()
        mode = str(result.get("resolutionMode") or "SuperSonic 语义解析").strip()
        sql = cls._safe_generated_sql(result.get("querySql")) or "上游未返回可展示 SQL"
        plan = result.get("analysisPlan")
        plan_detail = ""
        if isinstance(plan, dict):
            assumptions = "；".join(str(item) for item in plan.get("assumptions", [])[:5]) or "无"
            plan_detail = (f"\n分析计划：维度={plan.get('dimension') or '—'}；指标={plan.get('metric') or '—'}；"
                           f"运算={plan.get('operation') or '—'}；置信度={plan.get('confidence', 0):.0%}"
                           f"\n默认假设：{assumptions}")
        return f"解析方式：{mode}\n原始问题：{original_question}\n规范化问题：{resolved}{plan_detail}\n生成 SQL：{sql}"

    def _build_report(
        self,
        answer: str,
        rows: list[dict[str, Any]],
        evidence: Evidence,
        dashboard_id: str,
    ) -> AnalysisReport:
        """Build a bounded, portable report without trusting browser-supplied conclusions."""

        safe_answer = self._markdown_text(answer, 1000)
        range_label = self._markdown_text(evidence.time_range, 128) or "全量数据"
        observations = [
            safe_answer,
            (
                f"在“{range_label}”范围内返回 "
                f"{evidence.row_count} 行受治理数据。"
            ),
        ]
        columns = list(rows[0])[:6] if rows else []
        dimension = next(
            (column for column in columns if "date" not in column.lower() and "time" not in column.lower()),
            None,
        )
        suggested_actions = [
            (
                f"继续按 {self._markdown_text(dimension, 64)} 维度下钻，并与相邻时间段对比。"
                if dimension
                else "选择关键业务维度继续下钻，并与相邻时间段对比。"
            ),
            "对异常点发起追问，确认口径、筛选条件与业务事件是否一致。",
        ]
        title = f"AgentBI 分析报告 · {self._markdown_text(evidence.question, 80)}"
        source_url = self._dashboard_url(dashboard_id)
        lines = [
            f"# {title}",
            "",
            "## 结论摘要",
            "",
            safe_answer,
            "",
            "## 关键观察",
            "",
            *[f"- {item}" for item in observations],
            "",
            "## 建议动作",
            "",
            *[f"- {item}" for item in suggested_actions],
            "",
            "## 数据证据",
            "",
            f"- 查询编号：`{self._markdown_text(evidence.query_id, 128)}`",
            f"- 语义模型：`{evidence.semantic_model_id}`",
            f"- 数据范围：{range_label}",
            f"- 返回行数：{evidence.row_count}",
            f"- 查询耗时：{evidence.query_time_ms if evidence.query_time_ms is not None else '—'} ms",
            f"- SQL 指纹：`{evidence.sql_fingerprint or '—'}`",
            f"- 生成时间：{evidence.generated_at.isoformat()}",
            f"- 来源大屏：[返回 Superset 仪表盘]({source_url})" if source_url else "",
            "",
            "> 报告由受治理语义查询结果自动生成；关键决策前请结合业务口径复核。",
        ]
        return AnalysisReport(
            title=title,
            source_url=source_url,
            summary=safe_answer,
            observations=observations,
            suggested_actions=suggested_actions,
            markdown="\n".join(lines),
        )

    def _dashboard_url(self, dashboard_id: str) -> str | None:
        """Return an administrator-controlled Superset backlink for portable reports."""

        if not self._settings.allowed_origins:
            return None
        origin = self._settings.allowed_origins[0].rstrip("/")
        return f"{origin}/superset/dashboard/{quote(dashboard_id, safe='')}"

    @staticmethod
    def _markdown_text(value: Any, limit: int) -> str:
        """Neutralize HTML/Markdown control characters in report prose."""

        text = " ".join(str(value).split())[:limit]
        return (
            text.replace("\\", "\\\\")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("`", "\\`")
        )

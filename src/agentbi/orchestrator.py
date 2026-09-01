"""Deterministic AgentBI workflow with evidence construction and bounded output."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any
from urllib.parse import quote

from agentbi.config import Settings
from agentbi.models import (
    AnalysisReport,
    AnalysisStep,
    AnalyzeRequest,
    AnalyzeResponse,
    Evidence,
    StepStatus,
)
from agentbi.security import SlidingWindowRateLimiter, enforce_request_policy, sql_fingerprint
from agentbi.supersonic import SuperSonicClient


class Orchestrator:
    def __init__(self, settings: Settings, supersonic: SuperSonicClient):
        self._settings = settings
        self._supersonic = supersonic
        self._rate_limiter = SlidingWindowRateLimiter(settings.requests_per_minute)
        self._idempotency_lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Task[AnalyzeResponse]] = {}
        self._completed: dict[str, tuple[float, AnalyzeResponse]] = {}

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

        started = time.perf_counter()
        result = await self._supersonic.query(request)
        semantic_detail = self._semantic_detail(request.question, result)
        steps.append(self._step("semantic_query", started, semantic_detail))
        calculation_detail = result.get("calculationDetail")
        if isinstance(calculation_detail, str) and calculation_detail.strip():
            steps.append(self._step("calculate", time.perf_counter(), calculation_detail.strip()))

        started = time.perf_counter()
        rows = self._extract_rows(result)
        warnings: list[str] = []
        if len(rows) > self._settings.max_result_rows:
            rows = rows[: self._settings.max_result_rows]
            warnings.append("result was truncated by the server-side row limit")
        evidence = Evidence(
            query_id=str(result.get("queryId") or request_id),
            semantic_model_id=request.context.semantic_model_id,
            question=request.question,
            time_range=str(result.get("effectiveTimeRange") or request.context.time_range),
            filters=request.context.filters,
            row_count=len(rows),
            query_time_ms=self._non_negative_int(result.get("queryTimeCost")),
            sql_fingerprint=sql_fingerprint(result.get("querySql")),
            generated_sql=self._safe_generated_sql(result.get("querySql")),
        )
        steps.append(self._step(
            "validate_evidence",
            started,
            f"查询编号：{evidence.query_id}\n返回并校验：{len(rows)} 行\nSQL 指纹：{evidence.sql_fingerprint or '—'}",
        ))
        answer = self._extract_answer(result, len(rows))

        return AnalyzeResponse(
            request_id=request_id,
            chat_id=self._positive_int(result.get("chatId")),
            answer=answer,
            data=rows,
            evidence=evidence,
            report=self._build_report(answer, rows, evidence, request.context.dashboard_id),
            steps=steps,
            warnings=warnings,
        )

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

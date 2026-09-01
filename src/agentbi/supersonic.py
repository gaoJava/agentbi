"""Narrow, timeout-bound adapter for SuperSonic's semantic query API."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from urllib.parse import quote

import httpx

from agentbi.config import Settings
from agentbi.conversation import ConversationContextManager
from agentbi.database import IdentityRepository
from agentbi.models import AnalyzeRequest


class UpstreamError(RuntimeError):
    """A sanitized SuperSonic integration failure."""


class SemanticResolutionError(UpstreamError):
    """The service is healthy, but the question lacks a resolvable metric."""


class ResultMismatchError(UpstreamError):
    """The upstream query completed but returned fields unrelated to the question."""


class SuperSonicClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        repository: IdentityRepository | None = None,
    ):
        self._settings = settings
        headers = {"Accept": "application/json"}
        if settings.supersonic_token:
            headers["Authorization"] = settings.supersonic_token
        self._client = httpx.AsyncClient(
            base_url=settings.supersonic_base_url,
            headers=headers,
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            transport=transport,
            # Local governed-query traffic must not be intercepted by desktop or
            # corporate proxy environment variables. This matches SupersetClient.
            trust_env=False,
        )
        # The inspected SuperSonic build resolves governed metric candidates reliably
        # through its stateless chat (id 0), while newly persisted chats can be captured
        # by WEB_PAGE plugins. AgentBI therefore owns tenant-bound conversation history
        # and serializes access to that upstream stateless context.
        self._query_lock = asyncio.Lock()
        self._llm_resolver: Callable[[str], Awaitable[str | None]] | None = None
        if repository is None:
            repository = IdentityRepository("sqlite:///:memory:")
            repository.initialize(seed_demo_accounts=False, user_password="", admin_password="")
        self._conversations = ConversationContextManager(settings, repository)

    async def close(self) -> None:
        await self._client.aclose()

    def configure_llm_resolver(
        self, resolver: Callable[[str], Awaitable[str | None]] | None
    ) -> None:
        self._llm_resolver = resolver

    async def query(self, request: AnalyzeRequest) -> dict[str, Any]:
        """Parse the question, select the best semantic parse, then execute it."""

        conversation_id, summary, history = self._conversation(request)
        resolved_question = request.question
        resolution_mode = "SuperSonic 规则解析"
        calculation = self._calculation_query(resolved_question)
        ranked_query = self._ranking_query(resolved_question)
        structured = self._calculation_structure(calculation) or ranked_query or self._grouped_metric_query(request.question)
        if not structured and self._llm_resolver is not None:
            normalized = await self._llm_resolver(request.question)
            if normalized:
                resolved_question = normalized
                resolution_mode = "LLM 语义增强后交由 SuperSonic 执行"
                calculation = self._calculation_query(resolved_question)
                ranked_query = self._ranking_query(resolved_question)
                structured = self._calculation_structure(calculation) or ranked_query or self._grouped_metric_query(resolved_question)
        if structured:
            dimension, metric, limit = structured
            sort_direction = "ASC" if calculation and calculation.get("sort") == "asc" else "DESC"
            model_biz_name = await self._view_model_biz_name(
                request.context.semantic_model_id
            )
            started = time.perf_counter()
            data = await self._request_data(
                "POST",
                "/api/semantic/query/sql",
                payload={
                    "viewId": request.context.semantic_model_id,
                    "sql": (
                        f"SELECT {dimension}, SUM({metric}) AS {metric} "
                        f"FROM {model_biz_name} "
                        f"GROUP BY {dimension} ORDER BY {metric} {sort_direction} LIMIT {limit}"
                    ),
                },
            )
            if not isinstance(data, dict) or not isinstance(data.get("resultList"), list):
                raise UpstreamError("SuperSonic returned an invalid structured query result")
            dimension_label = self._field_label(dimension)
            metric_label = self._field_label(metric)
            raw_rows = [row for row in data["resultList"] if isinstance(row, dict)]
            rows = [
                {dimension_label: row.get(dimension), metric_label: row.get(metric)}
                for row in raw_rows
            ]
            calculation_result = self._calculate(calculation, raw_rows, dimension, metric)
            if calculation_result:
                rows = [{dimension_label: row.get(dimension), metric_label: row.get(metric)}
                        for row in calculation_result["rows"]]
            result = {
                "queryResults": rows,
                "querySql": data.get("sql"),
                "queryTimeCost": round((time.perf_counter() - started) * 1000),
                "response": calculation_result["response"] if calculation_result else (
                    f"已按{dimension_label}汇总{metric_label}，返回前 {limit} 名。"
                    if ranked_query
                    else f"已按{dimension_label}汇总{metric_label}。"
                ),
                "effectiveTimeRange": "全部数据（本问题未应用时间筛选）",
                "chatId": conversation_id,
                "resolvedQuestion": resolved_question,
                "resolutionMode": resolution_mode,
            }
            if calculation_result:
                result["calculationDetail"] = calculation_result["detail"]
                result["calculationTimeCost"] = calculation_result["duration_ms"]
            self._validate_question_result(request.question, result)
            self._conversations.record(
                request.actor.subject, conversation_id, request.question, result
            )
            return result
        parse_payload = {
            "queryText": self._contextual_question(request, history, summary),
            "chatId": 0,
            "viewId": request.context.semantic_model_id,
            "agentId": request.agent_id,
            "saveAnswer": True,
        }
        async with self._query_lock:
            parsed = await self._post("/api/chat/query/parse", parse_payload)
            candidates = (parsed.get("selectedParses") or []) + (
                parsed.get("candidateParses") or []
            )
            if parsed.get("state") == "FAILED" or not candidates:
                raise SemanticResolutionError(
                    "无法从问题中确定统计指标。请补充类似“全球销量、北美销量”等指标后重试"
                )
            parse_info = self._select_governed_query(candidates, request.context.semantic_model_id)

            execute_payload = {
                "queryText": parse_payload["queryText"],
                "chatId": 0,
                "agentId": request.agent_id,
                "queryId": parsed.get("queryId"),
                "parseId": parse_info["id"],
                "saveAnswer": True,
            }
            result = await self._post("/api/chat/query/execute", execute_payload)
        self._validate_question_result(request.question, result)
        # SuperSonic's UI also restores queryId from the parse response because some
        # execution modes omit it. Preserve that identifier for evidence and audit.
        if result.get("queryId") is None and parsed.get("queryId") is not None:
            result["queryId"] = parsed["queryId"]
        result["chatId"] = conversation_id
        result["resolvedQuestion"] = parse_payload["queryText"]
        result["resolutionMode"] = "SuperSonic 原生语义解析"
        self._conversations.record(request.actor.subject, conversation_id, request.question, result)
        return result

    async def _view_model_biz_name(self, view_id: int) -> str:
        view = await self._request_data("GET", f"/api/semantic/view/{view_id}")
        detail = view.get("viewDetail") if isinstance(view, dict) else None
        configs = detail.get("viewModelConfigs") if isinstance(detail, dict) else None
        model_id = next((item.get("id") for item in (configs or [])
                         if isinstance(item, dict) and isinstance(item.get("id"), int)), None)
        if model_id is None:
            raise UpstreamError("SuperSonic query view has no semantic model")
        model = await self._request_data("GET", f"/api/semantic/model/getModel/{model_id}")
        biz_name = model.get("bizName") if isinstance(model, dict) else None
        if not isinstance(biz_name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", biz_name):
            raise UpstreamError("SuperSonic returned an invalid semantic model identifier")
        return biz_name

    @staticmethod
    def _field_label(field: str) -> str:
        return {
            "publisher": "发行商", "platform": "平台", "genre": "游戏类型",
            "global_sales": "全球销量", "na_sales": "北美销量",
            "eu_sales": "欧洲销量", "jp_sales": "日本销量",
            "other_sales": "其他地区销量",
        }.get(field, field)

    @staticmethod
    def _chinese_number(value: str) -> int | None:
        if value.isdigit():
            return int(value)
        digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                  "七": 7, "八": 8, "九": 9, "十": 10}
        if value in digits:
            return digits[value]
        if len(value) == 2 and value[0] == "十" and value[1] in digits:
            return 10 + digits[value[1]]
        return None

    @classmethod
    def _calculation_query(cls, question: str) -> dict[str, Any] | None:
        dimensions = {"发行商": "publisher", "平台": "platform", "游戏类型": "genre", "类型": "genre"}
        metrics = {"全球销量": "global_sales", "北美销量": "na_sales", "欧洲销量": "eu_sales",
                   "日本销量": "jp_sales", "其他地区销量": "other_sales"}
        normalized = re.sub(r"[，。！？?\s]", "", question)
        dimension = next((field for label, field in dimensions.items() if label in normalized), None)
        metric = next((field for label, field in metrics.items() if label in normalized), None)
        if metric is None and ("销量" in normalized or "销售" in normalized):
            metric = "global_sales"
        operation = ("average" if any(word in normalized for word in ("平均", "均值"))
                     else "sum" if any(word in normalized for word in ("合计", "总和", "加总"))
                     else None)
        range_match = re.search(r"(?:排名)?(前|后|最低)([一二三四五六七八九十\d]{1,3})(?:名)?", normalized)
        if dimension and metric and operation and range_match:
            limit = cls._chinese_number(range_match.group(2))
            if limit:
                bottom_scope = range_match.group(1) in {"后", "最低"}
                return {"operation": operation, "dimension": dimension, "metric": metric,
                        "limit": min(limit, 100),
                        **({"sort": "asc", "scope": "Bottom"} if bottom_scope else {})}
        rank_pair = re.search(r"第([一二三四五六七八九十\d]{1,3})名?(?:和|与|、)第?"
                              r"([一二三四五六七八九十\d]{1,3})名?.*?(?:差|相差)", normalized)
        if dimension and metric and rank_pair:
            ranks = [cls._chinese_number(rank_pair.group(index)) for index in (1, 2)]
            if all(ranks):
                return {"operation": "rank_difference", "dimension": dimension, "metric": metric,
                        "limit": min(max(ranks), 100), "ranks": ranks}
        rank_single = re.search(r"第([一二三四五六七八九十\d]{1,3})名", normalized)
        if dimension and metric and rank_single and any(word in normalized for word in ("多少", "什么", "是谁")):
            rank = cls._chinese_number(rank_single.group(1))
            if rank:
                return {"operation": "rank_value", "dimension": dimension, "metric": metric,
                        "limit": min(rank, 100), "ranks": [rank]}
        top = re.search(r"(全球销量|北美销量|欧洲销量|日本销量|其他地区销量)前\s*"
                        r"([一二三四五六七八九十\d]{1,3})\s*名(发行商|平台|游戏类型|类型).*?(合计|总和|平均)", question)
        if top:
            limit = cls._chinese_number(top.group(2))
            if limit:
                return {"operation": "average" if top.group(4) == "平均" else "sum",
                        "dimension": dimensions[top.group(3)], "metric": metrics[top.group(1)],
                        "limit": min(limit, 100)}
        bottom = re.search(r"(全球销量|北美销量|欧洲销量|日本销量|其他地区销量)(?:后|最低)\s*"
                           r"([一二三四五六七八九十\d]{1,3})\s*名(发行商|平台|游戏类型|类型).*?(合计|总和|平均)", question)
        if bottom:
            limit = cls._chinese_number(bottom.group(2))
            if limit:
                return {"operation": "average" if bottom.group(4) == "平均" else "sum",
                        "dimension": dimensions[bottom.group(3)], "metric": metrics[bottom.group(1)],
                        "limit": min(limit, 100), "sort": "asc", "scope": "Bottom"}
        rank_difference = re.search(
            r"(发行商|平台|游戏类型|类型)(?:的?全球)?销量?第\s*"
            r"([一二三四五六七八九十\d]{1,3})\s*名\s*(?:和|与)\s*第\s*"
            r"([一二三四五六七八九十\d]{1,3})\s*名(?:相)?差多少", question)
        if rank_difference:
            ranks = [cls._chinese_number(rank_difference.group(index)) for index in (2, 3)]
            if all(ranks):
                return {"operation": "rank_difference", "dimension": dimensions[rank_difference.group(1)],
                        "metric": "global_sales", "limit": min(max(ranks), 100), "ranks": ranks}
        rank_value = re.search(
            r"(?:全球销量)?第\s*([一二三四五六七八九十\d]{1,3})\s*名"
            r"(发行商|平台|游戏类型|类型)(?:的)?(?:全球)?销量(?:是)?多少", question)
        if rank_value:
            rank = cls._chinese_number(rank_value.group(1))
            if rank:
                return {"operation": "rank_value", "dimension": dimensions[rank_value.group(2)],
                        "metric": "global_sales", "limit": min(rank, 100), "ranks": [rank]}
        difference = re.search(r"(.+?)\s*(?:类型|发行商|平台)?\s*的?\s*"
                               r"(全球销量|北美销量|欧洲销量|日本销量|其他地区销量)\s*比\s*"
                               r"(.+?)\s*(?:类型|发行商|平台)?\s*(高|低|多|少)多少", question)
        if difference:
            dimension = "genre" if "类型" in question else "publisher" if "发行商" in question else "platform"
            return {"operation": "difference", "dimension": dimension, "metric": metrics[difference.group(2)],
                    "limit": 100, "members": [difference.group(1).strip(), difference.group(3).strip()]}
        ratio = re.search(r"(.+?)\s*(?:类型|发行商|平台)?\s*的?\s*"
                          r"(全球销量|北美销量|欧洲销量|日本销量|其他地区销量)\s*是\s*"
                          r"(.+?)\s*(?:类型|发行商|平台)?\s*的?\s*几倍", question)
        if ratio:
            dimension = "genre" if "类型" in question else "publisher" if "发行商" in question else "platform"
            return {"operation": "ratio", "dimension": dimension, "metric": metrics[ratio.group(2)],
                    "limit": 100, "members": [ratio.group(1).strip(), ratio.group(3).strip()]}
        share = re.search(r"(.+?)\s*(?:类型|发行商|平台)?\s*(?:的)?\s*"
                          r"(全球销量|北美销量|欧洲销量|日本销量|其他地区销量)\s*占(?:全部|总体|总)\s*(?:销量)?(?:的)?(?:比例|占比)?(?:是)?多少", question)
        if share:
            dimension = "genre" if "类型" in question else "publisher" if "发行商" in question else "platform"
            return {"operation": "share", "dimension": dimension, "metric": metrics[share.group(2)],
                    "limit": 100, "members": [share.group(1).strip()]}
        return None

    @staticmethod
    def _calculation_structure(calculation: dict[str, Any] | None) -> tuple[str, str, int] | None:
        return ((calculation["dimension"], calculation["metric"], calculation["limit"])
                if calculation else None)

    @classmethod
    def _calculate(cls, calculation: dict[str, Any] | None, rows: list[dict[str, Any]],
                   dimension: str, metric: str) -> dict[str, Any] | None:
        if not calculation:
            return None
        started = time.perf_counter()
        values: list[tuple[dict[str, Any], Decimal]] = []
        for row in rows:
            try:
                values.append((row, Decimal(str(row.get(metric)))))
            except (InvalidOperation, TypeError, ValueError):
                continue
        if calculation["operation"] in {"sum", "average"}:
            selected = values[:calculation["limit"]]
            if len(selected) < calculation["limit"]:
                raise SemanticResolutionError("真实查询结果不足以完成本次 Top N 计算")
            total = sum((value for _, value in selected), Decimal("0"))
            result = total if calculation["operation"] == "sum" else total / Decimal(len(selected))
            label = "合计" if calculation["operation"] == "sum" else "平均值"
            expression = " + ".join(cls._format_decimal(value) for _, value in selected)
            formula = expression if calculation["operation"] == "sum" else f"({expression}) / {len(selected)}"
            scope = calculation.get("scope", "Top")
            scope_label = "后" if scope == "Bottom" else "前"
            return {"rows": [row for row, _ in selected],
                    "response": f"{cls._field_label(metric)}{scope_label} {len(selected)} 名{cls._field_label(dimension)}的{label}为 {cls._format_decimal(result)}。",
                    "detail": f"运算：{scope} {len(selected)} {label}\n参与值：{expression}\n公式：{formula} = {cls._format_decimal(result)}",
                    "duration_ms": round((time.perf_counter() - started) * 1000)}
        if calculation["operation"] == "rank_value":
            rank = calculation["ranks"][0]
            if len(values) < rank:
                raise SemanticResolutionError("真实查询结果不足以取得指定名次")
            row, value = values[rank - 1]
            member = str(row.get(dimension))
            return {"rows": [row],
                    "response": f"第 {rank} 名{cls._field_label(dimension)}是 {member}，{cls._field_label(metric)}为 {cls._format_decimal(value)}。",
                    "detail": f"运算：排名取值\n排序：{cls._field_label(metric)}从高到低\n结果：第 {rank} 名 {member} = {cls._format_decimal(value)}",
                    "duration_ms": round((time.perf_counter() - started) * 1000)}
        if calculation["operation"] == "rank_difference":
            ranks = calculation["ranks"]
            if len(values) < max(ranks):
                raise SemanticResolutionError("真实查询结果不足以完成排名差值计算")
            found = [values[rank - 1] for rank in ranks]
            members = [f"第{rank}名 {row.get(dimension)}" for rank, (row, _) in zip(ranks, found)]
        elif calculation["operation"] == "share":
            member = calculation["members"][0]
            match = next(((row, value) for row, value in values
                          if str(row.get(dimension, "")).casefold() == member.casefold()), None)
            if not match:
                raise SemanticResolutionError(f"真实查询结果中未找到“{member}”，无法计算占比")
            total = sum((value for _, value in values), Decimal("0"))
            share_value = match[1] / total * Decimal("100") if total else Decimal("0")
            return {"rows": [match[0]],
                    "response": f"{member} 的{cls._field_label(metric)}占总体 {cls._format_decimal(share_value)}%。",
                    "detail": f"运算：总体占比\n参与值：{member}={cls._format_decimal(match[1])}；总体={cls._format_decimal(total)}\n公式：{cls._format_decimal(match[1])} / {cls._format_decimal(total)} = {cls._format_decimal(share_value)}%",
                    "duration_ms": round((time.perf_counter() - started) * 1000)}
        else:
            found = []
            for member in calculation["members"]:
                match = next(((row, value) for row, value in values
                              if str(row.get(dimension, "")).casefold() == member.casefold()), None)
                if not match:
                    raise SemanticResolutionError(f"真实查询结果中未找到“{member}”，无法完成差值计算")
                found.append(match)
            members = calculation["members"]
        left, right = found[0][1], found[1][1]
        if calculation["operation"] == "ratio":
            ratio_value = left / right if right else None
            if ratio_value is None:
                raise SemanticResolutionError("作为除数的指标值为 0，无法计算倍数")
            return {"rows": [row for row, _ in found],
                    "response": f"{members[0]} 的{cls._field_label(metric)}是 {members[1]} 的 {cls._format_decimal(ratio_value)} 倍。",
                    "detail": f"运算：倍数\n参与值：{members[0]}={cls._format_decimal(left)}；{members[1]}={cls._format_decimal(right)}\n公式：{cls._format_decimal(left)} / {cls._format_decimal(right)} = {cls._format_decimal(ratio_value)}",
                    "duration_ms": round((time.perf_counter() - started) * 1000)}
        difference, absolute = left - right, abs(left - right)
        percentage = absolute / abs(right) * Decimal("100") if right else None
        detail = (f"运算：差值与差异率\n参与值：{members[0]}={cls._format_decimal(left)}；{members[1]}={cls._format_decimal(right)}\n"
                  f"公式：{cls._format_decimal(left)} - {cls._format_decimal(right)} = {cls._format_decimal(difference)}")
        response = (f"{members[0]} 的{cls._field_label(metric)}为 {cls._format_decimal(left)}，{members[1]} 为 "
                    f"{cls._format_decimal(right)}，相差 {cls._format_decimal(absolute)}")
        if percentage is not None:
            detail += f"\n差异率：{cls._format_decimal(absolute)} / {cls._format_decimal(abs(right))} = {cls._format_decimal(percentage)}%"
            response += f"，相对 {members[1]} 的差异率为 {cls._format_decimal(percentage)}%"
        return {"rows": [row for row, _ in found], "response": response + "。", "detail": detail,
                "duration_ms": round((time.perf_counter() - started) * 1000)}

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"

    @classmethod
    def _ranking_query(cls, question: str) -> tuple[str, str, int] | None:
        """Compile an explicit governed Top-N request without invoking an LLM parser."""

        match = re.search(r"按\s*(发行商|平台|游戏类型|类型)\s*统计\s*"
                          r"(全球销量|北美销量|欧洲销量|日本销量|其他地区销量)\s*"
                          r"前\s*(\d{1,3})\s*(?:名|个)?", question)
        dimensions = {"发行商": "publisher", "平台": "platform",
                      "游戏类型": "genre", "类型": "genre"}
        metrics = {"全球销量": "global_sales", "北美销量": "na_sales",
                   "欧洲销量": "eu_sales", "日本销量": "jp_sales",
                   "其他地区销量": "other_sales"}
        if match:
            return dimensions[match.group(1)], metrics[match.group(2)], min(int(match.group(3)), 100)
        shorthand = re.search(
            r"(发行商|平台|游戏类型|类型)\s*(?:销售|销量)?\s*(?:排名|排行)", question
        )
        if shorthand:
            return dimensions[shorthand.group(1)], "global_sales", 10
        return None

    @classmethod
    def _grouped_metric_query(cls, question: str) -> tuple[str, str, int] | None:
        """Compile an explicit grouped metric question when no Top-N phrase is present."""

        match = re.search(
            r"(?:各|按)?\s*(发行商|平台|游戏类型|类型)\s*(?:统计)?\s*的?\s*"
            r"(全球销量|北美销量|欧洲销量|日本销量|其他地区销量)",
            question,
        )
        if not match:
            return None
        dimensions = {"发行商": "publisher", "平台": "platform",
                      "游戏类型": "genre", "类型": "genre"}
        metrics = {"全球销量": "global_sales", "北美销量": "na_sales",
                   "欧洲销量": "eu_sales", "日本销量": "jp_sales",
                   "其他地区销量": "other_sales"}
        return dimensions[match.group(1)], metrics[match.group(2)], 100

    @staticmethod
    def _validate_question_result(question: str, result: dict[str, Any]) -> None:
        """Reject successful-but-unrelated parses before they reach the workbench."""

        rows = result.get("queryResults")
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return
        columns = {str(column).strip().lower() for column in rows[0]}
        required_terms = {
            "发行商": {"publisher", "发行商"},
            "平台": {"platform", "平台"},
            "游戏类型": {"genre", "游戏类型", "类型"},
            "全球销量": {"global_sales", "全球销量", "global sales"},
            "北美销量": {"na_sales", "北美销量"},
            "欧洲销量": {"eu_sales", "欧洲销量"},
            "日本销量": {"jp_sales", "日本销量"},
        }
        missing = [label for label, aliases in required_terms.items()
                   if label in question and columns.isdisjoint(aliases)]
        if missing:
            raise ResultMismatchError(
                f"查询结果与本次问题不匹配，缺少字段：{'、'.join(missing)}。请检查所选语义模型后重试"
            )
        ranking = re.search(r"前\s*(\d{1,3})\s*(?:名|个)?", question)
        if ranking and len(rows) > int(ranking.group(1)):
            result["queryResults"] = rows[: int(ranking.group(1))]

    async def list_semantic_models(self) -> list[dict[str, Any]]:
        """Return a sanitized inventory of live SuperSonic semantic models."""

        domains = await self._request_data("GET", "/api/semantic/schema/domain/list")
        databases = await self._request_data("GET", "/api/semantic/database/getDatabaseList")
        if not isinstance(domains, list):
            raise UpstreamError("SuperSonic returned an invalid domain inventory")
        database_names = {
            item["id"]: str(item.get("name") or item["id"])[:128]
            for item in databases
            if isinstance(databases, list)
            and isinstance(item, dict)
            and isinstance(item.get("id"), int)
        }
        inventory: list[dict[str, Any]] = []
        for domain in domains[:100]:
            if not isinstance(domain, dict) or not isinstance(domain.get("id"), int):
                continue
            domain_id = domain["id"]
            models = await self._request_data(
                "GET", f"/api/semantic/model/getModelList/{domain_id}"
            )
            if not isinstance(models, list):
                continue
            model_by_id = {
                model["id"]: model for model in models[:500]
                if isinstance(model, dict) and isinstance(model.get("id"), int)
            }
            views = await self._request_data(
                "GET", f"/api/semantic/view/getViewList?domainId={domain_id}"
            )
            if not isinstance(views, list):
                continue
            for view in views[:500]:
                if not isinstance(view, dict) or not isinstance(view.get("id"), int):
                    continue
                detail = view.get("viewDetail") if isinstance(view.get("viewDetail"), dict) else {}
                configs = detail.get("viewModelConfigs") if isinstance(detail.get("viewModelConfigs"), list) else []
                model_id = next((item.get("id") for item in configs if isinstance(item, dict)
                                 and isinstance(item.get("id"), int)), None)
                model = model_by_id.get(model_id, {})
                inventory.append(
                    {
                        "id": view["id"],
                        "key": f"supersonic:{view['id']}",
                        "model_id": model_id,
                        "name": str(view.get("name") or view.get("bizName") or view["id"])[:128],
                        "biz_name": str(view.get("bizName") or "")[:128],
                        "description": str(view.get("description") or model.get("description") or "")[:512],
                        "domain_id": domain_id,
                        "domain_name": str(domain.get("name") or domain_id)[:128],
                        "database_id": model.get("databaseId"),
                        "database_name": database_names.get(model.get("databaseId"), "未知连接"),
                        "status": "active" if view.get("status") == 1 else "offline",
                    }
                )
        return inventory

    async def list_modeling_catalog(self) -> dict[str, list[dict[str, Any]]]:
        """Return sanitized domains and databases needed by the review form."""

        domains = await self._request_data("GET", "/api/semantic/schema/domain/list")
        databases = await self._request_data("GET", "/api/semantic/database/getDatabaseList")
        if not isinstance(domains, list) or not isinstance(databases, list):
            raise UpstreamError("SuperSonic returned an invalid modeling catalog")
        model_names: dict[int, list[str]] = {}
        for domain in domains[:100]:
            if not isinstance(domain, dict) or not isinstance(domain.get("id"), int):
                continue
            models = await self._request_data(
                "GET", f"/api/semantic/model/getModelList/{domain['id']}"
            )
            if not isinstance(models, list):
                continue
            for model in models:
                if not isinstance(model, dict):
                    continue
                database_id = model.get("databaseId")
                if isinstance(database_id, int):
                    model_names.setdefault(database_id, []).append(
                        str(model.get("name") or model.get("bizName") or model.get("id"))[:128]
                    )
        return {
            "domains": [
                {
                    "id": item["id"],
                    "name": str(item.get("name") or item["id"])[:128],
                    "biz_name": str(item.get("bizName") or "")[:128],
                    "description": str(item.get("description") or "")[:512],
                }
                for item in domains[:100]
                if isinstance(item, dict) and isinstance(item.get("id"), int)
            ],
            "databases": [
                {
                    "id": item["id"],
                    "name": str(item.get("name") or item["id"])[:128],
                    "type": str(item.get("type") or "")[:64],
                    "host": str(item.get("host") or "")[:253],
                    "port": str(item.get("port") or "")[:16],
                    "database": str(item.get("database") or "")[:250],
                    "username": str(item.get("username") or "")[:250],
                    "description": str(item.get("description") or "")[:512],
                    "model_count": len(model_names.get(item["id"], [])),
                    "model_names": model_names.get(item["id"], []),
                    "editable": bool(item.get("hasEditPermission")) and item.get("type") != "h2",
                }
                for item in databases[:100]
                if isinstance(item, dict) and isinstance(item.get("id"), int)
            ],
        }

    async def save_domain(
        self,
        *,
        name: str,
        biz_name: str,
        description: str,
        username: str,
        domain_id: int | None = None,
    ) -> dict[str, Any]:
        payload = {
            "name": name,
            "bizName": biz_name,
            "description": description,
            "parentId": 0,
            "isOpen": 1,
            "status": 1,
            "typeEnum": "DOMAIN",
            "viewers": [username],
            "admins": [username],
            "viewOrgs": [],
            "adminOrgs": [],
        }
        path = "/api/semantic/domain/createDomain"
        if domain_id is not None:
            payload["id"] = domain_id
            path = "/api/semantic/domain/updateDomain"
        saved = await self._request_data("POST", path, payload=payload)
        if saved is not True:
            raise UpstreamError("SuperSonic rejected semantic domain")
        catalog = await self.list_modeling_catalog()
        match = next(
            (item for item in catalog["domains"] if item["id"] == domain_id),
            None,
        ) if domain_id is not None else next(
            (item for item in catalog["domains"] if item.get("biz_name") == biz_name),
            None,
        )
        if match is None:
            raise UpstreamError("SuperSonic did not return the saved semantic domain")
        return match

    async def delete_domain(self, domain_id: int) -> None:
        deleted = await self._request_data(
            "DELETE", f"/api/semantic/domain/deleteDomain/{domain_id}"
        )
        if deleted is not True:
            raise UpstreamError("SuperSonic rejected semantic domain deletion")

    async def publish_semantic_model(self, payload: dict[str, Any]) -> None:
        """Publish an administrator-reviewed model through SuperSonic's governed API."""

        await self._request_data("POST", "/api/semantic/model/createModel", payload=payload)
        models = await self._request_data(
            "GET", f"/api/semantic/model/getModelList/{int(payload['domainId'])}"
        )
        model = next((item for item in (models or []) if isinstance(item, dict)
                      and item.get("bizName") == payload.get("bizName")), None)
        if not isinstance(model, dict) or not isinstance(model.get("id"), int):
            raise UpstreamError("SuperSonic did not return the published semantic model")
        view_payload = {
            "name": payload["name"], "bizName": f"{payload['bizName']}_view",
            "description": payload.get("description") or "", "domainId": payload["domainId"],
            "status": 1, "typeEnum": "VIEW",
            "viewDetail": {"viewModelConfigs": [{"id": model["id"], "includesAll": True,
                                                  "metrics": [], "dimensions": []}]},
            "admins": payload.get("admins") or [], "adminOrgs": payload.get("adminOrgs") or [],
        }
        view = await self._request_data("POST", "/api/semantic/view", payload=view_payload)
        if not isinstance(view, dict) or not isinstance(view.get("id"), int):
            raise UpstreamError("SuperSonic did not create a query view for the semantic model")
        await self._request_data("POST", "/api/chat/conf", payload={"modelId": view["id"]})

    async def get_database_columns(
        self, database_id: int, database_name: str, table_name: str
    ) -> set[str]:
        """Preflight the selected physical table through the target SuperSonic connection."""

        data = await self._request_data(
            "GET",
            "/api/semantic/database/getColumns/"
            f"{database_id}/{quote(database_name, safe='')}/{quote(table_name, safe='')}",
        )
        rows = data.get("resultList") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise UpstreamError("SuperSonic returned invalid table metadata")
        return {str(item["name"]) for item in rows if isinstance(item, dict) and item.get("name")}

    async def create_database(
        self, payload: dict[str, Any], database_id: int | None = None
    ) -> dict[str, Any]:
        """Test a one-shot secret, then create the connection; never return credentials."""

        connected = await self._request_data(
            "POST", "/api/semantic/database/testConnect", payload=payload
        )
        if connected is not True:
            raise UpstreamError("SuperSonic database connection test failed")

        # SuperSonic's create endpoint returns the request-shaped object before the
        # generated id is copied back into it. It also allows identical connections
        # to be inserted repeatedly. Resolve an existing same-source connection first
        # so retries update it, then fall back to the refreshed inventory for new rows.
        databases = await self._request_data("GET", "/api/semantic/database/getDatabaseList")
        if not isinstance(databases, list):
            raise UpstreamError("SuperSonic returned invalid database inventory")
        existing = next(
            (item for item in databases if isinstance(item, dict) and item.get("id") == database_id),
            None,
        ) if database_id is not None else self._matching_database(databases, payload)
        saved_payload = dict(payload)
        if existing is not None:
            saved_payload["id"] = existing["id"]
        created = await self._request_data(
            "POST", "/api/semantic/database/createOrUpdateDatabase", payload=saved_payload
        )
        if not isinstance(created, dict):
            raise UpstreamError("SuperSonic returned invalid database metadata")
        database_id = created.get("id")
        if not isinstance(database_id, int):
            refreshed = await self._request_data(
                "GET", "/api/semantic/database/getDatabaseList"
            )
            if not isinstance(refreshed, list):
                raise UpstreamError("SuperSonic returned invalid database inventory")
            match = self._matching_database(refreshed, payload, newest=True)
            database_id = match.get("id") if match is not None else None
        if not isinstance(database_id, int):
            raise UpstreamError("SuperSonic did not return the saved database")
        return {
            "id": database_id,
            "name": str(created.get("name") or payload["name"])[:128],
            "type": str(created.get("type") or payload["type"])[:64],
        }

    async def delete_database(self, database_id: int) -> None:
        databases = await self._request_data("GET", "/api/semantic/database/getDatabaseList")
        if not isinstance(databases, list):
            raise UpstreamError("SuperSonic returned invalid database inventory")
        target = next(
            (item for item in databases if isinstance(item, dict) and item.get("id") == database_id),
            None,
        )
        if target is None:
            raise UpstreamError("SuperSonic database does not exist")
        if target.get("type") == "h2":
            raise UpstreamError("SuperSonic built-in database cannot be deleted")
        deleted = await self._request_data("DELETE", f"/api/semantic/database/{database_id}")
        if deleted is not True:
            raise UpstreamError("SuperSonic rejected database deletion")

    @staticmethod
    def _matching_database(
        databases: list[Any], payload: dict[str, Any], *, newest: bool = False
    ) -> dict[str, Any] | None:
        def normalized(value: Any) -> str:
            return str(value or "").strip().lower()

        matches = [
            item
            for item in databases
            if isinstance(item, dict)
            and isinstance(item.get("id"), int)
            and normalized(item.get("name")) == normalized(payload.get("name"))
            and normalized(item.get("type")) == normalized(payload.get("type"))
            and normalized(item.get("host")) == normalized(payload.get("host"))
            and normalized(item.get("port")) == normalized(payload.get("port"))
            and normalized(item.get("database")) == normalized(payload.get("database"))
            and normalized(item.get("username")) == normalized(payload.get("username"))
        ]
        if not matches:
            return None
        return max(matches, key=lambda item: item["id"]) if newest else min(
            matches, key=lambda item: item["id"]
        )

    def _conversation(self, request: AnalyzeRequest) -> tuple[int, str, list[str]]:
        """Resolve an unguessable conversation id bound to the authenticated actor."""
        try:
            return self._conversations.resolve(request.actor.subject, request.chat_id)
        except ValueError as exc:
            raise UpstreamError("AgentBI conversation is unavailable") from exc

    @staticmethod
    def _select_governed_query(candidates: list[Any], expected_view_id: int | None = None) -> dict[str, Any]:
        """Select an executable semantic query and reject plugin/URL candidates.

        SuperSonic can rank WEB_PAGE plugins ahead of metric parses. AgentBI must never
        execute those candidates because they can redirect the workflow to an external
        URL. A populated querySQL proves the candidate passed SuperSonic's governed SQL
        generation path; raw SQL is still never exposed to the browser.
        """

        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("id") is None:
                continue
            candidate_view = candidate.get("viewId")
            if candidate_view is None and isinstance(candidate.get("view"), dict):
                candidate_view = candidate["view"].get("view") or candidate["view"].get("id")
            if expected_view_id is not None and candidate_view is not None:
                try:
                    if int(candidate_view) != expected_view_id:
                        continue
                except (TypeError, ValueError):
                    continue
            sql_info = candidate.get("sqlInfo")
            if isinstance(sql_info, dict) and any(
                isinstance(sql_info.get(key), str) and sql_info[key].strip()
                for key in ("querySQL", "correctS2SQL", "s2SQL")
            ) and candidate.get("queryMode") != "WEB_PAGE":
                return candidate
        raise UpstreamError("SuperSonic returned no governed semantic query")

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Call a ResultData-wrapped SuperSonic endpoint and validate its envelope."""

        data = await self._request_data("POST", path, payload=payload)
        if not isinstance(data, dict):
            raise UpstreamError("SuperSonic returned an unexpected response")
        return data

    async def _request_data(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._client.request(method, path, json=payload, params=params)
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            # Status is safe for internal diagnostics; response bodies may contain
            # SQL or upstream implementation details and are deliberately discarded.
            raise UpstreamError(
                f"SuperSonic semantic query returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise UpstreamError(f"SuperSonic semantic query failed ({type(exc).__name__})") from exc
        except ValueError as exc:
            raise UpstreamError("SuperSonic semantic query returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise UpstreamError("SuperSonic returned an unexpected response")
        if body.get("code") != 200 or "data" not in body:
            raise UpstreamError("SuperSonic rejected the semantic query")
        return body["data"]

    @staticmethod
    def _contextual_question(
        request: AnalyzeRequest,
        history: list[str] | None = None,
        summary: str = "",
    ) -> str:
        """Render dashboard state as data context, not as executable model instructions."""

        parts = [request.question]
        if request.context.time_range.strip():
            parts.append(f"时间范围：{request.context.time_range.strip()}")
        for item in request.context.filters:
            parts.append(f"筛选条件：{item.field} {item.operator.value} {item.value}")
        if request.context.selected:
            parts.append(
                f"当前选中：{request.context.selected.label}={request.context.selected.value}"
            )
        if summary:
            parts.append(f"已压缩的历史事实与证据：{summary}")
        for previous in (history or [])[-2:]:
            parts.append(f"同一用户的历史问题：{' '.join(previous.split())[:300]}")
        return "；".join(parts)

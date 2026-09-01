"""Narrow, timeout-bound adapter for SuperSonic's semantic query API."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
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
        ranked_query = self._ranking_query(resolved_question)
        structured = ranked_query or self._grouped_metric_query(request.question)
        if not structured and self._llm_resolver is not None:
            normalized = await self._llm_resolver(request.question)
            if normalized:
                resolved_question = normalized
                resolution_mode = "LLM 语义增强后交由 SuperSonic 执行"
                ranked_query = self._ranking_query(resolved_question)
                structured = ranked_query or self._grouped_metric_query(resolved_question)
        if structured:
            dimension, metric, limit = structured
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
                        f"GROUP BY {dimension} ORDER BY {metric} DESC LIMIT {limit}"
                    ),
                },
            )
            if not isinstance(data, dict) or not isinstance(data.get("resultList"), list):
                raise UpstreamError("SuperSonic returned an invalid structured query result")
            dimension_label = self._field_label(dimension)
            metric_label = self._field_label(metric)
            rows = [
                {dimension_label: row.get(dimension), metric_label: row.get(metric)}
                for row in data["resultList"]
                if isinstance(row, dict)
            ]
            result = {
                "queryResults": rows,
                "querySql": data.get("sql"),
                "queryTimeCost": round((time.perf_counter() - started) * 1000),
                "response": (
                    f"已按{dimension_label}汇总{metric_label}，返回前 {limit} 名。"
                    if ranked_query
                    else f"已按{dimension_label}汇总{metric_label}。"
                ),
                "effectiveTimeRange": "全部数据（本问题未应用时间筛选）",
                "chatId": conversation_id,
                "resolvedQuestion": resolved_question,
                "resolutionMode": resolution_mode,
            }
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

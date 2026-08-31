"""Narrow, timeout-bound adapter for SuperSonic's semantic query API."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx

from agentbi.config import Settings
from agentbi.conversation import ConversationContextManager
from agentbi.database import IdentityRepository
from agentbi.models import AnalyzeRequest


class UpstreamError(RuntimeError):
    """A sanitized SuperSonic integration failure."""


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
        if repository is None:
            repository = IdentityRepository("sqlite:///:memory:")
            repository.initialize(seed_demo_accounts=False, user_password="", admin_password="")
        self._conversations = ConversationContextManager(settings, repository)

    async def close(self) -> None:
        await self._client.aclose()

    async def query(self, request: AnalyzeRequest) -> dict[str, Any]:
        """Parse the question, select the best semantic parse, then execute it."""

        conversation_id, summary, history = self._conversation(request)
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
                raise UpstreamError("SuperSonic could not resolve the semantic question")
            parse_info = self._select_governed_query(candidates)

            execute_payload = {
                "queryText": parse_payload["queryText"],
                "chatId": 0,
                "agentId": request.agent_id,
                "queryId": parsed.get("queryId"),
                "parseId": parse_info["id"],
                "saveAnswer": True,
            }
            result = await self._post("/api/chat/query/execute", execute_payload)
        # SuperSonic's UI also restores queryId from the parse response because some
        # execution modes omit it. Preserve that identifier for evidence and audit.
        if result.get("queryId") is None and parsed.get("queryId") is not None:
            result["queryId"] = parsed["queryId"]
        result["chatId"] = conversation_id
        self._conversations.record(request.actor.subject, conversation_id, request.question, result)
        return result

    async def list_semantic_models(self) -> list[dict[str, Any]]:
        """Return a sanitized inventory of live SuperSonic semantic models."""

        domains = await self._request_data("GET", "/api/semantic/schema/domain/list")
        if not isinstance(domains, list):
            raise UpstreamError("SuperSonic returned an invalid domain inventory")
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
            for model in models[:500]:
                if not isinstance(model, dict) or not isinstance(model.get("id"), int):
                    continue
                inventory.append(
                    {
                        "id": model["id"],
                        "key": f"supersonic:{model['id']}",
                        "name": str(model.get("name") or model.get("bizName") or model["id"])[:128],
                        "biz_name": str(model.get("bizName") or "")[:128],
                        "description": str(model.get("description") or "")[:512],
                        "domain_id": domain_id,
                        "domain_name": str(domain.get("name") or domain_id)[:128],
                        "status": "active" if model.get("status") == 1 else "offline",
                    }
                )
        return inventory

    async def list_modeling_catalog(self) -> dict[str, list[dict[str, Any]]]:
        """Return sanitized domains and databases needed by the review form."""

        domains = await self._request_data("GET", "/api/semantic/schema/domain/list")
        databases = await self._request_data("GET", "/api/semantic/database/getDatabaseList")
        if not isinstance(domains, list) or not isinstance(databases, list):
            raise UpstreamError("SuperSonic returned an invalid modeling catalog")
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

    async def get_database_columns(
        self, database_id: int, schema_name: str, table_name: str
    ) -> set[str]:
        """Preflight the selected physical table through the target SuperSonic connection."""

        data = await self._request_data(
            "GET",
            "/api/semantic/database/getColumns/"
            f"{database_id}/{quote(schema_name, safe='')}/{quote(table_name, safe='')}",
        )
        rows = data.get("resultList") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise UpstreamError("SuperSonic returned invalid table metadata")
        return {str(item["name"]) for item in rows if isinstance(item, dict) and item.get("name")}

    async def create_database(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Test a one-shot secret, then create the connection; never return credentials."""

        connected = await self._request_data(
            "POST", "/api/semantic/database/testConnect", payload=payload
        )
        if connected is not True:
            raise UpstreamError("SuperSonic database connection test failed")
        created = await self._request_data(
            "POST", "/api/semantic/database/createOrUpdateDatabase", payload=payload
        )
        if not isinstance(created, dict) or not isinstance(created.get("id"), int):
            raise UpstreamError("SuperSonic returned invalid database metadata")
        return {
            "id": created["id"],
            "name": str(created.get("name") or payload["name"])[:128],
            "type": str(created.get("type") or payload["type"])[:64],
        }

    def _conversation(self, request: AnalyzeRequest) -> tuple[int, str, list[str]]:
        """Resolve an unguessable conversation id bound to the authenticated actor."""
        try:
            return self._conversations.resolve(request.actor.subject, request.chat_id)
        except ValueError as exc:
            raise UpstreamError("AgentBI conversation is unavailable") from exc

    @staticmethod
    def _select_governed_query(candidates: list[Any]) -> dict[str, Any]:
        """Select an executable semantic query and reject plugin/URL candidates.

        SuperSonic can rank WEB_PAGE plugins ahead of metric parses. AgentBI must never
        execute those candidates because they can redirect the workflow to an external
        URL. A populated querySQL proves the candidate passed SuperSonic's governed SQL
        generation path; raw SQL is still never exposed to the browser.
        """

        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("id") is None:
                continue
            sql_info = candidate.get("sqlInfo")
            if (
                isinstance(sql_info, dict)
                and isinstance(sql_info.get("querySQL"), str)
                and sql_info["querySQL"].strip()
            ):
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

        parts = [request.question, f"时间范围：{request.context.time_range}"]
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

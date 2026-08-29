"""Narrow, timeout-bound adapter for SuperSonic's semantic query API."""

from __future__ import annotations

import asyncio
from typing import Any

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
            raise UpstreamError(
                f"SuperSonic semantic query failed ({type(exc).__name__})"
            ) from exc
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

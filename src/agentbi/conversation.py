"""Persistent long-dialogue context with deterministic dynamic compression."""

from __future__ import annotations

import json
import secrets
from typing import Any

from agentbi.config import Settings
from agentbi.database import IdentityRepository


def estimate_tokens(text: str) -> int:
    """Conservative dependency-free estimate suitable for compression triggers."""

    cjk = sum("\u4e00" <= char <= "\u9fff" for char in text)
    return max(1, cjk + (len(text) - cjk + 3) // 4)


class ConversationContextManager:
    def __init__(self, settings: Settings, repository: IdentityRepository):
        self.settings = settings
        self.repository = repository

    def resolve(self, actor_id: str, conversation_id: int | None) -> tuple[int, str, list[str]]:
        if conversation_id:
            context = self.repository.conversation_context(conversation_id, actor_id)
            if context is None:
                raise ValueError("conversation unavailable")
        else:
            conversation_id = self._new_id(actor_id)
            context = ("", 0, [])
        summary, _, turns = context
        return conversation_id, summary, [str(turn["question"]) for turn in turns]

    def record(self, actor_id: str, conversation_id: int, question: str, result: dict[str, Any]) -> None:
        answer = self._answer_text(result)
        tool_call = json.dumps({"tool": "supersonic.semantic_query", "question": question}, ensure_ascii=False)
        tool_result = json.dumps(
            {"queryId": result.get("queryId"), "rowCount": len(result.get("queryResults") or [])},
            ensure_ascii=False,
        )
        self.repository.append_conversation_turn(
            conversation_id, actor_id, user_question=question, assistant_answer=answer,
            tool_call=tool_call, tool_result=tool_result,
            estimated_tokens=estimate_tokens(question + answer + tool_call + tool_result),
        )
        self._compress_if_needed(actor_id, conversation_id)

    def _new_id(self, actor_id: str) -> int:
        for _ in range(20):
            conversation_id = secrets.randbelow(2_000_000_000) + 1
            try:
                self.repository.create_conversation(conversation_id, actor_id)
                return conversation_id
            except Exception as exc:
                if "UNIQUE" not in str(exc).upper():
                    raise
        raise RuntimeError("unable to allocate conversation id")

    def _compress_if_needed(self, actor_id: str, conversation_id: int) -> None:
        summary, _, turns = self.repository.conversation_context(conversation_id, actor_id) or ("", 0, [])
        message_count = len(turns) * 4
        token_count = estimate_tokens(summary) + sum(int(turn["tokens"]) for turn in turns)
        if message_count < self.settings.conversation_message_threshold and token_count < self.settings.conversation_token_threshold:
            return
        count = len(turns) - self.settings.conversation_keep_recent_turns
        if count <= 0:
            return
        old = turns[:count]
        lines = [summary] if summary else []
        lines.extend(
            f"第{turn['sequence']}轮：问题={str(turn['question'])[:300]}；结论={str(turn['answer'])[:500]}；证据={turn['tool_result']}"
            for turn in old
        )
        self.repository.compress_conversation(
            conversation_id, actor_id, int(old[-1]["sequence"]), "\n".join(lines)[-6000:]
        )

    @staticmethod
    def _answer_text(result: dict[str, Any]) -> str:
        for key in ("response", "queryText", "summary"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:1000]
        return f"返回 {len(result.get('queryResults') or [])} 行受治理数据"

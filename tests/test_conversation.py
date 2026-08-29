"""Acceptance tests for competition-required dynamic context compression."""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from agentbi.config import Settings
from agentbi.conversation import ConversationContextManager
from agentbi.database import ConversationTurn, IdentityRepository


def base_settings() -> Settings:
    return Settings(
        api_key="x" * 32,
        supersonic_base_url="http://supersonic.test",
        supersonic_token=None,
        request_timeout_seconds=2,
        max_result_rows=500,
        allowed_origins=("http://localhost:8088",),
    )


def repository() -> IdentityRepository:
    repo = IdentityRepository("sqlite:///:memory:")
    repo.initialize(seed_demo_accounts=False, user_password="", admin_password="")
    return repo


def test_message_threshold_compresses_complete_tool_pairs_and_persists_boundary():
    repo = repository()
    manager = ConversationContextManager(
        replace(base_settings(), conversation_message_threshold=8, conversation_keep_recent_turns=1),
        repo,
    )
    chat_id, _, _ = manager.resolve("actor-1", None)
    manager.record("actor-1", chat_id, "华东销售额是多少", {"queryId": 11, "queryResults": [{"v": 1}]})
    manager.record("actor-1", chat_id, "再按产品下钻", {"queryId": 12, "queryResults": [{"v": 2}]})

    # A fresh manager simulates process restart and must recover the persisted boundary.
    restarted = ConversationContextManager(manager.settings, repo)
    _, summary, recent = restarted.resolve("actor-1", chat_id)
    assert "华东销售额是多少" in summary
    assert '"queryId": 11' in summary
    assert recent == ["再按产品下钻"]

    with Session(repo.engine) as db:
        first = db.scalar(
            select(ConversationTurn).where(
                ConversationTurn.conversation_id == chat_id,
                ConversationTurn.sequence == 1,
            )
        )
        assert first is not None
        assert "supersonic.semantic_query" in first.tool_call
        assert '"queryId": 11' in first.tool_result


def test_token_threshold_also_triggers_compression_and_actor_isolation():
    repo = repository()
    manager = ConversationContextManager(
        replace(
            base_settings(),
            conversation_token_threshold=20,
            conversation_message_threshold=100,
            conversation_keep_recent_turns=1,
        ),
        repo,
    )
    chat_id, _, _ = manager.resolve("actor-1", None)
    manager.record("actor-1", chat_id, "第一轮" * 20, {"queryId": 21, "queryResults": []})
    manager.record("actor-1", chat_id, "第二轮" * 20, {"queryId": 22, "queryResults": []})

    _, summary, recent = manager.resolve("actor-1", chat_id)
    assert "第一轮" in summary
    assert recent == ["第二轮" * 20]
    try:
        manager.resolve("actor-2", chat_id)
    except ValueError as exc:
        assert "unavailable" in str(exc)
    else:
        raise AssertionError("another actor must not access the conversation")

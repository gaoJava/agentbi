from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from agentbi.config import Settings
from agentbi.semantic_llm import SemanticDraftLlm, SemanticLlmError


def settings() -> Settings:
    return Settings(
        api_key="x" * 32,
        supersonic_base_url="http://supersonic.test",
        supersonic_token=None,
        request_timeout_seconds=2,
        max_result_rows=100,
        allowed_origins=("http://localhost",),
        llm_base_url="http://llm.test/v1",
        llm_api_key="secret",
        llm_model="enterprise-model",
    )


def baseline() -> dict:
    return {
        "dataset": {"superset_id": 1, "name": "orders", "schema": "public", "database_name": "db"},
        "model": {"name": "订单", "biz_name": "orders", "description": ""},
        "fields": [{"name": "order_id", "type": "BIGINT"}, {"name": "amount", "type": "NUMERIC"}],
        "identifiers": [{"name": "订单", "field": "order_id", "type": "primary", "synonyms": []}],
        "dimensions": [],
        "measures": [{"name": "金额", "field": "amount", "aggregation": "SUM", "synonyms": []}],
        "drilldown_path": [],
        "warnings": [],
        "generation": {"source": "metadata_inference", "ai_generated": False, "label": "fallback"},
    }


def test_enriches_only_with_schema_constrained_real_provider_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer secret"
        assert payload["temperature"] == 0.1
        assert "orders" in payload["messages"][1]["content"]
        assert "password" not in payload["messages"][1]["content"].lower()
        result = {
            "model": {"name": "销售订单", "biz_name": "orders_model", "description": "订单分析"},
            "identifiers": [
                {"name": "订单", "field": "order_id", "type": "primary", "synonyms": ["单据"]}
            ],
            "dimensions": [],
            "measures": [
                {"name": "销售额", "field": "amount", "aggregation": "SUM", "synonyms": ["收入"]}
            ],
            "drilldown_path": [],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    client = SemanticDraftLlm(settings(), httpx.MockTransport(handler))
    result = asyncio.run(client.enrich(baseline()))
    asyncio.run(client.close())
    assert result["generation"]["ai_generated"] is True
    assert result["generation"]["source"] == "llm"
    assert result["measures"][0]["name"] == "销售额"


def test_enrich_with_uses_selected_provider_without_changing_default() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert str(request.url).startswith("http://selected.test/v1/chat/completions")
        assert request.headers["Authorization"] == "Bearer selected-secret"
        assert payload["model"] == "selected-model"
        result = {
            "model": {},
            "identifiers": [
                {"name": "订单", "field": "order_id", "type": "primary", "synonyms": []}
            ],
            "dimensions": [],
            "measures": [
                {"name": "金额", "field": "amount", "aggregation": "SUM", "synonyms": []}
            ],
            "drilldown_path": [],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    client = SemanticDraftLlm(settings(), httpx.MockTransport(handler))
    result = asyncio.run(
        client.enrich_with(
            baseline(),
            base_url="http://selected.test/v1/",
            api_key="selected-secret",
            model="selected-model",
        )
    )
    asyncio.run(client.close())
    assert result["generation"]["label"] == "真实 LLM 增强（selected-model · 快速模式）"
    assert result["model"]["name"] == "订单"
    assert result["model"]["biz_name"] == "orders"
    assert result["model"]["description"] == ""


def test_rejects_hallucinated_field() -> None:
    result = {
        "model": {},
        "identifiers": [{"name": "订单", "field": "missing", "type": "primary"}],
        "dimensions": [],
        "measures": [{"name": "金额", "field": "amount", "aggregation": "SUM"}],
        "drilldown_path": [],
    }
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(result)}}]},
        )
    )
    client = SemanticDraftLlm(settings(), transport)
    with pytest.raises(SemanticLlmError, match="不存在"):
        asyncio.run(client.enrich(baseline()))
    asyncio.run(client.close())


def test_api_key_is_encrypted_and_bound_to_session_secret() -> None:
    client = SemanticDraftLlm(settings())
    encrypted = client.encrypt_key("provider-secret")
    assert encrypted != "provider-secret"
    assert "provider-secret" not in encrypted
    assert client.decrypt_key(encrypted) == "provider-secret"

    other = SemanticDraftLlm(replace(settings(), session_secret="z" * 32))
    with pytest.raises(SemanticLlmError, match="无法解密"):
        other.decrypt_key(encrypted)
    asyncio.run(client.close())
    asyncio.run(other.close())


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (401, "API Key 无效"),
        (403, "没有调用该模型的权限"),
        (404, "模型名称不存在"),
        (429, "额度不足"),
        (500, "暂时不可用"),
    ],
)
def test_provider_errors_are_actionable_without_echoing_response(
    status_code: int, message: str
) -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(status_code, json={"error": {"message": "secret upstream detail"}})
    )
    client = SemanticDraftLlm(settings(), transport)
    with pytest.raises(SemanticLlmError, match=message) as captured:
        asyncio.run(client.enrich(baseline()))
    assert "secret upstream detail" not in str(captured.value)
    asyncio.run(client.close())


def test_connection_checks_chat_protocol_without_requiring_semantic_draft() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "glm-5.3"
        assert "response_format" not in payload
        assert "thinking" not in payload
        assert "temperature" not in payload
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    client = SemanticDraftLlm(settings(), httpx.MockTransport(handler))
    asyncio.run(
        client.test_connection(
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key="secret",
            model="glm-5.3",
        )
    )
    asyncio.run(client.close())


def test_glm_semantic_enrichment_uses_fast_non_reasoning_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["thinking"] == {"type": "disabled"}
        assert "temperature" not in payload
        assert payload["max_tokens"] == 4096
        result = {
            "model": {}, "identifiers": baseline()["identifiers"], "dimensions": [],
            "measures": baseline()["measures"], "drilldown_path": [],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    client = SemanticDraftLlm(settings(), httpx.MockTransport(handler))
    enriched = asyncio.run(client.enrich_with(
        baseline(), base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="secret", model="glm-5.3",
    ))
    assert enriched["generation"]["ai_generated"] is True
    asyncio.run(client.close())


def test_glm_semantic_enrichment_supports_deep_reasoning_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["thinking"] == {"type": "enabled"}
        assert payload["max_tokens"] == 16384
        result = {
            "model": {}, "identifiers": baseline()["identifiers"], "dimensions": [],
            "measures": baseline()["measures"], "drilldown_path": [],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    client = SemanticDraftLlm(settings(), httpx.MockTransport(handler))
    enriched = asyncio.run(client.enrich_with(
        baseline(), base_url="https://open.bigmodel.cn/api/paas/v4",
        api_key="secret", model="glm-5.3", reasoning_mode="deep",
    ))
    assert enriched["generation"]["reasoning_mode"] == "deep"
    assert "深度模式" in enriched["generation"]["label"]
    asyncio.run(client.close())


def test_invalid_model_metadata_falls_back_without_weakening_field_validation() -> None:
    result = {
        "model": "orders_model",
        "identifiers": baseline()["identifiers"],
        "dimensions": [],
        "measures": baseline()["measures"],
        "drilldown_path": [],
    }
    transport = httpx.MockTransport(lambda _: httpx.Response(
        200, json={"choices": [{"message": {"content": json.dumps(result)}}]},
    ))
    client = SemanticDraftLlm(settings(), transport)
    enriched = asyncio.run(client.enrich(baseline()))
    assert enriched["model"] == baseline()["model"]
    assert enriched["generation"]["ai_generated"] is True
    asyncio.run(client.close())


def test_connection_accepts_reasoning_model_before_final_content() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "正在生成最终答案",
                        }
                    }
                ]
            },
        )
    )
    client = SemanticDraftLlm(settings(), transport)
    asyncio.run(
        client.test_connection(
            base_url="https://open.bigmodel.cn/api/paas/v4",
            api_key="secret",
            model="glm-5.3",
        )
    )
    asyncio.run(client.close())


def test_enrichment_accepts_json_inside_markdown_fence() -> None:
    result = {
        "model": {"name": "订单", "biz_name": "orders", "description": ""},
        "identifiers": [{"name": "订单", "field": "order_id", "type": "primary"}],
        "dimensions": [],
        "measures": [{"name": "金额", "field": "amount", "aggregation": "SUM"}],
        "drilldown_path": [],
    }
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": f"```json\n{json.dumps(result)}\n```"}}
                ]
            },
        )
    )
    client = SemanticDraftLlm(settings(), transport)
    enriched = asyncio.run(client.enrich(baseline()))
    assert enriched["generation"]["ai_generated"] is True
    asyncio.run(client.close())

from __future__ import annotations

import asyncio
import json

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

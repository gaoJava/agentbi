"""Contract tests for the inspected SuperSonic parse/execute protocol."""

from __future__ import annotations

import asyncio
import json
import unittest

import httpx

from agentbi.config import Settings
from agentbi.models import Actor, AnalyzeRequest, ScreenContext, ScreenFilter
from agentbi.supersonic import SuperSonicClient, UpstreamError


def settings() -> Settings:
    return Settings(
        api_key="x" * 32,
        supersonic_base_url="http://supersonic.test",
        supersonic_token="Bearer test-token",
        request_timeout_seconds=2,
        max_result_rows=500,
        allowed_origins=("http://localhost:8088",),
    )


def analyze_request() -> AnalyzeRequest:
    return AnalyzeRequest(
        question="为什么销售额下降",
        actor=Actor(subject="user-1", roles=["Analyst"]),
        context=ScreenContext(
            dashboard_id="7",
            semantic_model_id=1,
            time_range="本月",
            filters=[ScreenFilter(field="region", operator="EQ", value="华东")],
        ),
    )


class SuperSonicClientTest(unittest.TestCase):
    def test_lists_sanitized_live_semantic_models(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/domain/list"):
                return httpx.Response(200, json={
                    "code": 200,
                    "data": [{"id": 1, "name": "超音数", "viewers": ["private-user"]}],
                })
            return httpx.Response(200, json={
                "code": 200,
                "data": [{
                    "id": 3, "name": "停留时长统计", "bizName": "stay_time",
                    "description": "受治理模型", "status": 1,
                    "modelDetail": {"sqlQuery": "SELECT secret"},
                    "viewers": ["private-user"],
                }],
            })

        client = SuperSonicClient(settings(), httpx.MockTransport(handler))
        models = asyncio.run(client.list_semantic_models())
        asyncio.run(client.close())

        self.assertEqual(models, [{
            "id": 3, "key": "supersonic:3", "name": "停留时长统计",
            "biz_name": "stay_time", "description": "受治理模型",
            "domain_id": 1, "domain_name": "超音数", "status": "active",
        }])
        self.assertNotIn("sql", str(models).lower())
        self.assertNotIn("private-user", str(models))

    def test_calls_parse_then_execute_and_unwraps_result_data(self):
        requests: list[tuple[str, dict]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append((request.url.path, payload))
            self.assertEqual(request.headers["Authorization"], "Bearer test-token")
            if request.url.path.endswith("/parse"):
                return httpx.Response(200, json={
                    "code": 200,
                    "data": {
                        "chatId": 9,
                        "queryId": 42,
                        "state": "COMPLETED",
                        "selectedParses": [
                            {"id": 1, "queryMode": "WEB_PAGE", "sqlInfo": {"querySQL": None}},
                            {
                                "id": 3,
                                "queryMode": "METRIC",
                                "sqlInfo": {"querySQL": "SELECT governed_metric"},
                            },
                        ],
                        "candidateParses": [],
                    },
                })
            return httpx.Response(200, json={
                "code": 200,
                "data": {"queryId": None, "queryResults": [{"amount": 1200}]},
            })

        client = SuperSonicClient(settings(), httpx.MockTransport(handler))
        result = asyncio.run(client.query(analyze_request()))
        asyncio.run(client.close())

        self.assertEqual(result["queryId"], 42)
        self.assertGreater(result["chatId"], 0)
        self.assertEqual([item[0] for item in requests], [
            "/api/chat/query/parse", "/api/chat/query/execute"
        ])
        self.assertEqual(requests[1][1]["parseId"], 3)
        self.assertEqual(requests[0][1]["chatId"], 0)
        self.assertEqual(requests[1][1]["chatId"], 0)
        self.assertIn("筛选条件：region EQ 华东", requests[0][1]["queryText"])

    def test_keeps_actor_bound_history_for_follow_up(self):
        parse_questions: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/parse"):
                payload = json.loads(request.content)
                self.assertEqual(payload["chatId"], 0)
                parse_questions.append(payload["queryText"])
                return httpx.Response(200, json={
                    "code": 200,
                    "data": {
                        "chatId": 0,
                        "queryId": 43,
                        "state": "COMPLETED",
                        "selectedParses": [{
                            "id": 4,
                            "sqlInfo": {"querySQL": "SELECT governed_metric"},
                        }],
                    },
                })
            return httpx.Response(200, json={
                "code": 200,
                "data": {"queryResults": []},
            })

        first_turn = analyze_request()
        client = SuperSonicClient(settings(), httpx.MockTransport(handler))

        async def run_turns():
            first = await client.query(first_turn)
            follow_up = analyze_request()
            follow_up.chat_id = first["chatId"]
            follow_up.question = "哪个地区下降最多"
            second = await client.query(follow_up)
            impostor = analyze_request()
            impostor.actor.subject = "user-2"
            impostor.chat_id = first["chatId"]
            with self.assertRaises(UpstreamError):
                await client.query(impostor)
            await client.close()
            return first, second

        first, second = asyncio.run(run_turns())

        self.assertEqual(first["chatId"], second["chatId"])
        self.assertEqual(len(parse_questions), 2)
        self.assertIn("同一用户的历史问题：为什么销售额下降", parse_questions[1])

    def test_rejects_failed_parse_without_execution(self):
        calls = 0

        def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={
                "code": 200,
                "data": {"state": "FAILED", "selectedParses": [], "candidateParses": []},
            })

        client = SuperSonicClient(settings(), httpx.MockTransport(handler))
        with self.assertRaises(UpstreamError):
            asyncio.run(client.query(analyze_request()))
        asyncio.run(client.close())
        self.assertEqual(calls, 1)

    def test_rejects_external_candidate_without_governed_query(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "code": 200,
                "data": {
                    "state": "COMPLETED",
                    "selectedParses": [{
                        "id": 1,
                        "queryMode": "WEB_PAGE",
                        "properties": {"url": "https://untrusted.example"},
                        "sqlInfo": {"querySQL": None},
                    }],
                    "candidateParses": [],
                },
            })

        client = SuperSonicClient(settings(), httpx.MockTransport(handler))
        with self.assertRaises(UpstreamError):
            asyncio.run(client.query(analyze_request()))
        asyncio.run(client.close())

    def test_sanitizes_upstream_timeout(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("private upstream detail", request=request)

        client = SuperSonicClient(settings(), httpx.MockTransport(handler))
        with self.assertRaisesRegex(UpstreamError, "semantic query failed"):
            asyncio.run(client.query(analyze_request()))
        asyncio.run(client.close())


if __name__ == "__main__":
    unittest.main()

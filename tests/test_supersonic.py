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
    def test_compiles_explicit_top_n_question(self):
        self.assertEqual(
            SuperSonicClient._ranking_query("按发行商统计全球销量前 10 名"),
            ("publisher", "global_sales", 10),
        )

    def test_compiles_recommended_chart_title_as_default_top_ten(self):
        self.assertEqual(
            SuperSonicClient._ranking_query("发行商销售排名"),
            ("publisher", "global_sales", 10),
        )

    def test_compiles_explicit_grouped_metric_question(self):
        self.assertEqual(
            SuperSonicClient._grouped_metric_query("各游戏类型的全球销量是多少？"),
            ("genre", "global_sales", 100),
        )

    def test_compiles_top_n_sum_and_average_calculations(self):
        self.assertEqual(
            SuperSonicClient._calculation_query("全球销量前三名发行商的销量合计是多少？"),
            {"operation": "sum", "dimension": "publisher", "metric": "global_sales", "limit": 3},
        )
        self.assertEqual(
            SuperSonicClient._calculation_query("全球销量前 5 名发行商的平均销量是多少？"),
            {"operation": "average", "dimension": "publisher", "metric": "global_sales", "limit": 5},
        )

    def test_calculates_difference_and_percentage_from_real_rows(self):
        calculation = SuperSonicClient._calculation_query("Action 类型的全球销量比 Sports 高多少？")
        self.assertIsNotNone(calculation)
        result = SuperSonicClient._calculate(
            calculation,
            [{"genre": "Action", "global_sales": 1751.17},
             {"genre": "Sports", "global_sales": 1330.93}],
            "genre", "global_sales",
        )
        self.assertIn("420.24", result["response"])
        self.assertIn("31.57%", result["response"])
        self.assertIn("1,751.17 - 1,330.93", result["detail"])

    def test_calculates_top_five_average_from_real_rows(self):
        calculation = SuperSonicClient._calculation_query("全球销量前 5 名发行商的平均销量是多少？")
        result = SuperSonicClient._calculate(
            calculation,
            [{"publisher": name, "global_sales": value} for name, value in [
                ("Nintendo", 1786.56), ("Electronic Arts", 1110.32), ("Activision", 727.46),
                ("Sony Computer Entertainment", 607.5), ("Ubisoft", 474.43),
            ]],
            "publisher", "global_sales",
        )
        self.assertIn("941.25", result["response"])
        self.assertEqual(len(result["rows"]), 5)

    def test_compiles_rank_value_difference_bottom_ratio_and_share(self):
        cases = {
            "发行商销量第2名和第3名差多少": "rank_difference",
            "全球销量第3名发行商的销量是多少": "rank_value",
            "全球销量最低 3 名平台的平均销量是多少": "average",
            "Action 类型的全球销量是 Sports 的几倍": "ratio",
            "Action 类型的全球销量占全部销量比例是多少": "share",
        }
        for question, operation in cases.items():
            with self.subTest(question=question):
                plan = SuperSonicClient._calculation_query(question)
                self.assertIsNotNone(plan)
                self.assertEqual(plan["operation"], operation)

    def test_calculates_rank_difference_ratio_and_share(self):
        publisher_rows = [
            {"publisher": "Nintendo", "global_sales": 1786.56},
            {"publisher": "Electronic Arts", "global_sales": 1110.32},
            {"publisher": "Activision", "global_sales": 727.46},
        ]
        rank_result = SuperSonicClient._calculate(
            SuperSonicClient._calculation_query("发行商销量第2名和第3名差多少"),
            publisher_rows, "publisher", "global_sales",
        )
        self.assertIn("382.86", rank_result["response"])
        self.assertIn("第2名 Electronic Arts", rank_result["response"])

        genre_rows = [
            {"genre": "Action", "global_sales": 1751.17},
            {"genre": "Sports", "global_sales": 1330.93},
        ]
        ratio_result = SuperSonicClient._calculate(
            SuperSonicClient._calculation_query("Action 类型的全球销量是 Sports 的几倍"),
            genre_rows, "genre", "global_sales",
        )
        self.assertIn("1.32 倍", ratio_result["response"])
        share_result = SuperSonicClient._calculate(
            SuperSonicClient._calculation_query("Action 类型的全球销量占全部销量比例是多少"),
            genre_rows, "genre", "global_sales",
        )
        self.assertIn("56.82%", share_result["response"])

    def test_rejects_successful_result_with_unrelated_requested_fields(self):
        with self.assertRaisesRegex(UpstreamError, "发行商.*全球销量"):
            SuperSonicClient._validate_question_result(
                "按发行商统计全球销量前 10 名",
                {"queryResults": [{"sys_imp_date": "2026-08-02", "department": "sales", "pv": 4}]},
            )

    def test_limits_ranked_result_to_requested_top_n(self):
        result = {"queryResults": [
            {"publisher": f"P{index}", "global_sales": 100 - index} for index in range(20)
        ]}
        SuperSonicClient._validate_question_result("按发行商统计全球销量前 10 名", result)
        self.assertEqual(len(result["queryResults"]), 10)

    def test_selects_only_candidate_for_requested_semantic_model(self):
        candidates = [
            {"id": 1, "viewId": 2, "sqlInfo": {"querySQL": "SELECT wrong_model"}},
            {"id": 2, "viewId": 9, "sqlInfo": {"querySQL": "SELECT requested_model"}},
        ]
        selected = SuperSonicClient._select_governed_query(candidates, 9)
        self.assertEqual(selected["id"], 2)

    def test_resolves_generated_database_id_and_reuses_same_source(self):
        requests: list[tuple[str, dict | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content) if request.content else None
            requests.append((request.url.path, payload))
            if request.url.path.endswith("/testConnect"):
                return httpx.Response(200, json={"code": 200, "data": True})
            if request.url.path.endswith("/getDatabaseList"):
                return httpx.Response(200, json={
                    "code": 200,
                    "data": [{
                        "id": 2, "name": "examples", "type": "postgresql",
                        "host": "127.0.0.1", "port": "5432", "database": "examples",
                        "username": "superset",
                    }],
                })
            return httpx.Response(200, json={
                "code": 200,
                "data": {"id": None, "name": "examples", "type": "postgresql"},
            })

        payload = {
            "name": "examples", "type": "postgresql", "host": "127.0.0.1",
            "port": "5432", "database": "examples", "username": "superset",
            "password": "secret", "admins": ["admin"], "viewers": ["admin"],
        }
        client = SuperSonicClient(settings(), httpx.MockTransport(handler))
        created = asyncio.run(client.create_database(payload))
        asyncio.run(client.close())

        self.assertEqual(created, {"id": 2, "name": "examples", "type": "postgresql"})
        save_payload = next(body for path, body in requests if path.endswith("createOrUpdateDatabase"))
        self.assertEqual(save_payload["id"], 2)

    def test_lists_sanitized_live_semantic_models(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/domain/list"):
                return httpx.Response(200, json={
                    "code": 200,
                    "data": [{"id": 1, "name": "超音数", "viewers": ["private-user"]}],
                })
            if request.url.path.endswith("/getDatabaseList"):
                return httpx.Response(200, json={
                    "code": 200, "data": [{"id": 2, "name": "业务库", "password": "secret"}],
                })
            if request.url.path.endswith("/view/getViewList"):
                return httpx.Response(200, json={
                    "code": 200,
                    "data": [{
                        "id": 9, "name": "停留时长统计", "bizName": "stay_time_view",
                        "description": "受治理查询视图", "status": 1,
                        "viewDetail": {"viewModelConfigs": [{"id": 3, "includesAll": True}]},
                    }],
                })
            return httpx.Response(200, json={
                "code": 200,
                "data": [{
                    "id": 3, "name": "停留时长统计", "bizName": "stay_time",
                    "description": "受治理模型", "status": 1, "databaseId": 2,
                    "modelDetail": {"sqlQuery": "SELECT secret"},
                    "viewers": ["private-user"],
                }],
            })

        client = SuperSonicClient(settings(), httpx.MockTransport(handler))
        models = asyncio.run(client.list_semantic_models())
        asyncio.run(client.close())

        self.assertEqual(models, [{
            "id": 9, "key": "supersonic:9", "model_id": 3, "name": "停留时长统计",
            "biz_name": "stay_time_view", "description": "受治理查询视图",
            "domain_id": 1, "domain_name": "超音数", "status": "active",
            "database_id": 2, "database_name": "业务库",
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

    def test_reads_postgresql_columns_with_database_catalog_name(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertTrue(
                request.url.path.endswith(
                    "/api/semantic/database/getColumns/2/examples/video_game_sales"
                )
            )
            return httpx.Response(200, json={
                "code": 200,
                "data": {"resultList": [{"name": "genre"}, {"name": "global_sales"}]},
            })

        client = SuperSonicClient(settings(), httpx.MockTransport(handler))
        columns = asyncio.run(
            client.get_database_columns(2, "examples", "video_game_sales")
        )
        asyncio.run(client.close())
        self.assertEqual(columns, {"genre", "global_sales"})


if __name__ == "__main__":
    unittest.main()

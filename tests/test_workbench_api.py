"""HTTP-level acceptance tests for login, logout and the product shell."""

from __future__ import annotations

import os
from dataclasses import replace

# ``agentbi.main`` exports an ASGI app at import time and intentionally fails
# closed without secrets. Tests provide isolated non-production values first.
os.environ.setdefault("AGENTBI_API_KEY", "t" * 32)
os.environ.setdefault("AGENTBI_SESSION_SECRET", "q" * 32)

from fastapi.testclient import TestClient

from agentbi.config import Settings
from agentbi.main import create_app
from agentbi.orchestrator import Orchestrator
from agentbi.superset import SupersetApiError


def settings() -> Settings:
    return Settings(
        api_key="a" * 32,
        supersonic_base_url="http://localhost:9080",
        supersonic_token=None,
        request_timeout_seconds=20,
        max_result_rows=500,
        allowed_origins=("http://localhost:8090",),
        session_secret="s" * 32,
        demo_login_enabled=True,
        demo_user_password="user-password",
        demo_admin_password="admin-password",
    )


def test_product_shell_and_user_session_flow() -> None:
    with TestClient(create_app(settings())) as client:
        shell = client.get("/app")
        assert shell.status_code == 200
        assert "generated/workbench-runtime.js?v=20260829.7" in shell.text
        assert "app.js?v=20260829.9" in shell.text
        assert "尚未绑定真实下钻数据" in shell.text
        runtime = client.get("/app/assets/generated/workbench-runtime.js")
        assert runtime.status_code == 200
        assert "AgentBI.request" in runtime.text
        assert "SessionClient" in runtime.text
        assert "SupersetWorkspaceClient" in runtime.text
        assert "parseManagedCharts" in runtime.text
        assert "parseGovernedUsers" in runtime.text
        assert "parseSupersetDataAssets" in runtime.text
        assert "parseSavedReports" in runtime.text
        assert "parseAuditEvents" in runtime.text
        assert client.get("/api/v1/auth/me").status_code == 401

        login = client.post(
            "/api/v1/auth/login",
            json={"username": "user", "password": "user-password"},
        )
        assert login.status_code == 200
        user = login.json()["user"]
        assert user["role"] == "user"
        assert "user:manage" not in user["permissions"]
        assert client.get("/api/v1/auth/me").status_code == 200

        assert client.post("/api/v1/auth/logout").status_code == 403
        logout = client.post(
            "/api/v1/auth/logout",
            headers={"X-AgentBI-CSRF": user["csrf_token"]},
        )
        assert logout.status_code == 204
        assert client.get("/api/v1/auth/me").status_code == 401


def test_login_rejects_wrong_password() -> None:
    with TestClient(create_app(settings())) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrong"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "用户名或密码错误"


def test_workbench_analysis_uses_signed_session_identity() -> None:
    class CapturingSuperSonic:
        request = None

        async def query(self, request):
            self.request = request
            return {
                "queryId": 73,
                "queryResults": [{"department": "研发", "visits": 19}],
                "queryTimeCost": 8,
                "querySql": "SELECT department, COUNT(*) FROM visits GROUP BY department",
                "response": "研发部门访问次数最高。",
            }

        async def close(self) -> None:
            return None

    upstream = CapturingSuperSonic()
    app = create_app(settings())
    app.state.orchestrator = Orchestrator(settings(), upstream)  # type: ignore[arg-type]
    with TestClient(app) as client:
        user = client.post(
            "/api/v1/auth/login",
            json={"username": "user", "password": "user-password"},
        ).json()["user"]
        payload = {
            "question": "访问次数最高的部门",
            "context": {
                "dashboard_id": "sales-dashboard",
                "chart_id": "42",
                "dataset_id": "21",
                "semantic_model_id": 1,
                "time_range": "最近30天",
                "filters": [],
            },
            "client_request_id": "workbench_123456",
        }
        missing_csrf = client.post("/api/v1/workbench/analyze", json=payload)
        assert missing_csrf.status_code == 403

        response = client.post(
            "/api/v1/workbench/analyze",
            json=payload,
            headers={"X-AgentBI-CSRF": user["csrf_token"]},
        )
        assert response.status_code == 200
        assert response.json()["data"] == [{"department": "研发", "visits": 19}]
        assert response.json()["evidence"]["sql_fingerprint"]
        assert upstream.request.actor.subject == user["subject"]
        assert upstream.request.actor.roles == user["roles"]


def test_workbench_analysis_rejects_browser_supplied_actor() -> None:
    app = create_app(settings())
    with TestClient(app) as client:
        user = client.post(
            "/api/v1/auth/login",
            json={"username": "user", "password": "user-password"},
        ).json()["user"]
        response = client.post(
            "/api/v1/workbench/analyze",
            headers={"X-AgentBI-CSRF": user["csrf_token"]},
            json={
                "question": "访问次数最高的部门",
                "actor": {"subject": "admin", "roles": ["Admin"]},
                "context": {
                    "dashboard_id": "sales-dashboard",
                    "semantic_model_id": 1,
                    "time_range": "最近30天",
                },
            },
        )
        assert response.status_code == 422


def test_user_reads_sanitized_live_supersonic_models() -> None:
    class FakeSuperSonicClient:
        async def list_semantic_models(self):
            return [{
                "id": 1, "key": "supersonic:1", "name": "用户部门",
                "biz_name": "user_department", "description": "用户部门信息",
                "domain_id": 1, "domain_name": "超音数", "status": "active",
            }]

        async def close(self) -> None:
            return None

    app = create_app(settings())
    app.state.supersonic_client = FakeSuperSonicClient()
    with TestClient(app) as client:
        assert client.get("/api/v1/supersonic/models").status_code == 401
        client.post(
            "/api/v1/auth/login",
            json={"username": "user", "password": "user-password"},
        )
        response = client.get("/api/v1/supersonic/models")
        assert response.status_code == 200
        assert response.json()["count"] == 1
        assert response.json()["models"][0]["key"] == "supersonic:1"
        assert "sql" not in str(response.json()).lower()


def test_admin_can_sync_and_select_superset_home_dashboard() -> None:
    class FakeSupersetClient:
        async def list_dashboards(self) -> list[dict[str, object]]:
            return [
                {"superset_id": 5, "title": "Sales Dashboard", "slug": None,
                 "url_path": "/superset/dashboard/5/", "chart_count": 10, "published": True},
                {"superset_id": 7, "title": "Executive Dashboard", "slug": "executive",
                 "url_path": "/superset/dashboard/7/", "chart_count": 4, "published": True},
            ]

    app = create_app(settings())
    app.state.superset_client = FakeSupersetClient()
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin-password"}
        ).json()["user"]
        headers = {"X-AgentBI-CSRF": login["csrf_token"]}
        synced = client.post("/api/v1/admin/superset/dashboards/sync", headers=headers)
        assert synced.status_code == 200
        assert synced.json()["count"] == 2
        assert next(item for item in synced.json()["dashboards"] if item["is_home"])["superset_id"] == 5

        selected = client.post("/api/v1/admin/superset/dashboards/7/home", headers=headers)
        assert selected.status_code == 200
        assert selected.json()["dashboard"]["is_home"] is True


def test_admin_reads_real_superset_data_assets_without_secrets() -> None:
    class FakeSupersetClient:
        async def list_data_assets(self) -> dict[str, list[dict[str, object]]]:
            return {
                "databases": [{"superset_id": 1, "name": "examples", "backend": "postgresql",
                               "expose_in_sqllab": True, "allow_file_upload": True,
                               "dataset_count": 1}],
                "datasets": [{"superset_id": 20, "name": "video_game_sales",
                              "database_id": 1, "database_name": "examples", "schema": "public",
                              "kind": "physical", "description": "", "explore_url": "/explore/"}],
            }

    app = create_app(settings())
    app.state.superset_client = FakeSupersetClient()
    with TestClient(app) as client:
        client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin-password"}
        )
        response = client.get("/api/v1/admin/superset/data-assets")
        assert response.status_code == 200
        assert response.json()["databases"][0]["name"] == "examples"
        assert response.json()["datasets"][0]["name"] == "video_game_sales"
        assert "password" not in response.text


def test_database_connection_secret_is_forwarded_but_not_returned() -> None:
    class FakeSupersetClient:
        received_uri = ""

        async def test_database_connection(self, database_name: str, sqlalchemy_uri: str) -> None:
            self.received_uri = sqlalchemy_uri

        async def create_database(
            self, database_name: str, sqlalchemy_uri: str, expose_in_sqllab: bool
        ) -> dict[str, object]:
            self.received_uri = sqlalchemy_uri
            return {"superset_id": 9, "name": database_name}

    fake = FakeSupersetClient()
    app = create_app(settings())
    app.state.superset_client = fake
    with TestClient(app) as client:
        user = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin-password"}
        ).json()["user"]
        payload = {
            "database_name": "sales_prod",
            "sqlalchemy_uri": "postgresql://analyst:top-secret@db.local:5432/sales",
            "expose_in_sqllab": True,
        }
        headers = {"X-AgentBI-CSRF": user["csrf_token"]}
        tested = client.post("/api/v1/admin/superset/databases/test", json=payload, headers=headers)
        created = client.post("/api/v1/admin/superset/databases", json=payload, headers=headers)
        assert tested.status_code == 200
        assert created.status_code == 201
        assert fake.received_uri == payload["sqlalchemy_uri"]
        assert "top-secret" not in tested.text
        assert "top-secret" not in created.text


def test_structured_database_form_builds_encoded_uri_server_side() -> None:
    class FakeSupersetClient:
        received_uri = ""

        async def test_database_connection(self, database_name: str, sqlalchemy_uri: str) -> None:
            self.received_uri = sqlalchemy_uri

    fake = FakeSupersetClient()
    app = create_app(settings())
    app.state.superset_client = fake
    with TestClient(app) as client:
        user = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin-password"}
        ).json()["user"]
        response = client.post(
            "/api/v1/admin/superset/databases/test",
            json={"database_name": "doris_prod", "connection_mode": "form", "engine": "doris",
                  "host": "10.0.0.8", "port": 9030, "database": "sales warehouse",
                  "username": "bi_user", "password": "p@ss:word", "sqlalchemy_uri": "",
                  "expose_in_sqllab": True},
            headers={"X-AgentBI-CSRF": user["csrf_token"]},
        )
        assert response.status_code == 200
        assert fake.received_uri == "mysql://bi_user:p%40ss%3Aword@10.0.0.8:9030/sales%20warehouse"
        assert "p@ss" not in response.text


def test_admin_creates_real_superset_dataset() -> None:
    class FakeSupersetClient:
        async def create_dataset(
            self, database_id: int, schema_name: str, table_name: str
        ) -> dict[str, object]:
            assert (database_id, schema_name, table_name) == (1, "public", "sales_orders")
            return {"superset_id": 88, "name": table_name}

    app = create_app(settings())
    app.state.superset_client = FakeSupersetClient()
    with TestClient(app) as client:
        user = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin-password"}
        ).json()["user"]
        response = client.post(
            "/api/v1/admin/superset/datasets",
            json={"database_id": 1, "schema_name": "public", "table_name": "sales_orders"},
            headers={"X-AgentBI-CSRF": user["csrf_token"]},
        )
        assert response.status_code == 201
        assert response.json()["dataset"]["superset_id"] == 88


def test_superset_asset_deletion_conflicts_are_safe() -> None:
    class FakeSupersetClient:
        async def get_dataset(self, dataset_id: int) -> dict[str, object]:
            return {"superset_id": dataset_id, "name": "orders", "schema": "public",
                    "database_name": "sales", "columns": [], "metrics": []}

        async def delete_dataset(self, dataset_id: int) -> None:
            raise SupersetApiError("Dataset 正被 3 个图表引用，不能删除")

        async def delete_database(self, database_id: int) -> None:
            raise SupersetApiError("数据库仍关联 3 个图表和 1 个仪表盘，不能删除")

    app = create_app(settings())
    app.state.superset_client = FakeSupersetClient()
    with TestClient(app) as client:
        user = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin-password"}
        ).json()["user"]
        headers = {"X-AgentBI-CSRF": user["csrf_token"]}
        assert client.get("/api/v1/admin/superset/datasets/3").status_code == 200
        dataset = client.delete("/api/v1/admin/superset/datasets/3", headers=headers)
        database = client.delete("/api/v1/admin/superset/databases/1", headers=headers)
        assert dataset.status_code == 409
        assert database.status_code == 409


def test_admin_updates_superset_database_and_dataset_metadata() -> None:
    class FakeSupersetClient:
        async def update_database(
            self, database_id: int, database_name: str, sqlalchemy_uri: str,
            expose_in_sqllab: bool,
        ) -> dict[str, object]:
            assert (database_id, database_name, sqlalchemy_uri, expose_in_sqllab) == (
                1, "sales_prod", "", False,
            )
            return {"superset_id": database_id, "name": database_name}

        async def update_dataset(self, dataset_id: int, description: str) -> dict[str, object]:
            assert (dataset_id, description) == (3, "销售订单事实表")
            return {"superset_id": dataset_id, "description": description}

    app = create_app(settings())
    app.state.superset_client = FakeSupersetClient()
    with TestClient(app) as client:
        user = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin-password"}
        ).json()["user"]
        headers = {"X-AgentBI-CSRF": user["csrf_token"]}
        database = client.put(
            "/api/v1/admin/superset/databases/1",
            json={"database_name": "sales_prod", "sqlalchemy_uri": "", "expose_in_sqllab": False},
            headers=headers,
        )
        dataset = client.put(
            "/api/v1/admin/superset/datasets/3",
            json={"description": "销售订单事实表"}, headers=headers,
        )
        assert database.status_code == 200
        assert dataset.status_code == 200


def test_superset_workspace_rejects_uncontrolled_dashboard_url() -> None:
    unsafe_settings = replace(
        settings(),
        superset_dashboard_path="https://attacker.example/superset/dashboard/1/",
    )
    with TestClient(create_app(unsafe_settings)) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "user", "password": "user-password"},
        )
        assert login.status_code == 200

        response = client.get("/api/v1/superset/workspace")
        assert response.status_code == 200
        workspace = response.json()["workspace"]
        assert workspace == {
            "available": False,
            "status": "misconfigured",
            "message": "Superset 仪表盘路径配置无效",
        }


def test_admin_creates_governed_chart_and_drilldown() -> None:
    with TestClient(create_app(settings())) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin-password"},
        )
        csrf_token = login.json()["user"]["csrf_token"]
        payload = {
            "chart_key": "customer_growth",
            "title": "新增客户趋势",
            "metric": "新增客户数",
            "dataset_name": "customer_sales",
            "visualization_type": "bar",
            "semantic_model": "customer_model",
            "dimensions": ["区域", "渠道", "销售人员"],
        }

        assert client.post("/api/v1/admin/charts", json=payload).status_code == 403
        created = client.post(
            "/api/v1/admin/charts",
            json=payload,
            headers={"X-AgentBI-CSRF": csrf_token},
        )
        assert created.status_code == 201
        assert created.json()["chart"]["dimensions"] == ["区域", "渠道", "销售人员"]

        charts = client.get("/api/v1/charts")
        assert charts.status_code == 200
        assert charts.json()["charts"][0]["chart_key"] == "customer_growth"

        duplicate = client.post(
            "/api/v1/admin/charts",
            json=payload,
            headers={"X-AgentBI-CSRF": csrf_token},
        )
        assert duplicate.status_code == 409

        updated_payload = {
            "title": "新增客户趋势（已调整）",
            "metric": "活跃客户数",
            "dataset_name": "customer_sales_v2",
            "visualization_type": "line",
            "semantic_model": "customer_model_v2",
            "dimensions": ["区域", "渠道", "门店"],
        }
        assert client.put("/api/v1/admin/charts/customer_growth", json=updated_payload).status_code == 403
        updated = client.put(
            "/api/v1/admin/charts/customer_growth",
            json=updated_payload,
            headers={"X-AgentBI-CSRF": csrf_token},
        )
        assert updated.status_code == 200
        assert updated.json()["chart"]["title"] == "新增客户趋势（已调整）"
        assert updated.json()["chart"]["dimensions"] == ["区域", "渠道", "门店"]

        blocked_delete = client.delete(
            "/api/v1/admin/charts/customer_growth",
            headers={"X-AgentBI-CSRF": csrf_token},
        )
        assert blocked_delete.status_code == 409
        assert blocked_delete.json()["detail"] == "请先下线图表，再执行删除"

        offline = client.patch(
            "/api/v1/admin/charts/customer_growth/publication",
            json={"published": False},
            headers={"X-AgentBI-CSRF": csrf_token},
        )
        assert offline.status_code == 200
        assert offline.json()["chart"]["is_published"] is False
        assert client.get("/api/v1/charts").json()["charts"] == []
        admin_charts = client.get("/api/v1/admin/charts").json()["charts"]
        assert admin_charts[0]["status"] == "offline"

        deleted = client.delete(
            "/api/v1/admin/charts/customer_growth",
            headers={"X-AgentBI-CSRF": csrf_token},
        )
        assert deleted.status_code == 204
        assert client.get("/api/v1/admin/charts").json()["charts"] == []


def test_normal_user_cannot_create_chart() -> None:
    with TestClient(create_app(settings())) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "user", "password": "user-password"},
        )
        response = client.post(
            "/api/v1/admin/charts",
            headers={"X-AgentBI-CSRF": login.json()["user"]["csrf_token"]},
            json={
                "chart_key": "forbidden_chart",
                "title": "越权图表",
                "metric": "销售收入",
                "dataset_name": "sales",
                "visualization_type": "table",
                "semantic_model": "sales_model",
                "dimensions": ["区域", "门店"],
            },
        )
        assert response.status_code == 403
        assert client.get("/api/v1/admin/charts").status_code == 403
        assert client.patch(
            "/api/v1/admin/charts/forbidden_chart/publication",
            headers={"X-AgentBI-CSRF": login.json()["user"]["csrf_token"]},
            json={"published": False},
        ).status_code == 403


def test_navigation_modules_use_session_scoped_data() -> None:
    with TestClient(create_app(settings())) as client:
        user_login = client.post(
            "/api/v1/auth/login",
            json={"username": "user", "password": "user-password"},
        )
        user = user_login.json()["user"]
        dashboards = client.get("/api/v1/dashboards")
        assert dashboards.status_code == 200
        assert len(dashboards.json()["dashboards"]) == 1
        assert dashboards.json()["dashboards"][0]["data_scope"] == "华东区域"

        payload = {"title": "华东经营快照", "dashboard_name": "销售经营分析"}
        assert client.post("/api/v1/reports", json=payload).status_code == 403
        created = client.post(
            "/api/v1/reports",
            json=payload,
            headers={"X-AgentBI-CSRF": user["csrf_token"]},
        )
        assert created.status_code == 201
        assert created.json()["report"]["data_scope"] == "华东区域"
        report_id = created.json()["report"]["id"]
        assert len(client.get("/api/v1/reports").json()["reports"]) == 1

        admin_login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "admin-password"},
        )
        assert admin_login.status_code == 200
        admin = admin_login.json()["user"]
        assert len(client.get("/api/v1/dashboards").json()["dashboards"]) == 2
        assert len(client.get("/api/v1/admin/users").json()["users"]) == 2
        roles = client.get("/api/v1/admin/roles")
        assert roles.status_code == 200
        assert {role["code"] for role in roles.json()["roles"]} == {"user", "admin"}
        assert client.get("/api/v1/admin/semantic-models").json()["models"][0]["name"] == "sales_model"
        assert client.get("/api/v1/admin/data-sources").json()["sources"][0]["name"] == "sales_orders"
        events = client.get("/api/v1/admin/audit-events")
        assert events.status_code == 200
        assert any(event["event_type"] == "report_created" for event in events.json()["events"])

        user_update = {
            "role": "user",
            "data_scope": "华南区域",
            "is_active": True,
        }
        assert client.put("/api/v1/admin/users/user", json=user_update).status_code == 403
        updated = client.put(
            "/api/v1/admin/users/user",
            json=user_update,
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert updated.status_code == 200
        assert updated.json()["user"]["data_scope"] == "华南区域"

        permissions = client.get("/api/v1/admin/permissions")
        assert permissions.status_code == 200
        assert len(permissions.json()["permissions"]) == 10
        role_payload = {
            "code": "regional_analyst",
            "name": "区域分析师",
            "description": "只使用分析能力",
            "permissions": ["workspace:view", "dashboard:view", "drilldown:use"],
        }
        assert client.post("/api/v1/admin/roles", json=role_payload).status_code == 403
        role_created = client.post(
            "/api/v1/admin/roles", json=role_payload,
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert role_created.status_code == 201
        role_updated = client.put(
            "/api/v1/admin/roles/regional_analyst",
            json={"name": "区域经营分析师", "description": "区域范围",
                  "permissions": ["workspace:view", "dashboard:view", "report:view",
                                  "datasource:manage"]},
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert role_updated.status_code == 200
        assert client.put(
            "/api/v1/admin/roles/admin",
            json={"name": "管理员", "description": "", "permissions": ["workspace:view"]},
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        ).status_code == 409

        new_user = {
            "username": "analyst2",
            "password": "safe-password",
            "display_name": "分析师二号",
            "role": "regional_analyst",
            "data_scope": "华北区域",
            "is_active": True,
        }
        assert client.post("/api/v1/admin/users", json=new_user).status_code == 403
        user_created = client.post(
            "/api/v1/admin/users",
            json=new_user,
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert user_created.status_code == 201
        assert user_created.json()["user"]["data_scope"] == "华北区域"
        assert len(client.get("/api/v1/admin/users").json()["users"]) == 3
        duplicate_user = client.post(
            "/api/v1/admin/users",
            json=new_user,
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert duplicate_user.status_code == 409
        assert client.delete(
            "/api/v1/admin/roles/regional_analyst",
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        ).status_code == 409
        disposable_role = client.post(
            "/api/v1/admin/roles",
            json={"code": "temporary", "name": "临时角色", "description": "",
                  "permissions": ["workspace:view"]},
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert disposable_role.status_code == 201
        assert client.delete(
            "/api/v1/admin/roles/temporary",
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        ).status_code == 204

        self_lockout = client.put(
            "/api/v1/admin/users/admin",
            json={"role": "user", "data_scope": "全部区域", "is_active": True},
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert self_lockout.status_code == 409
        assert self_lockout.json()["detail"] == "不能停用或降级当前管理员账号"

        assert client.post("/api/v1/admin/data-sources/sales_orders/test").status_code == 403
        missing_source = client.post(
            "/api/v1/admin/data-sources/not-found/test",
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert missing_source.status_code == 404
        missing_model = client.post(
            "/api/v1/admin/semantic-models/not-found/sync",
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert missing_model.status_code == 404

        source_payload = {
            "name": "crm_orders",
            "source_type": "Superset Dataset",
            "description": "CRM 订单数据集",
        }
        assert client.post("/api/v1/admin/data-sources", json=source_payload).status_code == 403
        source_created = client.post(
            "/api/v1/admin/data-sources", json=source_payload,
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert source_created.status_code == 201
        source_updated = client.put(
            "/api/v1/admin/data-sources/crm_orders",
            json={"source_type": "Superset Dataset", "description": "CRM 订单数据集", "status": "offline"},
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert source_updated.status_code == 200
        assert source_updated.json()["source"]["status"] == "offline"
        assert client.delete(
            "/api/v1/admin/data-sources/sales_orders",
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        ).status_code == 409
        assert client.delete(
            "/api/v1/admin/data-sources/crm_orders",
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        ).status_code == 204

        model_payload = {
            "name": "customer_value_model",
            "subject_area": "客户价值",
            "description": "客户价值分析模型",
        }
        model_created = client.post(
            "/api/v1/admin/semantic-models", json=model_payload,
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert model_created.status_code == 201
        model_updated = client.put(
            "/api/v1/admin/semantic-models/customer_value_model",
            json={"subject_area": "客户价值", "description": "客户价值分析模型", "status": "offline"},
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert model_updated.status_code == 200
        assert client.delete(
            "/api/v1/admin/semantic-models/customer_value_model",
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        ).status_code == 204

        assert client.delete(f"/api/v1/reports/{report_id}").status_code == 403
        deleted = client.delete(
            f"/api/v1/reports/{report_id}",
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert deleted.status_code == 204
        assert client.get("/api/v1/reports").json()["reports"] == []

        custom_login = client.post(
            "/api/v1/auth/login",
            json={"username": "analyst2", "password": "safe-password"},
        )
        assert custom_login.status_code == 200
        assert custom_login.json()["user"]["role"] == "regional_analyst"
        assert client.get("/api/v1/admin/data-sources").status_code == 200
        assert client.get("/api/v1/admin/users").status_code == 403

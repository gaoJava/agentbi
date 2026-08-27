"""HTTP-level acceptance tests for login, logout and the product shell."""

from __future__ import annotations

import os

# ``agentbi.main`` exports an ASGI app at import time and intentionally fails
# closed without secrets. Tests provide isolated non-production values first.
os.environ.setdefault("AGENTBI_API_KEY", "t" * 32)
os.environ.setdefault("AGENTBI_SESSION_SECRET", "q" * 32)

from fastapi.testclient import TestClient

from agentbi.config import Settings
from agentbi.main import create_app


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
        assert client.get("/app").status_code == 200
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

        assert client.delete(f"/api/v1/reports/{report_id}").status_code == 403
        deleted = client.delete(
            f"/api/v1/reports/{report_id}",
            headers={"X-AgentBI-CSRF": admin["csrf_token"]},
        )
        assert deleted.status_code == 204
        assert client.get("/api/v1/reports").json()["reports"] == []

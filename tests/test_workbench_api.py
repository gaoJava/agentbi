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

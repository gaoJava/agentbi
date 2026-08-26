"""Authentication and RBAC tests for the standalone workbench."""

from __future__ import annotations

import time

import pytest

from agentbi.auth import AuthenticationError, SessionManager
from agentbi.config import Settings


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


def test_user_session_contains_only_analyst_permissions() -> None:
    manager = SessionManager(settings())
    identity = manager.decode(manager.encode(manager.authenticate("user", "user-password")))

    assert identity.role == "user"
    assert identity.data_scope == "华东区域"
    assert "dashboard:view" in identity.public_payload()["permissions"]
    assert "user:manage" not in identity.public_payload()["permissions"]


def test_admin_session_contains_governance_permissions() -> None:
    manager = SessionManager(settings())
    identity = manager.decode(manager.encode(manager.authenticate("admin", "admin-password")))

    assert identity.role == "admin"
    assert identity.data_scope == "全部区域"
    assert "user:manage" in identity.public_payload()["permissions"]
    assert "audit:view" in identity.public_payload()["permissions"]


def test_role_cannot_be_changed_inside_signed_cookie() -> None:
    manager = SessionManager(settings())
    token = manager.encode(manager.authenticate("user", "user-password"))
    payload, signature = token.split(".")
    tampered = f"{payload[:-1]}A.{signature}"

    with pytest.raises(AuthenticationError):
        manager.decode(tampered)


def test_expired_session_is_rejected() -> None:
    manager = SessionManager(settings())
    identity = manager.authenticate("user", "user-password")
    object.__setattr__(identity, "expires_at", int(time.time()) - 1)

    with pytest.raises(AuthenticationError, match="expired"):
        manager.decode(manager.encode(identity))


def test_invalid_password_is_rejected() -> None:
    with pytest.raises(AuthenticationError):
        SessionManager(settings()).authenticate("admin", "wrong-password")

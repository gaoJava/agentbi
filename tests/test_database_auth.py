"""Persistence and revocation tests for database-backed identity."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from agentbi.auth import AuthenticationError, SessionManager
from agentbi.config import Settings


def settings(database_url: str) -> Settings:
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
        database_url=database_url,
    )


def test_password_is_hashed_and_rbac_is_seeded(tmp_path) -> None:
    database = tmp_path / "agentbi.db"
    manager = SessionManager(settings(f"sqlite:///{database.as_posix()}"))

    identity = manager.authenticate("user", "user-password")
    assert identity.username == "user"
    assert "dashboard:view" in identity.permissions
    assert "user:manage" not in identity.permissions

    with sqlite3.connect(database) as connection:
        password_hash = connection.execute(
            "SELECT password_hash FROM users WHERE username = 'user'"
        ).fetchone()[0]
        role_count = connection.execute("SELECT COUNT(*) FROM roles").fetchone()[0]
        permission_count = connection.execute("SELECT COUNT(*) FROM permissions").fetchone()[0]
    assert password_hash != "user-password"
    assert password_hash.startswith("pbkdf2_sha256$")
    assert role_count == 2
    assert permission_count == 10


def test_logout_revokes_server_side_session() -> None:
    manager = SessionManager(replace(settings("sqlite:///:memory:")))
    identity = manager.authenticate("admin", "admin-password")
    token = manager.encode(identity)

    manager.revoke(identity)

    with pytest.raises(AuthenticationError, match="revoked"):
        manager.decode(token)

"""Runtime configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Security and upstream connection settings."""

    api_key: str
    supersonic_base_url: str
    supersonic_token: str | None
    request_timeout_seconds: int
    max_result_rows: int
    allowed_origins: tuple[str, ...]
    requests_per_minute: int = 30
    idempotency_ttl_seconds: int = 300
    session_secret: str = ""
    session_ttl_seconds: int = 28800
    demo_login_enabled: bool = False
    demo_user_password: str = ""
    demo_admin_password: str = ""
    database_url: str = "sqlite:///:memory:"
    superset_base_url: str = "http://127.0.0.1:8088"
    superset_dashboard_path: str = "/superset/dashboard/1/"
    superset_username: str | None = None
    superset_password: str | None = None
    conversation_token_threshold: int = 6000
    conversation_message_threshold: int = 20
    conversation_keep_recent_turns: int = 4

    @classmethod
    def from_env(cls) -> Settings:
        api_key = os.getenv("AGENTBI_API_KEY", "")
        # Fail closed. A predictable development secret tends to leak into deployments.
        if len(api_key) < 32:
            raise ValueError("AGENTBI_API_KEY must contain at least 32 characters")
        origins = tuple(
            origin.strip()
            for origin in os.getenv("AGENTBI_ALLOWED_ORIGINS", "http://localhost:8088").split(",")
            if origin.strip()
        )
        session_secret = os.getenv("AGENTBI_SESSION_SECRET", api_key)
        if len(session_secret) < 32:
            raise ValueError("AGENTBI_SESSION_SECRET must contain at least 32 characters")
        demo_login_enabled = os.getenv("AGENTBI_ENABLE_DEMO_LOGIN", "false").lower() in {
            "1",
            "true",
            "yes",
        }
        demo_user_password = os.getenv("AGENTBI_DEMO_USER_PASSWORD", "")
        demo_admin_password = os.getenv("AGENTBI_DEMO_ADMIN_PASSWORD", "")
        if demo_login_enabled and (not demo_user_password or not demo_admin_password):
            raise ValueError("demo login passwords must be configured when demo login is enabled")
        return cls(
            api_key=api_key,
            supersonic_base_url=os.getenv(
                "SUPERSONIC_BASE_URL", "http://localhost:9080"
            ).rstrip("/"),
            supersonic_token=os.getenv("SUPERSONIC_TOKEN") or None,
            request_timeout_seconds=_positive_int("AGENTBI_REQUEST_TIMEOUT_SECONDS", 20),
            max_result_rows=_positive_int("AGENTBI_MAX_RESULT_ROWS", 500),
            allowed_origins=origins,
            requests_per_minute=_positive_int("AGENTBI_REQUESTS_PER_MINUTE", 30),
            idempotency_ttl_seconds=_positive_int("AGENTBI_IDEMPOTENCY_TTL_SECONDS", 300),
            session_secret=session_secret,
            session_ttl_seconds=_positive_int("AGENTBI_SESSION_TTL_SECONDS", 28800),
            demo_login_enabled=demo_login_enabled,
            demo_user_password=demo_user_password,
            demo_admin_password=demo_admin_password,
            database_url=os.getenv("AGENTBI_DATABASE_URL", "sqlite:///:memory:"),
            superset_base_url=os.getenv(
                "SUPERSET_BASE_URL", "http://127.0.0.1:8088"
            ).rstrip("/"),
            superset_dashboard_path=os.getenv(
                "SUPERSET_DASHBOARD_PATH", "/superset/dashboard/1/"
            ),
            superset_username=os.getenv("SUPERSET_USER") or None,
            superset_password=os.getenv("SUPERSET_PASSWORD") or None,
            conversation_token_threshold=_positive_int("AGENTBI_CONVERSATION_TOKEN_THRESHOLD", 6000),
            conversation_message_threshold=_positive_int("AGENTBI_CONVERSATION_MESSAGE_THRESHOLD", 20),
            conversation_keep_recent_turns=_positive_int("AGENTBI_CONVERSATION_KEEP_RECENT_TURNS", 4),
        )

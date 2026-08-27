"""Database-backed authentication with signed, server-revocable sessions."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from agentbi.config import Settings
from agentbi.database import ADMIN_PERMISSIONS, COMMON_PERMISSIONS, IdentityRepository

SESSION_COOKIE = "agentbi_session"


class AuthenticationError(ValueError):
    """Raised when credentials or a session cannot be trusted."""


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    subject: str
    username: str
    display_name: str
    role: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...]
    data_scope: str
    csrf_token: str
    expires_at: int
    session_id: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public_payload(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "roles": list(self.roles),
            "data_scope": self.data_scope,
            "csrf_token": self.csrf_token,
            "permissions": list(self.permissions),
        }


def permissions_for(role: str) -> tuple[str, ...]:
    """Compatibility helper; effective permissions are loaded from the database."""

    return ADMIN_PERMISSIONS if role == "admin" else COMMON_PERMISSIONS


class SessionManager:
    """Authenticate database users and issue tamper-evident, revocable sessions."""

    def __init__(self, settings: Settings):
        self._settings = settings
        secret = settings.session_secret or settings.api_key
        if len(secret) < 32:
            raise ValueError("session signing secret must contain at least 32 characters")
        self._secret = secret.encode("utf-8")
        self.repository = IdentityRepository(settings.database_url)
        self.repository.initialize(
            seed_demo_accounts=settings.demo_login_enabled,
            user_password=settings.demo_user_password,
            admin_password=settings.demo_admin_password,
        )

    def authenticate(self, username: str, password: str) -> SessionIdentity:
        account = self.repository.authenticate(username, password)
        if account is None:
            raise AuthenticationError("invalid username or password")
        role_names = tuple("Admin" if role == "admin" else "Analyst" for role in account.roles)
        return SessionIdentity(
            subject=account.id,
            username=account.username,
            display_name=account.display_name,
            role=account.primary_role,
            roles=role_names,
            permissions=account.permissions,
            data_scope=account.data_scope,
            csrf_token=secrets.token_urlsafe(24),
            expires_at=int(time.time()) + self._settings.session_ttl_seconds,
        )

    def encode(self, identity: SessionIdentity) -> str:
        session_id = identity.session_id
        if not session_id:
            session_id = self.repository.create_session(
                identity.subject, identity.csrf_token, identity.expires_at
            )
            object.__setattr__(identity, "session_id", session_id)
        payload = {
            "sub": identity.subject,
            "sid": session_id,
            "csrf": identity.csrf_token,
            "exp": identity.expires_at,
        }
        encoded = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
        signature = _b64url(hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest())
        return f"{encoded}.{signature}"

    def decode(self, token: str) -> SessionIdentity:
        try:
            encoded, provided_signature = token.split(".", 1)
            expected_signature = _b64url(
                hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
            )
            if not hmac.compare_digest(provided_signature, expected_signature):
                raise AuthenticationError("invalid session signature")
            payload = json.loads(_b64url_decode(encoded))
            expires_at = int(payload["exp"])
            if expires_at <= int(time.time()):
                raise AuthenticationError("session expired")
            subject = str(payload["sub"])
            session_id = str(payload["sid"])
            csrf_token = str(payload["csrf"])
            if not self.repository.session_is_active(session_id, subject, csrf_token):
                raise AuthenticationError("session revoked")
            account = self.repository.get_account(subject)
            if account is None:
                raise AuthenticationError("account disabled")
            role_names = tuple(
                "Admin" if role == "admin" else "Analyst" for role in account.roles
            )
            return SessionIdentity(
                subject=account.id,
                username=account.username,
                display_name=account.display_name,
                role=account.primary_role,
                roles=role_names,
                permissions=account.permissions,
                data_scope=account.data_scope,
                csrf_token=csrf_token,
                expires_at=expires_at,
                session_id=session_id,
            )
        except AuthenticationError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AuthenticationError("invalid session") from exc

    def revoke(self, identity: SessionIdentity) -> None:
        if identity.session_id:
            self.repository.revoke_session(identity.session_id)

    def list_users(self) -> list[dict[str, object]]:
        return self.repository.list_users()

    def create_user(self, **values: object) -> dict[str, object]:
        return self.repository.create_user(**values)

    def list_roles(self) -> list[dict[str, object]]:
        return self.repository.list_roles()

    def update_user(self, **values: object) -> dict[str, object]:
        return self.repository.update_user(**values)

    def list_audit_events(self, *, limit: int = 100) -> list[dict[str, object]]:
        return self.repository.list_audit_events(limit=limit)

    def create_report(self, **values: object) -> dict[str, object]:
        return self.repository.create_report(**values)

    def list_reports(
        self, *, actor_user_id: str, include_all: bool = False
    ) -> list[dict[str, object]]:
        return self.repository.list_reports(
            actor_user_id=actor_user_id,
            include_all=include_all,
        )

    def delete_report(
        self, report_id: str, *, actor_user_id: str, include_all: bool = False
    ) -> str:
        return self.repository.delete_report(
            report_id,
            actor_user_id=actor_user_id,
            include_all=include_all,
        )

    def list_charts(self, *, published_only: bool = True) -> list[dict[str, object]]:
        return self.repository.list_charts(published_only=published_only)

    def create_chart_with_drilldown(
        self,
        *,
        chart_key: str,
        title: str,
        metric: str,
        dataset_name: str,
        visualization_type: str,
        semantic_model: str,
        dimensions: list[str],
        actor_user_id: str,
    ) -> dict[str, object]:
        return self.repository.create_chart_with_drilldown(
            chart_key=chart_key,
            title=title,
            metric=metric,
            dataset_name=dataset_name,
            visualization_type=visualization_type,
            semantic_model=semantic_model,
            dimensions=dimensions,
            actor_user_id=actor_user_id,
        )

    def update_chart_with_drilldown(self, **values: object) -> dict[str, object]:
        return self.repository.update_chart_with_drilldown(**values)

    def set_chart_published(self, chart_key: str, *, published: bool) -> dict[str, object]:
        return self.repository.set_chart_published(chart_key, published=published)

    def delete_chart(self, chart_key: str) -> None:
        self.repository.delete_chart(chart_key)

    def audit(
        self,
        event_type: str,
        outcome: str,
        *,
        actor_user_id: str | None = None,
        source_ip: str = "",
        detail: str = "",
    ) -> None:
        self.repository.audit(
            event_type,
            outcome,
            actor_user_id=actor_user_id,
            source_ip=source_ip,
            detail=detail,
        )


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)

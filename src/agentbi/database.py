"""Database-backed identity, RBAC, session and audit storage.

SQLite is used for the zero-install local workbench.  The schema and repository
use portable SQLAlchemy constructs so the same model can run on PostgreSQL by
changing ``AGENTBI_DATABASE_URL``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Mapped, Session, declarative_base
from sqlalchemy.pool import StaticPool


def utc_now() -> datetime:
    return datetime.now(UTC)


Base = declarative_base()
mapped_column = Column


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str] = mapped_column(String(128))
    primary_role_code: Mapped[str] = mapped_column(String(64), ForeignKey("roles.code"))
    data_scope: Mapped[str] = mapped_column(String(256), default="全部区域")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Role(Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    description: Mapped[str] = mapped_column(String(512), default="")


class Permission(Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(512), default="")


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), primary_key=True)
    role_code: Mapped[str] = mapped_column(String(64), ForeignKey("roles.code"), primary_key=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_code: Mapped[str] = mapped_column(String(64), ForeignKey("roles.code"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(
        String(128), ForeignKey("permissions.code"), primary_key=True
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    source_ip: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class DashboardChart(Base):
    __tablename__ = "dashboard_charts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chart_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    metric: Mapped[str] = mapped_column(String(128))
    dataset_name: Mapped[str] = mapped_column(String(200))
    visualization_type: Mapped[str] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    is_published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DrilldownDefinition(Base):
    __tablename__ = "drilldown_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chart_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dashboard_charts.id"), unique=True, index=True
    )
    semantic_model: Mapped[str] = mapped_column(String(128))
    dimensions_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="published")
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


@dataclass(frozen=True, slots=True)
class AccountRecord:
    id: str
    username: str
    display_name: str
    primary_role: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...]
    data_scope: str


PERMISSIONS = {
    "workspace:view": "访问分析工作台",
    "dashboard:view": "查看仪表盘",
    "drilldown:use": "使用下钻分析",
    "agent:ask": "向 AgentBI 提问",
    "report:view": "查看分析报告",
    "dashboard:manage": "管理仪表盘",
    "semantic_model:manage": "管理语义模型",
    "datasource:manage": "管理数据源",
    "user:manage": "管理用户与角色",
    "audit:view": "查看安全审计",
}
COMMON_PERMISSIONS = (
    "workspace:view",
    "dashboard:view",
    "drilldown:use",
    "agent:ask",
    "report:view",
)
ADMIN_PERMISSIONS = COMMON_PERMISSIONS + (
    "dashboard:manage",
    "semantic_model:manage",
    "datasource:manage",
    "user:manage",
    "audit:view",
)


class IdentityRepository:
    """Small transaction boundary for identity and session persistence."""

    def __init__(self, database_url: str):
        url = make_url(database_url)
        if url.drivername.startswith("sqlite") and url.database not in {None, ":memory:"}:
            Path(url.database).parent.mkdir(parents=True, exist_ok=True)
        engine_args: dict[str, object] = {}
        if database_url.startswith("sqlite"):
            engine_args["connect_args"] = {"check_same_thread": False}
        if database_url == "sqlite:///:memory:":
            engine_args["poolclass"] = StaticPool
        self.engine = create_engine(database_url, **engine_args)

    def initialize(
        self,
        *,
        seed_demo_accounts: bool,
        user_password: str,
        admin_password: str,
    ) -> None:
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as db, db.begin():
            for code, name in PERMISSIONS.items():
                if db.get(Permission, code) is None:
                    db.add(Permission(code=code, name=name))
            for code, name in (("user", "数据分析师"), ("admin", "系统管理员")):
                if db.get(Role, code) is None:
                    db.add(Role(code=code, name=name))
            db.flush()
            for role, permissions in (("user", COMMON_PERMISSIONS), ("admin", ADMIN_PERMISSIONS)):
                for permission in permissions:
                    if db.get(RolePermission, (role, permission)) is None:
                        db.add(RolePermission(role_code=role, permission_code=permission))
            if seed_demo_accounts:
                self._seed_user(db, "user", user_password, "张晓", "user", "华东区域")
                self._seed_user(db, "admin", admin_password, "系统管理员", "admin", "全部区域")

    def _seed_user(
        self,
        db: Session,
        username: str,
        password: str,
        display_name: str,
        role: str,
        data_scope: str,
    ) -> None:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                id=str(uuid.uuid4()),
                username=username,
                password_hash=hash_password(password),
                display_name=display_name,
                primary_role_code=role,
                data_scope=data_scope,
            )
            db.add(user)
            db.flush()
        if db.get(UserRole, (user.id, role)) is None:
            db.add(UserRole(user_id=user.id, role_code=role))

    def authenticate(self, username: str, password: str) -> AccountRecord | None:
        normalized = username.strip().lower()
        now = utc_now()
        with Session(self.engine) as db, db.begin():
            user = db.scalar(select(User).where(User.username == normalized))
            expected = user.password_hash if user else dummy_password_hash()
            valid = verify_password(password, expected)
            if user is None or not valid or not user.is_active:
                if user is not None:
                    user.failed_login_count += 1
                    user.updated_at = now
                return None
            if user.locked_until and _as_utc(user.locked_until) > now:
                return None
            user.failed_login_count = 0
            user.last_login_at = now
            user.updated_at = now
            db.flush()
            return self._account(db, user)

    def get_account(self, user_id: str) -> AccountRecord | None:
        with Session(self.engine) as db:
            user = db.get(User, user_id)
            if user is None or not user.is_active:
                return None
            return self._account(db, user)

    def _account(self, db: Session, user: User) -> AccountRecord:
        roles = tuple(
            db.scalars(select(UserRole.role_code).where(UserRole.user_id == user.id)).all()
        )
        permissions = tuple(
            sorted(
                set(
                    db.scalars(
                        select(RolePermission.permission_code).where(
                            RolePermission.role_code.in_(roles)
                        )
                    ).all()
                )
            )
        )
        return AccountRecord(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            primary_role=user.primary_role_code,
            roles=roles,
            permissions=permissions,
            data_scope=user.data_scope,
        )

    def create_session(self, user_id: str, csrf_token: str, expires_at: int) -> str:
        session_id = str(uuid.uuid4())
        with Session(self.engine) as db, db.begin():
            db.add(
                AuthSession(
                    id=session_id,
                    user_id=user_id,
                    csrf_hash=_sha256(csrf_token),
                    expires_at=datetime.fromtimestamp(expires_at, UTC),
                )
            )
        return session_id

    def session_is_active(self, session_id: str, user_id: str, csrf_token: str) -> bool:
        with Session(self.engine) as db:
            auth_session = db.get(AuthSession, session_id)
            if auth_session is None or auth_session.user_id != user_id or auth_session.revoked_at:
                return False
            if _as_utc(auth_session.expires_at) <= utc_now():
                return False
            return hmac.compare_digest(auth_session.csrf_hash, _sha256(csrf_token))

    def revoke_session(self, session_id: str) -> None:
        with Session(self.engine) as db, db.begin():
            auth_session = db.get(AuthSession, session_id)
            if auth_session and auth_session.revoked_at is None:
                auth_session.revoked_at = utc_now()

    def list_users(self) -> list[dict[str, object]]:
        with Session(self.engine) as db:
            users = db.scalars(select(User).order_by(User.created_at)).all()
            return [
                {
                    "id": user.id,
                    "username": user.username,
                    "display_name": user.display_name,
                    "role": user.primary_role_code,
                    "data_scope": user.data_scope,
                    "is_active": user.is_active,
                    "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
                }
                for user in users
            ]

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
        normalized_key = chart_key.strip().lower()
        with Session(self.engine) as db, db.begin():
            if db.scalar(select(DashboardChart).where(DashboardChart.chart_key == normalized_key)):
                raise ValueError("chart key already exists")
            chart = DashboardChart(
                id=str(uuid.uuid4()),
                chart_key=normalized_key,
                title=title.strip(),
                metric=metric.strip(),
                dataset_name=dataset_name.strip(),
                visualization_type=visualization_type,
                created_by=actor_user_id,
            )
            db.add(chart)
            db.flush()
            drilldown = DrilldownDefinition(
                id=str(uuid.uuid4()),
                chart_id=chart.id,
                semantic_model=semantic_model.strip(),
                dimensions_json=json.dumps(dimensions, ensure_ascii=False),
                created_by=actor_user_id,
            )
            db.add(drilldown)
            db.flush()
            return self._chart_payload(chart, drilldown)

    def list_charts(self, *, published_only: bool = True) -> list[dict[str, object]]:
        with Session(self.engine) as db:
            statement = (
                select(DashboardChart, DrilldownDefinition)
                .join(DrilldownDefinition, DrilldownDefinition.chart_id == DashboardChart.id)
                .order_by(DashboardChart.created_at)
            )
            if published_only:
                statement = statement.where(DashboardChart.is_published.is_(True))
            rows = db.execute(statement).all()
            return [self._chart_payload(chart, drilldown) for chart, drilldown in rows]

    def update_chart_with_drilldown(
        self,
        *,
        chart_key: str,
        title: str,
        metric: str,
        dataset_name: str,
        visualization_type: str,
        semantic_model: str,
        dimensions: list[str],
    ) -> dict[str, object]:
        normalized_key = chart_key.strip().lower()
        with Session(self.engine) as db, db.begin():
            row = db.execute(
                select(DashboardChart, DrilldownDefinition)
                .join(DrilldownDefinition, DrilldownDefinition.chart_id == DashboardChart.id)
                .where(DashboardChart.chart_key == normalized_key)
            ).one_or_none()
            if row is None:
                raise KeyError("chart not found")
            chart, drilldown = row
            chart.title = title.strip()
            chart.metric = metric.strip()
            chart.dataset_name = dataset_name.strip()
            chart.visualization_type = visualization_type
            chart.updated_at = utc_now()
            drilldown.semantic_model = semantic_model.strip()
            drilldown.dimensions_json = json.dumps(dimensions, ensure_ascii=False)
            drilldown.updated_at = utc_now()
            db.flush()
            return self._chart_payload(chart, drilldown)

    def set_chart_published(self, chart_key: str, *, published: bool) -> dict[str, object]:
        normalized_key = chart_key.strip().lower()
        with Session(self.engine) as db, db.begin():
            row = db.execute(
                select(DashboardChart, DrilldownDefinition)
                .join(DrilldownDefinition, DrilldownDefinition.chart_id == DashboardChart.id)
                .where(DashboardChart.chart_key == normalized_key)
            ).one_or_none()
            if row is None:
                raise KeyError("chart not found")
            chart, drilldown = row
            chart.is_published = published
            chart.updated_at = utc_now()
            drilldown.status = "published" if published else "offline"
            drilldown.updated_at = utc_now()
            db.flush()
            return self._chart_payload(chart, drilldown)

    def delete_chart(self, chart_key: str) -> None:
        normalized_key = chart_key.strip().lower()
        with Session(self.engine) as db, db.begin():
            row = db.execute(
                select(DashboardChart, DrilldownDefinition)
                .join(DrilldownDefinition, DrilldownDefinition.chart_id == DashboardChart.id)
                .where(DashboardChart.chart_key == normalized_key)
            ).one_or_none()
            if row is None:
                raise KeyError("chart not found")
            chart, drilldown = row
            if chart.is_published:
                raise RuntimeError("chart must be offline before deletion")
            db.delete(drilldown)
            db.delete(chart)

    @staticmethod
    def _chart_payload(
        chart: DashboardChart, drilldown: DrilldownDefinition
    ) -> dict[str, object]:
        return {
            "id": chart.id,
            "chart_key": chart.chart_key,
            "title": chart.title,
            "metric": chart.metric,
            "dataset_name": chart.dataset_name,
            "visualization_type": chart.visualization_type,
            "semantic_model": drilldown.semantic_model,
            "dimensions": json.loads(drilldown.dimensions_json),
            "status": drilldown.status,
            "is_published": chart.is_published,
            "created_at": chart.created_at.isoformat(),
        }

    def audit(
        self,
        event_type: str,
        outcome: str,
        *,
        actor_user_id: str | None = None,
        source_ip: str = "",
        detail: str = "",
    ) -> None:
        with Session(self.engine) as db, db.begin():
            db.add(
                AuditEvent(
                    id=str(uuid.uuid4()),
                    actor_user_id=actor_user_id,
                    event_type=event_type,
                    outcome=outcome,
                    source_ip=source_ip[:64],
                    detail=detail[:512],
                )
            )


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = 260_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), _decode(raw_salt), int(raw_iterations)
        )
        return hmac.compare_digest(digest, _decode(raw_digest))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH: str | None = None


def dummy_password_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(16))
    return _DUMMY_HASH


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode()


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode())


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or UTC)

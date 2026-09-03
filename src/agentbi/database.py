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
    delete,
    func,
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )


class ConversationState(Base):
    """Persisted compression boundary and summary for an actor-owned chat."""

    __tablename__ = "conversation_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    compressed_through: Mapped[int] = mapped_column(Integer, default=0)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConversationTurn(Base):
    """Atomic user, tool-call, tool-result and assistant interaction."""

    __tablename__ = "conversation_turns"
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversation_states.id"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_question: Mapped[str] = mapped_column(Text)
    assistant_answer: Mapped[str] = mapped_column(Text, default="")
    tool_call: Mapped[str] = mapped_column(Text, default="")
    tool_result: Mapped[str] = mapped_column(Text, default="")
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


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


class SupersetDashboardAsset(Base):
    """Dashboard metadata synchronized from Superset; no credentials are stored."""

    __tablename__ = "superset_dashboard_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    superset_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str | None] = mapped_column(String(200))
    url_path: Mapped[str] = mapped_column(String(512))
    chart_count: Mapped[int] = mapped_column(Integer, default=0)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    is_home: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DrilldownDefinition(Base):
    __tablename__ = "drilldown_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chart_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dashboard_charts.id"), unique=True, index=True
    )
    semantic_model: Mapped[str] = mapped_column(String(128))
    dimensions_json: Mapped[str] = mapped_column(Text)
    superset_dashboard_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    superset_chart_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    superset_dataset_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    validation_status: Mapped[str] = mapped_column(String(32), default="pending")
    status: Mapped[str] = mapped_column(String(32), default="published")
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AnalysisReport(Base):
    """Persisted report snapshot bound to its creator and governed evidence path."""

    __tablename__ = "analysis_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    dashboard_name: Mapped[str] = mapped_column(String(200))
    data_scope: Mapped[str] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(Text)
    evidence_path: Mapped[str] = mapped_column(Text)
    content_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="draft")
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DataSourceAsset(Base):
    """Governed Superset dataset metadata; credentials remain in Superset."""

    __tablename__ = "data_source_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(64), default="Superset Dataset")
    description: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SemanticModelAsset(Base):
    """Governed SuperSonic semantic-model registration."""

    __tablename__ = "semantic_model_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    subject_area: Mapped[str] = mapped_column(String(128), default="通用主题域")
    description: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(32), default="active")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LlmProviderConfig(Base):
    """Switchable LLM provider config; API keys are stored as authenticated ciphertext."""

    __tablename__ = "llm_provider_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_url: Mapped[str] = mapped_column(String(512))
    model_name: Mapped[str] = mapped_column(String(128))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SuperSonicLlmBinding(Base):
    """AgentBI-owned binding; runtime activation remains explicit and auditable."""

    __tablename__ = "supersonic_llm_bindings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    provider_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("llm_provider_configs.id"))
    draft_provider_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("llm_provider_configs.id"))
    report_provider_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("llm_provider_configs.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(32), default="rule_first")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    fallback_to_rules: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SemanticDomainMetadata(Base):
    """AgentBI metadata missing from the current SuperSonic domain table."""

    __tablename__ = "semantic_domain_metadata"
    domain_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    description: Mapped[str] = mapped_column(String(512), default="")
    updated_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DashboardSemanticBinding(Base):
    """Default SuperSonic model selected for a governed Superset dashboard."""

    __tablename__ = "dashboard_semantic_bindings"
    dashboard_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    semantic_model_id: Mapped[int] = mapped_column(Integer, index=True)
    domain_id: Mapped[int] = mapped_column(Integer, index=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
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
        # create_all does not add columns to an existing SQLite demo database.
        # Keep this small additive migration here so older local workspaces retain
        # their registered charts and can bind them to real Superset assets.
        if self.engine.dialect.name == "sqlite":
            with self.engine.begin() as connection:
                existing = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(drilldown_definitions)"
                    ).fetchall()
                }
                additions = {
                    "superset_dashboard_id": "INTEGER",
                    "superset_chart_id": "INTEGER",
                    "superset_dataset_id": "INTEGER",
                    "validation_status": "VARCHAR(32) DEFAULT 'pending' NOT NULL",
                }
                for column, ddl in additions.items():
                    if column not in existing:
                        connection.exec_driver_sql(
                            f"ALTER TABLE drilldown_definitions ADD COLUMN {column} {ddl}"
                        )
                report_columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(analysis_reports)"
                    ).fetchall()
                }
                report_additions = {
                    "content_json": "TEXT DEFAULT '{}' NOT NULL",
                    "status": "VARCHAR(32) DEFAULT 'draft' NOT NULL",
                    "updated_at": "DATETIME",
                }
                for column, ddl in report_additions.items():
                    if column not in report_columns:
                        connection.exec_driver_sql(
                            f"ALTER TABLE analysis_reports ADD COLUMN {column} {ddl}"
                        )
                connection.exec_driver_sql(
                    "UPDATE analysis_reports SET updated_at = created_at WHERE updated_at IS NULL"
                )
                llm_binding_columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(supersonic_llm_bindings)"
                    ).fetchall()
                }
                for column in ("draft_provider_id", "report_provider_id"):
                    if column not in llm_binding_columns:
                        connection.exec_driver_sql(
                            f"ALTER TABLE supersonic_llm_bindings ADD COLUMN {column} INTEGER"
                        )
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
            db.flush()
            admin = db.scalar(select(User).where(User.username == "admin"))
            creator = admin.id if admin else None
            self._ensure_data_source(db, "sales_orders", creator, is_system=True)
            self._ensure_semantic_model(db, "sales_model", creator, is_system=True)
            for chart, drilldown in db.execute(
                select(DashboardChart, DrilldownDefinition).join(
                    DrilldownDefinition, DrilldownDefinition.chart_id == DashboardChart.id
                )
            ).all():
                self._ensure_data_source(db, chart.dataset_name, chart.created_by)
                if not drilldown.semantic_model.startswith("supersonic:"):
                    self._ensure_semantic_model(db, drilldown.semantic_model, chart.created_by)

    def create_conversation(self, conversation_id: int, actor_user_id: str) -> None:
        with Session(self.engine) as db, db.begin():
            db.add(ConversationState(id=conversation_id, actor_user_id=actor_user_id))

    @staticmethod
    def _llm_provider_dict(item: LlmProviderConfig) -> dict[str, object]:
        return {
            "id": item.id,
            "base_url": item.base_url,
            "model": item.model_name,
            "encrypted_api_key": item.encrypted_api_key,
            "enabled": item.enabled,
            "updated_at": item.updated_at.isoformat(),
        }

    def list_llm_provider_configs(self) -> list[dict[str, object]]:
        with Session(self.engine) as db:
            items = db.scalars(
                select(LlmProviderConfig).order_by(
                    LlmProviderConfig.enabled.desc(), LlmProviderConfig.updated_at.desc()
                )
            ).all()
            return [self._llm_provider_dict(item) for item in items]

    def get_llm_provider_config(self, config_id: int | None = None) -> dict[str, object] | None:
        with Session(self.engine) as db:
            item = (
                db.get(LlmProviderConfig, config_id)
                if config_id is not None
                else db.scalar(
                    select(LlmProviderConfig).order_by(
                        LlmProviderConfig.enabled.desc(), LlmProviderConfig.updated_at.desc()
                    )
                )
            )
            if item is None:
                return None
            return self._llm_provider_dict(item)

    def save_llm_provider_config(
        self,
        *,
        base_url: str,
        model: str,
        encrypted_api_key: str,
        enabled: bool,
        actor_user_id: str,
        config_id: int | None = None,
    ) -> int:
        with Session(self.engine) as db, db.begin():
            item = db.get(LlmProviderConfig, config_id) if config_id is not None else None
            if item is None:
                next_id = int(db.scalar(select(func.max(LlmProviderConfig.id))) or 0) + 1
                item = LlmProviderConfig(id=next_id)
                db.add(item)
            item.base_url = base_url
            item.model_name = model
            item.encrypted_api_key = encrypted_api_key
            item.enabled = enabled
            item.updated_by = actor_user_id
            item.updated_at = utc_now()
            db.flush()
            return item.id

    def activate_llm_provider_config(self, config_id: int) -> dict[str, object]:
        with Session(self.engine) as db, db.begin():
            item = db.get(LlmProviderConfig, config_id)
            if item is None:
                raise KeyError("LLM provider not found")
            item.enabled = True
            item.updated_at = utc_now()
            db.flush()
            return self._llm_provider_dict(item)

    def delete_llm_provider_config(self, config_id: int) -> None:
        with Session(self.engine) as db, db.begin():
            item = db.get(LlmProviderConfig, config_id)
            if item is None:
                raise KeyError("LLM provider not found")
            binding = db.get(SuperSonicLlmBinding, 1)
            bound_ids = ({binding.provider_id, binding.draft_provider_id, binding.report_provider_id}
                         if binding else set())
            if item.id in bound_ids:
                raise PermissionError("bound LLM provider cannot be deleted")
            db.delete(item)

    def get_supersonic_llm_binding(self) -> dict[str, object]:
        with Session(self.engine) as db:
            item = db.get(SuperSonicLlmBinding, 1)
            if item is None:
                return {"provider_id": None, "intent_provider_id": None,
                        "draft_provider_id": None, "report_provider_id": None,
                        "enabled": False, "mode": "rule_first",
                        "timeout_seconds": 60, "fallback_to_rules": True, "updated_at": None}
            return {"provider_id": item.provider_id, "intent_provider_id": item.provider_id,
                    "draft_provider_id": item.draft_provider_id or item.provider_id,
                    "report_provider_id": item.report_provider_id or item.provider_id,
                    "enabled": item.enabled, "mode": item.mode,
                    "timeout_seconds": item.timeout_seconds,
                    "fallback_to_rules": item.fallback_to_rules,
                    "updated_at": item.updated_at.isoformat()}

    def save_supersonic_llm_binding(
        self, *, provider_id: int | None, draft_provider_id: int | None = None,
        report_provider_id: int | None = None, enabled: bool, mode: str,
        timeout_seconds: int, fallback_to_rules: bool, actor_user_id: str,
    ) -> dict[str, object]:
        with Session(self.engine) as db, db.begin():
            for selected_id in (provider_id, draft_provider_id, report_provider_id):
                if selected_id is not None and db.get(LlmProviderConfig, selected_id) is None:
                    raise KeyError("LLM provider not found")
            item = db.get(SuperSonicLlmBinding, 1)
            if item is None:
                item = SuperSonicLlmBinding(id=1)
                db.add(item)
            item.provider_id = provider_id
            item.draft_provider_id = draft_provider_id or provider_id
            item.report_provider_id = report_provider_id or provider_id
            item.enabled = enabled
            item.mode = mode
            item.timeout_seconds = timeout_seconds
            item.fallback_to_rules = fallback_to_rules
            item.updated_by = actor_user_id
            item.updated_at = utc_now()
        return self.get_supersonic_llm_binding()

    def list_semantic_domain_descriptions(self) -> dict[int, str]:
        with Session(self.engine) as db:
            return {
                item.domain_id: item.description
                for item in db.scalars(select(SemanticDomainMetadata)).all()
            }

    def save_semantic_domain_description(
        self, domain_id: int, description: str, actor_user_id: str
    ) -> None:
        with Session(self.engine) as db, db.begin():
            item = db.get(SemanticDomainMetadata, domain_id)
            if item is None:
                item = SemanticDomainMetadata(domain_id=domain_id)
                db.add(item)
            item.description = description.strip()
            item.updated_by = actor_user_id
            item.updated_at = utc_now()

    def delete_semantic_domain_description(self, domain_id: int) -> None:
        with Session(self.engine) as db, db.begin():
            item = db.get(SemanticDomainMetadata, domain_id)
            if item is not None:
                db.delete(item)

    def conversation_context(
        self, conversation_id: int, actor_user_id: str
    ) -> tuple[str, int, list[dict[str, object]]] | None:
        with Session(self.engine) as db:
            state = db.get(ConversationState, conversation_id)
            if state is None or state.actor_user_id != actor_user_id:
                return None
            turns = db.scalars(
                select(ConversationTurn)
                .where(
                    ConversationTurn.conversation_id == conversation_id,
                    ConversationTurn.sequence > state.compressed_through,
                )
                .order_by(ConversationTurn.sequence)
            ).all()
            return (
                state.summary,
                state.compressed_through,
                [
                    {
                        "sequence": turn.sequence,
                        "question": turn.user_question,
                        "answer": turn.assistant_answer,
                        "tool_call": turn.tool_call,
                        "tool_result": turn.tool_result,
                        "tokens": turn.estimated_tokens,
                    }
                    for turn in turns
                ],
            )

    def append_conversation_turn(
        self, conversation_id: int, actor_user_id: str, **values: object
    ) -> int:
        with Session(self.engine) as db, db.begin():
            state = db.get(ConversationState, conversation_id)
            if state is None or state.actor_user_id != actor_user_id:
                raise ValueError("conversation unavailable")
            state.turn_count += 1
            state.updated_at = utc_now()
            sequence = state.turn_count
            db.add(ConversationTurn(conversation_id=conversation_id, sequence=sequence, **values))
            return sequence

    def conversation_history(self, actor_user_id: str, limit: int = 30) -> list[dict[str, object]]:
        """Return the actor's newest persisted Q&A turns without exposing tool payloads."""

        with Session(self.engine) as db:
            turns = db.scalars(
                select(ConversationTurn)
                .join(ConversationState, ConversationState.id == ConversationTurn.conversation_id)
                .where(ConversationState.actor_user_id == actor_user_id)
                .order_by(ConversationTurn.created_at.desc(), ConversationTurn.sequence.desc())
                .limit(max(1, min(limit, 100)))
            ).all()
            return [
                {
                    "chat_id": turn.conversation_id,
                    "sequence": turn.sequence,
                    "question": turn.user_question,
                    "answer": turn.assistant_answer,
                    "created_at": turn.created_at.isoformat(),
                }
                for turn in turns
            ]

    def actor_owns_query(self, actor_user_id: str, query_id: str) -> bool:
        """Verify that a report evidence id came from this actor's persisted query."""

        with Session(self.engine) as db:
            results = db.scalars(
                select(ConversationTurn.tool_result)
                .join(ConversationState, ConversationState.id == ConversationTurn.conversation_id)
                .where(ConversationState.actor_user_id == actor_user_id)
                .order_by(ConversationTurn.created_at.desc())
                .limit(200)
            ).all()
        for raw in results:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if str(payload.get("queryId") or "") == query_id:
                return True
        return False

    def compress_conversation(
        self, conversation_id: int, actor_user_id: str, through: int, summary: str
    ) -> None:
        with Session(self.engine) as db, db.begin():
            state = db.get(ConversationState, conversation_id)
            if state is None or state.actor_user_id != actor_user_id:
                raise ValueError("conversation unavailable")
            state.summary = summary
            state.compressed_through = max(state.compressed_through, through)
            state.updated_at = utc_now()

    @staticmethod
    def _ensure_data_source(
        db: Session, name: str, actor_user_id: str | None, *, is_system: bool = False
    ) -> DataSourceAsset:
        asset = db.scalar(select(DataSourceAsset).where(DataSourceAsset.name == name.strip()))
        if asset is None:
            asset = DataSourceAsset(
                id=str(uuid.uuid4()),
                name=name.strip(),
                created_by=actor_user_id,
                is_system=is_system,
            )
            db.add(asset)
            db.flush()
        return asset

    @staticmethod
    def _ensure_semantic_model(
        db: Session, name: str, actor_user_id: str | None, *, is_system: bool = False
    ) -> SemanticModelAsset:
        asset = db.scalar(select(SemanticModelAsset).where(SemanticModelAsset.name == name.strip()))
        if asset is None:
            asset = SemanticModelAsset(
                id=str(uuid.uuid4()),
                name=name.strip(),
                created_by=actor_user_id,
                is_system=is_system,
            )
            db.add(asset)
            db.flush()
        return asset

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
                    "role_name": db.get(Role, user.primary_role_code).name,
                    "data_scope": user.data_scope,
                    "is_active": user.is_active,
                    "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
                }
                for user in users
            ]

    def create_user(
        self,
        *,
        username: str,
        password: str,
        display_name: str,
        role: str,
        data_scope: str,
        is_active: bool,
    ) -> dict[str, object]:
        normalized = username.strip().lower()
        with Session(self.engine) as db, db.begin():
            if db.scalar(select(User).where(User.username == normalized)) is not None:
                raise ValueError("username already exists")
            if db.get(Role, role) is None:
                raise KeyError("role not found")
            user = User(
                id=str(uuid.uuid4()),
                username=normalized,
                password_hash=hash_password(password),
                display_name=display_name.strip(),
                primary_role_code=role,
                data_scope=data_scope.strip(),
                is_active=is_active,
            )
            db.add(user)
            db.flush()
            db.add(UserRole(user_id=user.id, role_code=role))
            return {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "role": user.primary_role_code,
                "role_name": db.get(Role, user.primary_role_code).name,
                "data_scope": user.data_scope,
                "is_active": user.is_active,
                "last_login_at": None,
            }

    def list_roles(self) -> list[dict[str, object]]:
        with Session(self.engine) as db:
            roles = db.scalars(select(Role).order_by(Role.code)).all()
            return [
                {
                    "code": role.code,
                    "name": role.name,
                    "description": role.description,
                    "permissions": list(
                        db.scalars(
                            select(RolePermission.permission_code)
                            .where(RolePermission.role_code == role.code)
                            .order_by(RolePermission.permission_code)
                        ).all()
                    ),
                    "user_count": int(
                        db.scalar(
                            select(func.count())
                            .select_from(UserRole)
                            .where(UserRole.role_code == role.code)
                        )
                        or 0
                    ),
                    "builtin": role.code in {"user", "admin"},
                }
                for role in roles
            ]

    def list_permissions(self) -> list[dict[str, str]]:
        with Session(self.engine) as db:
            permissions = db.scalars(select(Permission).order_by(Permission.code)).all()
            return [{"code": item.code, "name": item.name} for item in permissions]

    def create_role(
        self, *, code: str, name: str, description: str, permissions: list[str]
    ) -> dict[str, object]:
        normalized = code.strip().lower()
        with Session(self.engine) as db, db.begin():
            if db.get(Role, normalized) is not None:
                raise ValueError("role already exists")
            self._validate_permissions(db, permissions)
            role = Role(code=normalized, name=name.strip(), description=description.strip())
            db.add(role)
            db.flush()
            for permission in sorted(set(permissions)):
                db.add(RolePermission(role_code=normalized, permission_code=permission))
            db.flush()
            return self._role_payload(db, role)

    def update_role(
        self, code: str, *, name: str, description: str, permissions: list[str]
    ) -> dict[str, object]:
        with Session(self.engine) as db, db.begin():
            role = db.get(Role, code)
            if role is None:
                raise KeyError("role not found")
            if code in {"user", "admin"}:
                raise PermissionError("builtin role is immutable")
            self._validate_permissions(db, permissions)
            role.name = name.strip()
            role.description = description.strip()
            db.execute(delete(RolePermission).where(RolePermission.role_code == code))
            for permission in sorted(set(permissions)):
                db.add(RolePermission(role_code=code, permission_code=permission))
            db.flush()
            return self._role_payload(db, role)

    def delete_role(self, code: str) -> None:
        with Session(self.engine) as db, db.begin():
            role = db.get(Role, code)
            if role is None:
                raise KeyError("role not found")
            if code in {"user", "admin"}:
                raise PermissionError("builtin role is immutable")
            assigned = db.scalar(
                select(func.count()).select_from(UserRole).where(UserRole.role_code == code)
            )
            if assigned:
                raise PermissionError("role is assigned")
            db.execute(delete(RolePermission).where(RolePermission.role_code == code))
            db.delete(role)

    @staticmethod
    def _validate_permissions(db: Session, permissions: list[str]) -> None:
        known = set(db.scalars(select(Permission.code)).all())
        if not permissions or not set(permissions).issubset(known):
            raise ValueError("invalid permissions")

    @staticmethod
    def _role_payload(db: Session, role: Role) -> dict[str, object]:
        permissions = list(
            db.scalars(
                select(RolePermission.permission_code)
                .where(RolePermission.role_code == role.code)
                .order_by(RolePermission.permission_code)
            ).all()
        )
        count = db.scalar(
            select(func.count()).select_from(UserRole).where(UserRole.role_code == role.code)
        )
        return {
            "code": role.code,
            "name": role.name,
            "description": role.description,
            "permissions": permissions,
            "user_count": int(count or 0),
            "builtin": role.code in {"user", "admin"},
        }

    def list_data_sources(self) -> list[dict[str, object]]:
        with Session(self.engine) as db:
            assets = db.scalars(select(DataSourceAsset).order_by(DataSourceAsset.created_at)).all()
            return [self._data_source_payload(db, asset) for asset in assets]

    def create_data_source(
        self, *, name: str, source_type: str, description: str, actor_user_id: str
    ) -> dict[str, object]:
        with Session(self.engine) as db, db.begin():
            if db.scalar(select(DataSourceAsset).where(DataSourceAsset.name == name.strip())):
                raise ValueError("data source already exists")
            asset = DataSourceAsset(
                id=str(uuid.uuid4()),
                name=name.strip(),
                source_type=source_type.strip(),
                description=description.strip(),
                created_by=actor_user_id,
            )
            db.add(asset)
            db.flush()
            return self._data_source_payload(db, asset)

    def update_data_source(
        self, name: str, *, source_type: str, description: str, status: str
    ) -> dict[str, object]:
        with Session(self.engine) as db, db.begin():
            asset = db.scalar(select(DataSourceAsset).where(DataSourceAsset.name == name))
            if asset is None:
                raise KeyError("data source not found")
            asset.source_type = source_type.strip()
            asset.description = description.strip()
            asset.status = status
            asset.updated_at = utc_now()
            db.flush()
            return self._data_source_payload(db, asset)

    def delete_data_source(self, name: str) -> None:
        with Session(self.engine) as db, db.begin():
            asset = db.scalar(select(DataSourceAsset).where(DataSourceAsset.name == name))
            if asset is None:
                raise KeyError("data source not found")
            if asset.is_system or self._data_source_references(db, name):
                raise PermissionError("data source is referenced")
            db.delete(asset)

    def list_semantic_models(self) -> list[dict[str, object]]:
        with Session(self.engine) as db:
            assets = db.scalars(
                select(SemanticModelAsset).order_by(SemanticModelAsset.created_at)
            ).all()
            return [self._semantic_model_payload(db, asset) for asset in assets]

    def create_semantic_model(
        self, *, name: str, subject_area: str, description: str, actor_user_id: str
    ) -> dict[str, object]:
        with Session(self.engine) as db, db.begin():
            if db.scalar(select(SemanticModelAsset).where(SemanticModelAsset.name == name.strip())):
                raise ValueError("semantic model already exists")
            asset = SemanticModelAsset(
                id=str(uuid.uuid4()),
                name=name.strip(),
                subject_area=subject_area.strip(),
                description=description.strip(),
                created_by=actor_user_id,
            )
            db.add(asset)
            db.flush()
            return self._semantic_model_payload(db, asset)

    def update_semantic_model(
        self, name: str, *, subject_area: str, description: str, status: str
    ) -> dict[str, object]:
        with Session(self.engine) as db, db.begin():
            asset = db.scalar(select(SemanticModelAsset).where(SemanticModelAsset.name == name))
            if asset is None:
                raise KeyError("semantic model not found")
            asset.subject_area = subject_area.strip()
            asset.description = description.strip()
            asset.status = status
            asset.updated_at = utc_now()
            db.flush()
            return self._semantic_model_payload(db, asset)

    def delete_semantic_model(self, name: str) -> None:
        with Session(self.engine) as db, db.begin():
            asset = db.scalar(select(SemanticModelAsset).where(SemanticModelAsset.name == name))
            if asset is None:
                raise KeyError("semantic model not found")
            if asset.is_system or self._semantic_model_references(db, name):
                raise PermissionError("semantic model is referenced")
            db.delete(asset)

    @staticmethod
    def _data_source_references(db: Session, name: str) -> int:
        return int(
            db.scalar(
                select(func.count())
                .select_from(DashboardChart)
                .where(DashboardChart.dataset_name == name)
            )
            or 0
        )

    @classmethod
    def _data_source_payload(cls, db: Session, asset: DataSourceAsset) -> dict[str, object]:
        references = cls._data_source_references(db, asset.name) + (2 if asset.is_system else 0)
        return {
            "name": asset.name,
            "type": asset.source_type,
            "description": asset.description,
            "status": asset.status,
            "charts": references,
            "is_system": asset.is_system,
        }

    @staticmethod
    def _semantic_model_references(db: Session, name: str) -> int:
        return int(
            db.scalar(
                select(func.count())
                .select_from(DrilldownDefinition)
                .where(DrilldownDefinition.semantic_model == name)
            )
            or 0
        )

    @classmethod
    def _semantic_model_payload(cls, db: Session, asset: SemanticModelAsset) -> dict[str, object]:
        rows = db.execute(
            select(DashboardChart.metric)
            .join(DrilldownDefinition, DrilldownDefinition.chart_id == DashboardChart.id)
            .where(DrilldownDefinition.semantic_model == asset.name)
        ).all()
        metrics = sorted({str(row[0]) for row in rows})
        if asset.is_system:
            metrics = sorted(set(metrics) | {"销售收入"})
        references = cls._semantic_model_references(db, asset.name) + (2 if asset.is_system else 0)
        return {
            "name": asset.name,
            "subject_area": asset.subject_area,
            "description": asset.description,
            "status": asset.status,
            "metrics": metrics,
            "charts": references,
            "is_system": asset.is_system,
        }

    def list_audit_events(self, *, limit: int = 100) -> list[dict[str, object]]:
        with Session(self.engine) as db:
            rows = db.execute(
                select(AuditEvent, User)
                .outerjoin(User, AuditEvent.actor_user_id == User.id)
                .order_by(AuditEvent.created_at.desc())
                .limit(min(max(limit, 1), 200))
            ).all()
            return [
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "outcome": event.outcome,
                    "actor": user.display_name if user else "系统",
                    "source_ip": event.source_ip,
                    "detail": event.detail,
                    "created_at": event.created_at.isoformat(),
                }
                for event, user in rows
            ]

    def update_user(
        self,
        *,
        username: str,
        role: str,
        data_scope: str,
        is_active: bool,
    ) -> dict[str, object]:
        with Session(self.engine) as db, db.begin():
            user = db.scalar(select(User).where(User.username == username.strip().lower()))
            if user is None:
                raise KeyError("user not found")
            if db.get(Role, role) is None:
                raise ValueError("role not found")
            user.primary_role_code = role
            user.data_scope = data_scope.strip()
            user.is_active = is_active
            user.updated_at = utc_now()
            db.execute(delete(UserRole).where(UserRole.user_id == user.id))
            db.add(UserRole(user_id=user.id, role_code=role))
            db.flush()
            return {
                "id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "role": user.primary_role_code,
                "role_name": db.get(Role, user.primary_role_code).name,
                "data_scope": user.data_scope,
                "is_active": user.is_active,
                "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
            }

    def create_report(
        self,
        *,
        title: str,
        dashboard_name: str,
        data_scope: str,
        summary: str,
        evidence_path: str,
        content: dict[str, object] | None = None,
        status: str = "draft",
        actor_user_id: str,
    ) -> dict[str, object]:
        with Session(self.engine) as db, db.begin():
            report = AnalysisReport(
                id=str(uuid.uuid4()),
                title=title.strip(),
                dashboard_name=dashboard_name.strip(),
                data_scope=data_scope.strip(),
                summary=summary.strip(),
                evidence_path=evidence_path.strip(),
                content_json=json.dumps(content or {}, ensure_ascii=False),
                status=status,
                created_by=actor_user_id,
            )
            db.add(report)
            db.flush()
            return self._report_payload(report)

    def list_reports(
        self, *, actor_user_id: str, include_all: bool = False
    ) -> list[dict[str, object]]:
        with Session(self.engine) as db:
            statement = select(AnalysisReport).order_by(AnalysisReport.created_at.desc())
            if not include_all:
                statement = statement.where(AnalysisReport.created_by == actor_user_id)
            reports = db.scalars(statement).all()
            return [self._report_payload(report) for report in reports]

    def update_report(
        self,
        report_id: str,
        *,
        title: str,
        summary: str,
        content: dict[str, object],
        status: str,
        actor_user_id: str,
        include_all: bool = False,
    ) -> dict[str, object]:
        with Session(self.engine) as db, db.begin():
            report = db.get(AnalysisReport, report_id)
            if report is None:
                raise KeyError("report not found")
            if not include_all and report.created_by != actor_user_id:
                raise PermissionError("report does not belong to actor")
            report.title = title.strip()
            report.summary = summary.strip()
            report.content_json = json.dumps(content, ensure_ascii=False)
            report.status = status
            report.updated_at = utc_now()
            db.flush()
            return self._report_payload(report)

    def delete_report(
        self,
        report_id: str,
        *,
        actor_user_id: str,
        include_all: bool = False,
    ) -> str:
        with Session(self.engine) as db, db.begin():
            report = db.get(AnalysisReport, report_id)
            if report is None:
                raise KeyError("report not found")
            if not include_all and report.created_by != actor_user_id:
                raise PermissionError("report does not belong to actor")
            title = report.title
            db.delete(report)
            return title

    @staticmethod
    def _report_payload(report: AnalysisReport) -> dict[str, object]:
        return {
            "id": report.id,
            "title": report.title,
            "dashboard_name": report.dashboard_name,
            "data_scope": report.data_scope,
            "summary": report.summary,
            "evidence_path": report.evidence_path,
            "content": json.loads(report.content_json or "{}"),
            "status": report.status,
            "created_at": report.created_at.isoformat(),
            "updated_at": report.updated_at.isoformat(),
        }

    def sync_superset_dashboards(
        self, dashboards: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        """Atomically upsert a bounded Superset dashboard inventory."""

        with Session(self.engine) as db, db.begin():
            existing = {
                item.superset_id: item for item in db.scalars(select(SupersetDashboardAsset)).all()
            }
            seen: set[int] = set()
            for payload in dashboards:
                superset_id = int(payload["superset_id"])
                seen.add(superset_id)
                asset = existing.get(superset_id)
                if asset is None:
                    asset = SupersetDashboardAsset(id=str(uuid.uuid4()), superset_id=superset_id)
                    db.add(asset)
                asset.title = str(payload["title"])[:200]
                asset.slug = str(payload["slug"])[:200] if payload.get("slug") else None
                asset.url_path = str(payload["url_path"])[:512]
                asset.chart_count = max(0, int(payload.get("chart_count", 0)))
                asset.published = bool(payload.get("published", False))
                asset.available = True
                asset.synced_at = utc_now()
            for superset_id, asset in existing.items():
                if superset_id not in seen:
                    asset.available = False
                    asset.is_home = False
            db.flush()
            home = db.scalar(
                select(SupersetDashboardAsset).where(SupersetDashboardAsset.is_home.is_(True))
            )
            if home is None:
                candidates = db.scalars(
                    select(SupersetDashboardAsset)
                    .where(
                        SupersetDashboardAsset.available.is_(True),
                        SupersetDashboardAsset.published.is_(True),
                    )
                    .order_by(SupersetDashboardAsset.title)
                ).all()
                preferred = next(
                    (item for item in candidates if item.title.lower() == "sales dashboard"),
                    candidates[0] if candidates else None,
                )
                if preferred is not None:
                    preferred.is_home = True
            db.flush()
            return [self._superset_dashboard_payload(item) for item in self._dashboard_assets(db)]

    def list_superset_dashboards(self) -> list[dict[str, object]]:
        with Session(self.engine) as db:
            return [self._superset_dashboard_payload(item) for item in self._dashboard_assets(db)]

    def set_home_superset_dashboard(self, superset_id: int) -> dict[str, object]:
        with Session(self.engine) as db, db.begin():
            target = db.scalar(
                select(SupersetDashboardAsset).where(
                    SupersetDashboardAsset.superset_id == superset_id,
                    SupersetDashboardAsset.available.is_(True),
                    SupersetDashboardAsset.published.is_(True),
                )
            )
            if target is None:
                raise KeyError("dashboard not found")
            for item in db.scalars(select(SupersetDashboardAsset)).all():
                item.is_home = item.id == target.id
            db.flush()
            return self._superset_dashboard_payload(target)

    def get_home_superset_dashboard(self) -> dict[str, object] | None:
        with Session(self.engine) as db:
            asset = db.scalar(
                select(SupersetDashboardAsset).where(
                    SupersetDashboardAsset.is_home.is_(True),
                    SupersetDashboardAsset.available.is_(True),
                    SupersetDashboardAsset.published.is_(True),
                )
            )
            return self._superset_dashboard_payload(asset) if asset else None

    def save_dashboard_semantic_binding(
        self, dashboard_id: int, semantic_model_id: int, domain_id: int, actor_user_id: str
    ) -> dict[str, object]:
        with Session(self.engine) as db, db.begin():
            item = db.get(DashboardSemanticBinding, dashboard_id)
            if item is None:
                item = DashboardSemanticBinding(dashboard_id=dashboard_id)
                db.add(item)
            item.semantic_model_id = semantic_model_id
            item.domain_id = domain_id
            item.updated_by = actor_user_id
            item.updated_at = utc_now()
            db.flush()
            return self._dashboard_semantic_binding_payload(db, item)

    def get_dashboard_semantic_binding(self, dashboard_id: int) -> dict[str, object] | None:
        with Session(self.engine) as db:
            item = db.get(DashboardSemanticBinding, dashboard_id)
            return self._dashboard_semantic_binding_payload(db, item) if item else None

    def list_dashboard_semantic_bindings(self) -> list[dict[str, object]]:
        with Session(self.engine) as db:
            return [
                self._dashboard_semantic_binding_payload(db, item)
                for item in db.scalars(select(DashboardSemanticBinding)).all()
            ]

    @staticmethod
    def _dashboard_semantic_binding_payload(
        db: Session, item: DashboardSemanticBinding
    ) -> dict[str, object]:
        dashboard = db.scalar(
            select(SupersetDashboardAsset).where(
                SupersetDashboardAsset.superset_id == item.dashboard_id
            )
        )
        return {
            "dashboard_id": item.dashboard_id,
            "dashboard_title": dashboard.title if dashboard else f"仪表盘 {item.dashboard_id}",
            "semantic_model_id": item.semantic_model_id,
            "domain_id": item.domain_id,
            "updated_at": item.updated_at.isoformat(),
        }

    @staticmethod
    def _dashboard_assets(db: Session) -> list[SupersetDashboardAsset]:
        return list(
            db.scalars(
                select(SupersetDashboardAsset)
                .where(SupersetDashboardAsset.available.is_(True))
                .order_by(
                    SupersetDashboardAsset.is_home.desc(), SupersetDashboardAsset.title
                )
            ).all()
        )

    @staticmethod
    def _superset_dashboard_payload(asset: SupersetDashboardAsset) -> dict[str, object]:
        return {
            "superset_id": asset.superset_id,
            "title": asset.title,
            "slug": asset.slug,
            "url_path": asset.url_path,
            "chart_count": asset.chart_count,
            "published": asset.published,
            "available": asset.available,
            "is_home": asset.is_home,
            "synced_at": asset.synced_at.isoformat(),
        }

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
        superset_dashboard_id: int | None = None,
        superset_chart_id: int | None = None,
        superset_dataset_id: int | None = None,
        validation_status: str = "pending",
        actor_user_id: str,
    ) -> dict[str, object]:
        normalized_key = chart_key.strip().lower()
        with Session(self.engine) as db, db.begin():
            if db.scalar(select(DashboardChart).where(DashboardChart.chart_key == normalized_key)):
                raise ValueError("chart key already exists")
            self._ensure_data_source(db, dataset_name, actor_user_id)
            # A SuperSonic mapping is an external governed model reference, not a
            # local semantic asset. Keeping only the stable Model ID prevents an
            # AgentBI placeholder from being mistaken for a real model definition.
            if not semantic_model.startswith("supersonic:"):
                self._ensure_semantic_model(db, semantic_model, actor_user_id)
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
                superset_dashboard_id=superset_dashboard_id,
                superset_chart_id=superset_chart_id,
                superset_dataset_id=superset_dataset_id,
                validation_status=validation_status,
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
        superset_dashboard_id: int | None = None,
        superset_chart_id: int | None = None,
        superset_dataset_id: int | None = None,
        validation_status: str = "pending",
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
            self._ensure_data_source(db, dataset_name, chart.created_by)
            if not semantic_model.startswith("supersonic:"):
                self._ensure_semantic_model(db, semantic_model, chart.created_by)
            chart.title = title.strip()
            chart.metric = metric.strip()
            chart.dataset_name = dataset_name.strip()
            chart.visualization_type = visualization_type
            chart.updated_at = utc_now()
            drilldown.semantic_model = semantic_model.strip()
            drilldown.dimensions_json = json.dumps(dimensions, ensure_ascii=False)
            drilldown.superset_dashboard_id = superset_dashboard_id
            drilldown.superset_chart_id = superset_chart_id
            drilldown.superset_dataset_id = superset_dataset_id
            drilldown.validation_status = validation_status
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
    def _chart_payload(chart: DashboardChart, drilldown: DrilldownDefinition) -> dict[str, object]:
        return {
            "id": chart.id,
            "chart_key": chart.chart_key,
            "title": chart.title,
            "metric": chart.metric,
            "dataset_name": chart.dataset_name,
            "visualization_type": chart.visualization_type,
            "semantic_model": drilldown.semantic_model,
            "dimensions": json.loads(drilldown.dimensions_json),
            "superset_dashboard_id": drilldown.superset_dashboard_id,
            "superset_chart_id": drilldown.superset_chart_id,
            "superset_dataset_id": drilldown.superset_dataset_id,
            "validation_status": drilldown.validation_status,
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

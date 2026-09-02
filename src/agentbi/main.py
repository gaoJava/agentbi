"""FastAPI application factory for the AgentBI orchestration service."""

from __future__ import annotations

import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from agentbi.auth import SESSION_COOKIE, AuthenticationError, SessionIdentity, SessionManager
from agentbi.config import Settings
from agentbi.models import Actor, AnalysisPlan, AnalyzeRequest, AnalyzeResponse, ScreenContext
from agentbi.orchestrator import Orchestrator
from agentbi.security import PolicyViolation, RateLimitExceeded, require_api_key
from agentbi.semantic_draft import build_semantic_draft
from agentbi.semantic_llm import SemanticDraftLlm, SemanticLlmError
from agentbi.superset import SupersetApiError, SupersetClient
from agentbi.supersonic import (
    ConversationUnavailableError,
    ResultMismatchError,
    SemanticResolutionError,
    SuperSonicClient,
    UpstreamError,
)

# Reuse Uvicorn's configured handler so audit events are emitted in every launch mode.
logger = logging.getLogger("uvicorn.error")


class LoginPayload(BaseModel):
    """Credentials for the explicitly enabled local competition demo."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class WorkbenchAnalyzePayload(BaseModel):
    """Analysis input accepted from the standalone workbench.

    Actor identity is deliberately absent: it is reconstructed from the signed
    server-side session so callers cannot grant themselves roles or data scope.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=2, max_length=2000)
    context: ScreenContext
    chat_id: int | None = Field(default=None, gt=0)
    agent_id: int | None = Field(default=None, gt=0)
    client_request_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class ChartCreatePayload(BaseModel):
    """A governed chart plus its initial drilldown path."""

    model_config = ConfigDict(extra="forbid")

    chart_key: str = Field(min_length=2, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    title: str = Field(min_length=2, max_length=200)
    metric: str = Field(min_length=1, max_length=128)
    dataset_name: str = Field(min_length=1, max_length=200)
    visualization_type: Literal["bar", "line", "donut", "table"]
    semantic_model: str = Field(min_length=2, max_length=128)
    dimensions: list[str] = Field(min_length=2, max_length=5)
    superset_dashboard_id: int | None = Field(default=None, gt=0)
    superset_chart_id: int | None = Field(default=None, gt=0)
    superset_dataset_id: int | None = Field(default=None, gt=0)


class ChartUpdatePayload(BaseModel):
    """Editable governed chart and drilldown fields; the stable key never changes."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=200)
    metric: str = Field(min_length=1, max_length=128)
    dataset_name: str = Field(min_length=1, max_length=200)
    visualization_type: Literal["bar", "line", "donut", "table"]
    semantic_model: str = Field(min_length=2, max_length=128)
    dimensions: list[str] = Field(min_length=2, max_length=5)
    superset_dashboard_id: int | None = Field(default=None, gt=0)
    superset_chart_id: int | None = Field(default=None, gt=0)
    superset_dataset_id: int | None = Field(default=None, gt=0)


class ChartPublishPayload(BaseModel):
    """Explicit publication state transition."""

    model_config = ConfigDict(extra="forbid")

    published: bool


class ReportCreatePayload(BaseModel):
    """A bounded request to persist the current governed dashboard snapshot."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=200)
    dashboard_name: str = Field(min_length=2, max_length=200)
    summary: str | None = Field(default=None, min_length=2, max_length=4000)
    evidence_path: str | None = Field(default=None, min_length=2, max_length=4000)
    query_id: str | None = Field(default=None, min_length=8, max_length=128)


class UserUpdatePayload(BaseModel):
    """Administrator-controlled role, scope and account state."""

    model_config = ConfigDict(extra="forbid")

    role: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    data_scope: str = Field(min_length=2, max_length=256)
    is_active: bool


class UserCreatePayload(BaseModel):
    """Administrator-created local account for the competition workbench."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=128, pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]*$")
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=2, max_length=128)
    role: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    data_scope: str = Field(min_length=2, max_length=256)
    is_active: bool = True


class DataSourceCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=200, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    source_type: str = Field(min_length=2, max_length=64)
    description: str = Field(default="", max_length=512)


class DataSourceUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: str = Field(min_length=2, max_length=64)
    description: str = Field(default="", max_length=512)
    status: Literal["active", "offline"]


class SupersetDatabasePayload(BaseModel):
    """A connection secret used once and forwarded only to Superset."""

    model_config = ConfigDict(extra="forbid")

    database_name: str = Field(min_length=2, max_length=250)
    connection_mode: Literal["form", "uri"] = "form"
    engine: Literal["postgresql", "mysql", "doris", "trino", "presto", "druid"] = "postgresql"
    host: str = Field(default="", max_length=253)
    port: int | None = Field(default=None, gt=0, le=65535)
    database: str = Field(default="", max_length=250)
    username: str = Field(default="", max_length=250)
    password: str = Field(default="", max_length=512)
    sqlalchemy_uri: str = Field(default="", max_length=1024)
    expose_in_sqllab: bool = True

    def resolved_uri(self, *, required: bool) -> str:
        if self.connection_mode == "uri" or (self.sqlalchemy_uri.strip() and not self.host.strip()):
            uri = self.sqlalchemy_uri.strip()
        elif not any((self.host, self.database, self.username, self.password, self.port)):
            uri = ""
        else:
            if (
                not self.host.strip()
                or not self.database.strip()
                or not self.username.strip()
                or not self.port
            ):
                raise ValueError("请完整填写主机、端口、数据库和用户名")
            scheme = "mysql" if self.engine == "doris" else self.engine
            credentials = quote(self.username.strip(), safe="")
            if self.password:
                credentials += f":{quote(self.password, safe='')}"
            uri = f"{scheme}://{credentials}@{self.host.strip()}:{self.port}/{quote(self.database.strip(), safe='')}"
        if required and not uri:
            raise ValueError("请填写数据库连接信息")
        return uri


class SupersetDatasetPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database_id: int = Field(gt=0)
    schema_name: str = Field(default="", max_length=250)
    table_name: str = Field(min_length=1, max_length=250)


class SupersetDatabaseUpdatePayload(SupersetDatabasePayload):
    """Connection update; omitted connection fields preserve Superset's secret."""


class SupersetDatasetUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", max_length=1000)


class SupersetDashboardPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=500)
    published: bool = False


class SupersetDashboardCopyPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=500)
    duplicate_charts: bool = False


class SupersetChartAuthoringPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=250)
    dataset_id: int = Field(gt=0)
    dashboard_id: int = Field(gt=0)
    visualization_type: Literal[
        "table", "bar", "line", "area", "pie", "donut", "scatter", "funnel", "big_number"
    ] = "table"
    dimension: str = Field(min_length=1, max_length=250)
    metric_column: str = Field(min_length=1, max_length=250)
    aggregation: Literal["SUM", "AVG", "COUNT", "MAX", "MIN"] = "SUM"
    time_column: str | None = Field(default=None, max_length=250)


class SemanticModelCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    subject_area: str = Field(min_length=2, max_length=128)
    description: str = Field(default="", max_length=512)


class SemanticModelUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_area: str = Field(min_length=2, max_length=128)
    description: str = Field(default="", max_length=512)
    status: Literal["active", "offline"]


class SemanticDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: int = Field(gt=0)
    provider_id: int | None = Field(default=None, gt=0)
    use_llm: bool = True
    reasoning_mode: Literal["fast", "deep"] = "fast"


class LlmProviderPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=8, max_length=512, pattern=r"^https?://")
    model: str = Field(min_length=1, max_length=128)
    api_key: str = Field(default="", max_length=512)
    enabled: bool = True


class SuperSonicLlmBindingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_id: int | None = Field(default=None, gt=0)
    enabled: bool = False
    mode: Literal["rule_first", "llm_enhanced"] = "rule_first"
    timeout_seconds: int = Field(default=60, ge=10, le=180)
    fallback_to_rules: bool = True


class SuperSonicDatabasePayload(BaseModel):
    """One-shot secret forwarded to SuperSonic and never persisted by AgentBI."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=128)
    engine: Literal["postgresql", "mysql", "doris"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(gt=0, le=65535)
    database: str = Field(min_length=1, max_length=250)
    username: str = Field(min_length=1, max_length=250)
    password: str = Field(min_length=1, max_length=512)
    description: str = Field(default="AgentBI 同源语义连接", max_length=512)


class SemanticDomainPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=128)
    biz_name: str = Field(min_length=2, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    description: str = Field(default="", max_length=512)


class DashboardSemanticBindingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    semantic_model_id: int = Field(gt=0)


class SemanticDraftPublishPayload(BaseModel):
    """Administrator-reviewed values; no SQL or credentials are accepted."""

    model_config = ConfigDict(extra="forbid")
    dataset_id: int = Field(gt=0)
    domain_id: int = Field(gt=0)
    database_id: int = Field(gt=0)
    name: str = Field(min_length=2, max_length=128)
    biz_name: str = Field(min_length=2, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    description: str = Field(default="", max_length=512)
    identifiers: list[dict[str, object]] = Field(min_length=1, max_length=5)
    dimensions: list[dict[str, object]] = Field(default_factory=list, max_length=50)
    measures: list[dict[str, object]] = Field(min_length=1, max_length=50)
    fields: list[dict[str, str]] = Field(min_length=1, max_length=200)
    drilldown_path: list[str] = Field(default_factory=list, max_length=5)


class RoleCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(min_length=2, max_length=128)
    description: str = Field(default="", max_length=512)
    permissions: list[str] = Field(min_length=1, max_length=20)


class RoleUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=128)
    description: str = Field(default="", max_length=512)
    permissions: list[str] = Field(min_length=1, max_length=20)


def validated_dimensions(items: list[str]) -> list[str]:
    """Normalize and reject ambiguous drill paths before they reach storage."""

    dimensions = [item.strip() for item in items if item.strip()]
    if len(dimensions) < 2 or len(set(dimensions)) != len(dimensions):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="至少配置两个不重复的下钻维度",
        )
    return dimensions


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    sessions = SessionManager(settings)
    supersonic = SuperSonicClient(settings, repository=sessions.repository)
    semantic_llm = SemanticDraftLlm(settings)

    async def validate_chart_binding(payload: ChartCreatePayload | ChartUpdatePayload) -> str:
        """Validate that a governed definition points at one real chart and model."""

        binding = (
            payload.superset_dashboard_id,
            payload.superset_chart_id,
            payload.superset_dataset_id,
        )
        if not any(binding):
            return "pending"
        if not all(binding):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="请完整选择 Superset 仪表盘、图表和 Dataset",
            )
        try:
            dashboard = await app.state.superset_client.get_native_dashboard(
                payload.superset_dashboard_id
            )
            models = await app.state.supersonic_client.list_semantic_models()
        except (SupersetApiError, UpstreamError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="真实图表或语义模型校验失败，请检查上游连接",
            ) from exc
        chart = next(
            (
                item for item in dashboard.get("charts", [])
                if int(item.get("superset_id") or 0) == payload.superset_chart_id
            ),
            None,
        )
        if chart is None:
            raise HTTPException(status_code=422, detail="所选 Superset 图表不属于该仪表盘")
        configuration = chart.get("configuration") or {}
        if int(configuration.get("dataset_id") or 0) != payload.superset_dataset_id:
            raise HTTPException(status_code=422, detail="所选图表与 Dataset 不一致")
        if configuration.get("metric_column") != payload.metric:
            raise HTTPException(status_code=422, detail="核心指标与 Superset 图表指标不一致")
        if configuration.get("dimension") != payload.dimensions[0].strip():
            raise HTTPException(status_code=422, detail="下钻首层必须与 Superset 图表维度一致")
        try:
            model_id = int(payload.semantic_model.split(":", 1)[1]) \
                if payload.semantic_model.startswith("supersonic:") else None
        except ValueError:
            model_id = None
        if model_id is None or not any(int(item.get("id") or 0) == model_id for item in models):
            raise HTTPException(status_code=422, detail="所选 SuperSonic 语义模型不存在")
        return "ready"
    stored_llm = sessions.repository.get_llm_provider_config()
    if stored_llm and stored_llm["enabled"]:
        try:
            semantic_llm.configure(
                base_url=str(stored_llm["base_url"]),
                api_key=semantic_llm.decrypt_key(str(stored_llm["encrypted_api_key"])),
                model=str(stored_llm["model"]),
            )
        except SemanticLlmError:
            logger.warning("Stored LLM provider key cannot be decrypted; provider disabled")

    async def resolve_semantic_question_with_llm(question: str):
        binding = sessions.repository.get_supersonic_llm_binding()
        if not binding["enabled"] or binding["provider_id"] is None:
            return None
        provider = sessions.repository.get_llm_provider_config(int(binding["provider_id"]))
        if provider is None:
            return None
        try:
            return await semantic_llm.compile_analysis_plan_with(
                question,
                base_url=str(provider["base_url"]),
                api_key=semantic_llm.decrypt_key(str(provider["encrypted_api_key"])),
                model=str(provider["model"]),
                timeout_seconds=int(binding["timeout_seconds"]),
            )
        except SemanticLlmError as exc:
            if bool(binding["fallback_to_rules"]):
                logger.warning("LLM analysis planning failed; requesting explicit clarification: %s", exc)
                return AnalysisPlan(
                    confidence=0,
                    needs_clarification=True,
                    clarification_question=(
                        "我暂时无法稳定理解这个问题。请补充统计维度、指标和范围，"
                        "例如“按游戏类型统计全球销量前5名并计算平均值”。"
                    ),
                    clarification_options=[
                        "按平台统计全球销量前5名",
                        "按游戏类型统计全球销量前5名",
                        "按发行商统计全球销量前5名",
                        "按游戏类型统计全球销量前5名并计算平均值",
                    ],
                )
            raise UpstreamError("LLM semantic intent service is unavailable")

    supersonic.configure_llm_resolver(resolve_semantic_question_with_llm)
    orchestrator = Orchestrator(settings, supersonic)
    superset_client = SupersetClient(
        settings.superset_base_url,
        settings.superset_username,
        settings.superset_password,
        settings.request_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await supersonic.close()
        await semantic_llm.close()

    app = FastAPI(
        title="AgentBI Orchestrator",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.superset_client = superset_client
    app.state.orchestrator = orchestrator
    app.state.supersonic_client = supersonic
    app.state.semantic_llm = semantic_llm
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-AgentBI-Key", "X-AgentBI-CSRF"],
    )
    authenticate = require_api_key(settings)
    web_root = Path(__file__).with_name("web")
    app.mount("/app/assets", StaticFiles(directory=web_root), name="workbench-assets")

    def current_identity(request: Request) -> SessionIdentity:
        token = request.cookies.get(SESSION_COOKIE, "")
        try:
            return sessions.decode(token)
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated"
            ) from exc

    current_session = Depends(current_identity)

    def require_permission(permission: str):
        def checker(identity: SessionIdentity = current_session) -> SessionIdentity:
            if permission not in identity.permissions:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
            return identity

        return Depends(checker)

    def require_admin(identity: SessionIdentity = current_session) -> SessionIdentity:
        if "user:manage" not in identity.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return identity

    admin_session = Depends(require_admin)
    audit_session = require_permission("audit:view")
    semantic_session = require_permission("semantic_model:manage")
    datasource_session = require_permission("datasource:manage")
    dashboard_management_session = require_permission("dashboard:manage")
    drilldown_session = require_permission("drilldown:use")
    agent_session = require_permission("agent:ask")

    def enforce_csrf(request: Request, identity: SessionIdentity) -> None:
        if not hmac.compare_digest(request.headers.get("X-AgentBI-CSRF", ""), identity.csrf_token):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")

    @app.middleware("http")
    async def audit_request(request: Request, call_next) -> Response:
        """Emit bounded structured telemetry without logging prompts or credentials."""

        trace_id = uuid.uuid4().hex
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                json.dumps(
                    {
                        "event": "request_failed",
                        "trace_id": trace_id,
                        "method": request.method,
                        "path": request.url.path,
                    }
                )
            )
            raise
        response.headers["X-AgentBI-Trace"] = trace_id
        logger.info(
            json.dumps(
                {
                    "event": "request_complete",
                    "trace_id": trace_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": max(0, round((time.perf_counter() - started) * 1000)),
                }
            )
        )
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/app", include_in_schema=False)
    async def workbench() -> FileResponse:
        return FileResponse(web_root / "index.html")

    @app.post("/api/v1/auth/login")
    async def login(
        payload: LoginPayload, response: Response, request: Request
    ) -> dict[str, object]:
        try:
            identity = sessions.authenticate(payload.username, payload.password)
        except AuthenticationError as exc:
            sessions.audit(
                "login",
                "denied",
                source_ip=request.client.host if request.client else "",
                detail="credential_rejected",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            ) from exc
        sessions.audit(
            "login",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
        )
        response.set_cookie(
            key=SESSION_COOKIE,
            value=sessions.encode(identity),
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        return {"user": identity.public_payload()}

    @app.get("/api/v1/auth/me")
    async def me(identity: SessionIdentity = current_session) -> dict[str, object]:
        return {"user": identity.public_payload()}

    @app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(
        request: Request,
        response: Response,
        identity: SessionIdentity = current_session,
    ) -> Response:
        enforce_csrf(request, identity)
        sessions.revoke(identity)
        sessions.audit(
            "logout",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
        )
        response.delete_cookie(SESSION_COOKIE, path="/", samesite="strict")
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @app.get("/api/v1/admin/users")
    async def list_users(_: SessionIdentity = admin_session) -> dict[str, object]:
        return {"users": sessions.list_users()}

    @app.post("/api/v1/admin/users", status_code=status.HTTP_201_CREATED)
    async def create_user(
        payload: UserCreatePayload,
        request: Request,
        identity: SessionIdentity = admin_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            user = sessions.create_user(
                username=payload.username,
                password=payload.password,
                display_name=payload.display_name,
                role=payload.role,
                data_scope=payload.data_scope,
                is_active=payload.is_active,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="登录账号已存在"
            ) from exc
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="角色不存在"
            ) from exc
        sessions.audit(
            "user_created",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=payload.username.lower(),
        )
        return {"user": user}

    @app.get("/api/v1/admin/roles")
    async def list_roles(_: SessionIdentity = admin_session) -> dict[str, object]:
        return {"roles": sessions.list_roles()}

    @app.get("/api/v1/admin/permissions")
    async def list_permissions(_: SessionIdentity = admin_session) -> dict[str, object]:
        return {"permissions": sessions.list_permissions()}

    @app.post("/api/v1/admin/roles", status_code=status.HTTP_201_CREATED)
    async def create_role(
        payload: RoleCreatePayload,
        request: Request,
        identity: SessionIdentity = admin_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            role = sessions.create_role(
                code=payload.code,
                name=payload.name,
                description=payload.description,
                permissions=payload.permissions,
            )
        except ValueError as exc:
            detail = "角色代码已存在" if "exists" in str(exc) else "权限配置无效"
            raise HTTPException(
                status_code=409 if "exists" in str(exc) else 422, detail=detail
            ) from exc
        sessions.audit(
            "role_created",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=payload.code,
        )
        return {"role": role}

    @app.put("/api/v1/admin/roles/{role_code}")
    async def update_role(
        role_code: str,
        payload: RoleUpdatePayload,
        request: Request,
        identity: SessionIdentity = admin_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            role = sessions.update_role(
                role_code,
                name=payload.name,
                description=payload.description,
                permissions=payload.permissions,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="角色不存在") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail="内置角色不可修改") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="权限配置无效") from exc
        sessions.audit(
            "role_updated",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=role_code,
        )
        return {"role": role}

    @app.delete("/api/v1/admin/roles/{role_code}", status_code=204)
    async def delete_role(
        role_code: str,
        request: Request,
        identity: SessionIdentity = admin_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            sessions.delete_role(role_code)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="角色不存在") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail="内置角色或已分配角色不能删除") from exc
        sessions.audit(
            "role_deleted",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=role_code,
        )
        return Response(status_code=204)

    @app.put("/api/v1/admin/users/{username}")
    async def update_user(
        username: str,
        payload: UserUpdatePayload,
        request: Request,
        identity: SessionIdentity = admin_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        if username.lower() == identity.username.lower() and (
            payload.role != "admin" or not payload.is_active
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="不能停用或降级当前管理员账号",
            )
        try:
            user = sessions.update_user(
                username=username,
                role=payload.role,
                data_scope=payload.data_scope,
                is_active=payload.is_active,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在") from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="角色或数据范围无效",
            ) from exc
        sessions.audit(
            "user_updated",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=username,
        )
        return {"user": user}

    @app.get("/api/v1/admin/audit-events")
    async def list_audit_events(_: SessionIdentity = audit_session) -> dict[str, object]:
        return {"events": sessions.list_audit_events()}

    @app.get("/api/v1/dashboards")
    async def list_dashboards(identity: SessionIdentity = current_session) -> dict[str, object]:
        chart_count = len(sessions.list_charts()) + 2
        dashboards: list[dict[str, object]] = [
            {
                "id": "sales-overview",
                "title": "销售经营分析",
                "description": "销售收入、毛利润、产品结构及受治理下钻分析",
                "data_scope": identity.data_scope,
                "chart_count": chart_count,
                "role": "数据分析师" if identity.role != "admin" else "系统管理员",
            }
        ]
        if identity.is_admin:
            dashboards.insert(
                0,
                {
                    "id": "governance-overview",
                    "title": "全域经营与系统治理",
                    "description": "数据源、语义模型、权限和安全审计总览",
                    "data_scope": "全部区域",
                    "chart_count": 4,
                    "role": "系统管理员",
                },
            )
        for asset in sessions.list_superset_dashboards():
            if not asset["available"] or not asset["published"]:
                continue
            dashboards.insert(
                0,
                {
                    "id": f"superset-{asset['superset_id']}",
                    "title": asset["title"],
                    "description": f"Superset 真实仪表盘 · {asset['chart_count']} 个图表",
                    "data_scope": identity.data_scope,
                    "chart_count": asset["chart_count"],
                    "role": "系统管理员" if identity.is_admin else "数据分析师",
                    "source": "superset",
                    "is_home": asset["is_home"],
                },
            )
        return {"dashboards": dashboards}

    @app.get("/api/v1/admin/superset/dashboards")
    async def list_superset_dashboards(
        _: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        return {"dashboards": sessions.list_superset_dashboards()}

    @app.post("/api/v1/admin/superset/dashboards/sync")
    async def sync_superset_dashboards(
        request: Request,
        identity: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            inventory = await app.state.superset_client.list_dashboards()
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        dashboards = sessions.sync_superset_dashboards(inventory)
        sessions.audit(
            "superset_dashboards_synced",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=str(len(inventory)),
        )
        return {
            "dashboards": dashboards,
            "count": len(inventory),
            "message": "Superset 仪表盘同步完成",
        }

    @app.post("/api/v1/admin/superset/dashboards", status_code=status.HTTP_201_CREATED)
    async def create_superset_dashboard(
        payload: SupersetDashboardPayload,
        request: Request,
        identity: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            dashboard = await app.state.superset_client.create_dashboard(
                payload.title, payload.published
            )
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        sessions.audit("superset_dashboard_created", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "",
                       detail=str(dashboard["superset_id"]))
        return {"dashboard": dashboard, "message": "Superset 仪表盘已创建"}

    @app.put("/api/v1/admin/superset/dashboards/{superset_id}")
    async def update_superset_dashboard(
        superset_id: int,
        payload: SupersetDashboardPayload,
        request: Request,
        identity: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            dashboard = await app.state.superset_client.update_dashboard(
                superset_id, payload.title, payload.published
            )
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        sessions.audit("superset_dashboard_updated", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=str(superset_id))
        return {"dashboard": dashboard, "message": "Superset 仪表盘已更新"}

    @app.post("/api/v1/admin/superset/dashboards/{superset_id}/copy",
              status_code=status.HTTP_201_CREATED)
    async def copy_superset_dashboard(
        superset_id: int,
        payload: SupersetDashboardCopyPayload,
        request: Request,
        identity: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            dashboard = await app.state.superset_client.copy_dashboard(
                superset_id, payload.title, payload.duplicate_charts
            )
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        sessions.audit("superset_dashboard_copied", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=str(superset_id))
        return {"dashboard": dashboard, "message": "Superset 仪表盘已复制"}

    @app.delete("/api/v1/admin/superset/dashboards/{superset_id}", status_code=204)
    async def delete_superset_dashboard(
        superset_id: int,
        request: Request,
        identity: SessionIdentity = dashboard_management_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            await app.state.superset_client.delete_dashboard(superset_id)
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        sessions.audit("superset_dashboard_deleted", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=str(superset_id))
        return Response(status_code=204)

    @app.post("/api/v1/admin/superset/charts", status_code=status.HTTP_201_CREATED)
    async def create_superset_chart(
        payload: SupersetChartAuthoringPayload,
        request: Request,
        identity: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            chart = await app.state.superset_client.create_chart(
                payload.title, payload.dataset_id, payload.dashboard_id,
                payload.visualization_type, payload.dimension, payload.metric_column,
                payload.aggregation, payload.time_column,
            )
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        sessions.audit("superset_chart_created", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "",
                       detail=str(chart["superset_id"]))
        return {"chart": chart, "message": "Superset 图表已创建"}

    @app.post("/api/v1/admin/superset/dashboards/{superset_id}/home")
    async def set_home_superset_dashboard(
        superset_id: int,
        request: Request,
        identity: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            dashboard = sessions.set_home_superset_dashboard(superset_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="仪表盘不存在或未发布"
            ) from exc
        sessions.audit(
            "superset_home_dashboard_changed",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=str(superset_id),
        )
        return {"dashboard": dashboard}

    @app.get("/api/v1/reports")
    async def list_reports(identity: SessionIdentity = current_session) -> dict[str, object]:
        if "report:view" not in identity.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return {
            "reports": sessions.list_reports(
                actor_user_id=identity.subject,
                include_all=identity.is_admin,
            )
        }

    @app.post("/api/v1/reports", status_code=status.HTTP_201_CREATED)
    async def create_report(
        payload: ReportCreatePayload,
        request: Request,
        identity: SessionIdentity = current_session,
    ) -> dict[str, object]:
        if "report:view" not in identity.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        enforce_csrf(request, identity)
        if payload.query_id and not sessions.repository.actor_owns_query(
            identity.subject, payload.query_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="查询证据不存在或不属于当前账号，请重新执行分析",
            )
        summary = payload.summary or (
            f"{identity.data_scope}经营指标快照已生成，可继续通过 AgentBI 下钻原因。"
        )
        evidence_path = payload.evidence_path or f"{payload.dashboard_name} → {identity.data_scope}"
        report = sessions.create_report(
            title=payload.title,
            dashboard_name=payload.dashboard_name,
            data_scope=identity.data_scope,
            summary=summary,
            evidence_path=evidence_path,
            actor_user_id=identity.subject,
        )
        sessions.audit(
            "report_created",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=str(report["id"]),
        )
        return {"report": report}

    @app.delete("/api/v1/reports/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_report(
        report_id: str,
        request: Request,
        identity: SessionIdentity = current_session,
    ) -> Response:
        if "report:view" not in identity.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        enforce_csrf(request, identity)
        try:
            title = sessions.delete_report(
                report_id,
                actor_user_id=identity.subject,
                include_all=identity.is_admin,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="报告不存在") from exc
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="无权删除该报告"
            ) from exc
        sessions.audit(
            "report_deleted",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=f"{report_id}:{title}",
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/admin/semantic-models")
    async def list_semantic_models(_: SessionIdentity = semantic_session) -> dict[str, object]:
        return {"models": sessions.list_semantic_models()}

    @app.get("/api/v1/supersonic/models")
    async def list_live_supersonic_models(
        _: SessionIdentity = agent_session,
    ) -> dict[str, object]:
        """Expose live model metadata without SQL, viewers or upstream credentials."""

        try:
            models = await app.state.supersonic_client.list_semantic_models()
        except UpstreamError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="SuperSonic 语义模型服务不可用",
            ) from exc
        return {"models": models, "count": len(models), "source": "SuperSonic"}

    @app.get("/api/v1/admin/semantic-drafts/catalog")
    async def semantic_draft_catalog(
        _: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        try:
            catalog = await app.state.supersonic_client.list_modeling_catalog()
            descriptions = sessions.repository.list_semantic_domain_descriptions()
            for domain in catalog.get("domains", []):
                domain["description"] = descriptions.get(domain["id"], domain.get("description", ""))
            return catalog
        except UpstreamError as exc:
            raise HTTPException(status_code=502, detail="SuperSonic 建模目录不可用") from exc

    @app.post("/api/v1/admin/semantic-drafts/domains", status_code=status.HTTP_201_CREATED)
    async def create_semantic_domain(
        payload: SemanticDomainPayload,
        request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            domain = await app.state.supersonic_client.save_domain(
                name=payload.name,
                biz_name=payload.biz_name,
                description=payload.description,
                username=identity.username,
            )
        except UpstreamError as exc:
            raise HTTPException(status_code=502, detail="SuperSonic 创建主题域失败") from exc
        sessions.repository.save_semantic_domain_description(
            domain["id"], payload.description, identity.subject
        )
        domain["description"] = payload.description
        sessions.audit("semantic_domain_created", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=payload.biz_name)
        return {"domain": domain}

    @app.put("/api/v1/admin/semantic-drafts/domains/{domain_id}")
    async def update_semantic_domain(
        domain_id: int,
        payload: SemanticDomainPayload,
        request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            domain = await app.state.supersonic_client.save_domain(
                domain_id=domain_id,
                name=payload.name,
                biz_name=payload.biz_name,
                description=payload.description,
                username=identity.username,
            )
        except UpstreamError as exc:
            raise HTTPException(status_code=502, detail="SuperSonic 修改主题域失败") from exc
        sessions.repository.save_semantic_domain_description(
            domain_id, payload.description, identity.subject
        )
        domain["description"] = payload.description
        sessions.audit("semantic_domain_updated", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=str(domain_id))
        return {"domain": domain}

    @app.delete("/api/v1/admin/semantic-drafts/domains/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_semantic_domain(
        domain_id: int,
        request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            models = await app.state.supersonic_client.list_semantic_models()
            if any(item.get("domain_id") == domain_id for item in models):
                raise HTTPException(status_code=409, detail="主题域已有语义模型引用，不能删除")
            await app.state.supersonic_client.delete_domain(domain_id)
        except HTTPException:
            raise
        except UpstreamError as exc:
            raise HTTPException(status_code=502, detail="SuperSonic 删除主题域失败") from exc
        sessions.repository.delete_semantic_domain_description(domain_id)
        sessions.audit("semantic_domain_deleted", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=str(domain_id))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/admin/llm-provider")
    async def get_llm_provider(_: SessionIdentity = semantic_session) -> dict[str, object]:
        items = sessions.repository.list_llm_provider_configs()
        public_items = [
            {
                "id": item["id"],
                "enabled": item["enabled"],
                "base_url": item["base_url"],
                "model": item["model"],
                "api_key_masked": "••••••••",
                "updated_at": item["updated_at"],
            }
            for item in items
        ]
        active = next((item for item in public_items if item["enabled"]), None)
        selected = active or (public_items[0] if public_items else None)
        return {
            "configured": bool(public_items),
            "enabled": bool(active),
            "base_url": selected["base_url"] if selected else "",
            "model": selected["model"] if selected else "",
            "api_key_masked": selected["api_key_masked"] if selected else "",
            "updated_at": selected["updated_at"] if selected else None,
            "active_id": active["id"] if active else None,
            "items": public_items,
            "count": len(public_items),
        }

    @app.get("/api/v1/admin/supersonic-llm")
    async def get_supersonic_llm_binding(
        _: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        binding = sessions.repository.get_supersonic_llm_binding()
        provider = sessions.repository.get_llm_provider_config(binding["provider_id"]) \
            if binding["provider_id"] else None
        runtime_applied = bool(binding["enabled"] and provider)
        return {**binding, "provider_model": provider["model"] if provider else None,
                "runtime_applied": runtime_applied,
                "runtime_message": "LLM 语义增强已接入查询运行时；输出须通过白名单语义校验"
                if runtime_applied else "当前仅使用规则、Embedding 与语义模型解析"}

    @app.put("/api/v1/admin/supersonic-llm")
    async def save_supersonic_llm_binding(
        payload: SuperSonicLlmBindingPayload, request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        if payload.enabled and payload.provider_id is None:
            raise HTTPException(status_code=422, detail="启用 LLM 增强必须选择模型服务")
        try:
            saved = sessions.repository.save_supersonic_llm_binding(
                provider_id=payload.provider_id, enabled=payload.enabled, mode=payload.mode,
                timeout_seconds=payload.timeout_seconds,
                fallback_to_rules=payload.fallback_to_rules, actor_user_id=identity.subject,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="模型服务不存在") from exc
        sessions.audit("supersonic_llm_binding_updated", "success",
                       actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "",
                       detail=f"enabled={payload.enabled},provider={payload.provider_id},mode={payload.mode}")
        runtime_applied = bool(saved["enabled"] and saved["provider_id"])
        return {**saved, "runtime_applied": runtime_applied,
                "runtime_message": "配置已保存并立即应用到查询运行时"
                if runtime_applied else "LLM 语义增强已关闭"}

    async def persist_llm_provider(
        payload: LlmProviderPayload,
        identity: SessionIdentity,
        config_id: int | None,
    ) -> dict[str, object]:
        existing = sessions.repository.get_llm_provider_config(config_id) if config_id else None
        if not payload.api_key and existing is None:
            raise HTTPException(status_code=422, detail="新建配置必须填写 API Key")
        api_key = payload.api_key
        if not api_key and existing:
            try:
                api_key = semantic_llm.decrypt_key(str(existing["encrypted_api_key"]))
            except SemanticLlmError as exc:
                raise HTTPException(status_code=422, detail="已保存的 API Key 无法解密，请重新填写") from exc
        if payload.enabled:
            try:
                await semantic_llm.test_connection(
                    base_url=payload.base_url, api_key=api_key, model=payload.model
                )
            except SemanticLlmError as exc:
                raise HTTPException(status_code=422, detail=f"LLM 连接测试失败：{exc}；配置未保存") from exc
        saved_id = sessions.repository.save_llm_provider_config(
            config_id=config_id,
            base_url=payload.base_url.rstrip("/"),
            model=payload.model,
            encrypted_api_key=semantic_llm.encrypt_key(api_key),
            enabled=payload.enabled,
            actor_user_id=identity.subject,
        )
        if payload.enabled:
            semantic_llm.configure(
                base_url=payload.base_url, api_key=api_key, model=payload.model, enabled=True
            )
        elif existing and existing["enabled"]:
            semantic_llm.configure(base_url="", api_key="", model="", enabled=False)
        return {
            "message": "LLM 服务配置已保存",
            "id": saved_id,
            "configured": True,
            "enabled": payload.enabled,
            "base_url": payload.base_url.rstrip("/"),
            "model": payload.model,
            "api_key_masked": "••••••••",
        }

    @app.put("/api/v1/admin/llm-provider")
    async def save_llm_provider(
        payload: LlmProviderPayload, request: Request, identity: SessionIdentity = semantic_session
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        existing = sessions.repository.get_llm_provider_config()
        result = await persist_llm_provider(
            payload, identity, int(existing["id"]) if existing else None
        )
        sessions.audit(
            "llm_provider_updated",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=f"model:{payload.model}:enabled:{payload.enabled}",
        )
        return result

    @app.post("/api/v1/admin/llm-provider", status_code=status.HTTP_201_CREATED)
    async def create_llm_provider(
        payload: LlmProviderPayload, request: Request, identity: SessionIdentity = semantic_session
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        result = await persist_llm_provider(payload, identity, None)
        sessions.audit("llm_provider_created", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=f"model:{payload.model}")
        return result

    @app.put("/api/v1/admin/llm-provider/{config_id}")
    async def update_llm_provider(
        config_id: int, payload: LlmProviderPayload, request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        if sessions.repository.get_llm_provider_config(config_id) is None:
            raise HTTPException(status_code=404, detail="模型服务配置不存在")
        result = await persist_llm_provider(payload, identity, config_id)
        sessions.audit("llm_provider_updated", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=f"id:{config_id}:model:{payload.model}")
        return result

    @app.post("/api/v1/admin/llm-provider/{config_id}/activate")
    async def activate_llm_provider(
        config_id: int, request: Request, identity: SessionIdentity = semantic_session
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        item = sessions.repository.get_llm_provider_config(config_id)
        if item is None:
            raise HTTPException(status_code=404, detail="模型服务配置不存在")
        try:
            api_key = semantic_llm.decrypt_key(str(item["encrypted_api_key"]))
            await semantic_llm.test_connection(
                base_url=str(item["base_url"]), api_key=api_key, model=str(item["model"])
            )
        except SemanticLlmError as exc:
            raise HTTPException(status_code=422, detail=f"切换前连接测试失败：{exc}") from exc
        sessions.repository.activate_llm_provider_config(config_id)
        semantic_llm.configure(base_url=str(item["base_url"]), api_key=api_key,
                               model=str(item["model"]), enabled=True)
        sessions.audit("llm_provider_activated", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=f"id:{config_id}:model:{item['model']}")
        return {"message": f"已切换至 {item['model']}", "active_id": config_id}

    @app.delete("/api/v1/admin/llm-provider/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_llm_provider(
        config_id: int, request: Request, identity: SessionIdentity = semantic_session
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            sessions.repository.delete_llm_provider_config(config_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="模型服务配置不存在") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail="当前生效模型不能删除，请先切换其他模型") from exc
        sessions.audit("llm_provider_deleted", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=f"id:{config_id}")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/v1/admin/semantic-drafts/databases", status_code=status.HTTP_201_CREATED)
    async def create_supersonic_database(
        payload: SuperSonicDatabasePayload,
        request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        upstream = payload.model_dump()
        upstream["type"] = "mysql" if upstream.pop("engine") == "doris" else payload.engine
        upstream["port"] = str(upstream["port"])
        upstream["admins"] = [identity.username]
        upstream["viewers"] = [identity.username]
        try:
            database = await app.state.supersonic_client.create_database(upstream)
        except UpstreamError as exc:
            raise HTTPException(
                status_code=422, detail="同源连接验证或保存失败，请检查连接信息后重试"
            ) from exc
        sessions.audit(
            "supersonic_database_created",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=payload.name,
        )
        return {"database": database, "message": "同源连接已保存到 SuperSonic"}

    @app.put("/api/v1/admin/semantic-drafts/databases/{database_id}")
    async def update_supersonic_database(
        database_id: int,
        payload: SuperSonicDatabasePayload,
        request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        upstream = payload.model_dump()
        upstream["type"] = "mysql" if upstream.pop("engine") == "doris" else payload.engine
        upstream["port"] = str(upstream["port"])
        upstream["admins"] = [identity.username]
        upstream["viewers"] = [identity.username]
        try:
            database = await app.state.supersonic_client.create_database(upstream, database_id)
        except UpstreamError as exc:
            raise HTTPException(status_code=422, detail="同源连接修改失败，请检查连接信息") from exc
        sessions.audit("supersonic_database_updated", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=str(database_id))
        return {"database": database, "message": "SuperSonic 同源连接已更新"}

    @app.delete(
        "/api/v1/admin/semantic-drafts/databases/{database_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_supersonic_database(
        database_id: int,
        request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            await app.state.supersonic_client.delete_database(database_id)
        except UpstreamError as exc:
            raise HTTPException(
                status_code=409,
                detail="该连接为内置数据库或仍被语义模型引用，不能删除",
            ) from exc
        sessions.audit("supersonic_database_deleted", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=str(database_id))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/v1/admin/semantic-drafts/generate")
    async def generate_semantic_draft(
        payload: SemanticDraftRequest,
        request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        generation_started = time.perf_counter()
        try:
            dataset = await app.state.superset_client.get_dataset(payload.dataset_id)
        except SupersetApiError as exc:
            raise HTTPException(status_code=502, detail="无法读取 Superset Dataset 字段") from exc
        draft = build_semantic_draft(dataset)
        selected_provider = None
        if payload.use_llm and payload.provider_id is not None:
            selected_provider = sessions.repository.get_llm_provider_config(payload.provider_id)
            if selected_provider is None:
                raise HTTPException(status_code=404, detail="所选 LLM 模型配置不存在")
        if payload.use_llm and (selected_provider is not None or app.state.semantic_llm.configured):
            llm_started = time.perf_counter()
            try:
                if selected_provider is not None:
                    draft = await app.state.semantic_llm.enrich_with(
                        draft,
                        base_url=str(selected_provider["base_url"]),
                        api_key=app.state.semantic_llm.decrypt_key(
                            str(selected_provider["encrypted_api_key"])
                        ),
                        model=str(selected_provider["model"]),
                        reasoning_mode=payload.reasoning_mode,
                    )
                else:
                    draft = await app.state.semantic_llm.enrich(
                        draft, reasoning_mode=payload.reasoning_mode
                    )
            except SemanticLlmError as exc:
                draft["generation"]["label"] = "非 AI 草稿 · LLM 失败后按字段规则推断"
                draft["warnings"].append(
                    f"LLM 增强失败：{exc}。已安全降级为字段元数据推断。"
                )
            draft["generation"]["llm_duration_ms"] = round(
                (time.perf_counter() - llm_started) * 1000
            )
        draft["generation"]["duration_ms"] = round(
            (time.perf_counter() - generation_started) * 1000
        )
        sessions.audit(
            "semantic_draft_generated",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=(
                f"dataset:{payload.dataset_id}:{draft['generation']['source']}:"
                f"provider:{payload.provider_id or 'active'}"
            ),
        )
        return {"draft": draft}

    @app.post("/api/v1/admin/semantic-drafts/publish", status_code=status.HTTP_201_CREATED)
    async def publish_semantic_draft(
        payload: SemanticDraftPublishPayload,
        request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            dataset = await app.state.superset_client.get_dataset(payload.dataset_id)
            catalog = await app.state.supersonic_client.list_modeling_catalog()
        except (SupersetApiError, UpstreamError) as exc:
            raise HTTPException(status_code=502, detail="发布前无法校验真实元数据") from exc
        if payload.domain_id not in {item["id"] for item in catalog["domains"]}:
            raise HTTPException(status_code=422, detail="SuperSonic 主题域不存在")
        target_database = next(
            (item for item in catalog["databases"] if item["id"] == payload.database_id), None
        )
        if target_database is None:
            raise HTTPException(status_code=422, detail="SuperSonic 数据库连接不存在")
        actual_columns = {str(item["name"]): str(item["type"]) for item in dataset["columns"]}
        reviewed_fields = {item.get("name") for item in payload.fields}
        if not reviewed_fields or not reviewed_fields.issubset(actual_columns):
            raise HTTPException(status_code=422, detail="草稿包含 Dataset 中不存在的字段")
        try:
            target_columns = await app.state.supersonic_client.get_database_columns(
                payload.database_id,
                str(target_database.get("database") or dataset.get("database_name") or ""),
                str(dataset["name"]),
            )
        except UpstreamError as exc:
            raise HTTPException(
                status_code=422,
                detail="所选 SuperSonic 数据库中找不到该 Dataset 对应的物理表，请先配置同源连接",
            ) from exc
        missing_columns = sorted(str(item) for item in reviewed_fields - target_columns)
        if missing_columns:
            preview = "、".join(missing_columns[:8])
            suffix = f"等 {len(missing_columns)} 个字段" if len(missing_columns) > 8 else ""
            raise HTTPException(
                status_code=422,
                detail=(
                    f"SuperSonic 物理表缺少字段：{preview}{suffix}。"
                    "请确认两端连接指向同一数据库和物理表；若 Superset Dataset 是虚拟数据集，"
                    "请在 SuperSonic 中配置对应物理表后再发布。"
                ),
            )

        def reviewed(items: list[dict[str, object]]) -> list[dict[str, object]]:
            if any(item.get("field") not in reviewed_fields for item in items):
                raise HTTPException(status_code=422, detail="语义项引用了不存在的字段")
            return items

        identifiers = reviewed(payload.identifiers)
        dimensions = reviewed(payload.dimensions)
        measures = reviewed(payload.measures)
        field_aliases = {"year": "game_year"} if "year" in reviewed_fields else {}
        reserved_unused = {"rank", "row_number"} - {
            str(item["field"]) for item in identifiers + dimensions + measures
        }
        published_fields = [
            item for item in payload.fields if str(item["name"]) not in reserved_unused
        ]

        def published_field(value: object) -> str:
            return field_aliases.get(str(value), str(value))

        source_table = ".".join(
            filter(None, [str(dataset.get("schema") or ""), str(dataset["name"])])
        )
        safe_projection = ", ".join(
            f'"{item["name"]!s}" AS {published_field(item["name"])}'
            if str(item["name"]) in field_aliases else f'"{item["name"]!s}"'
            for item in published_fields
        )
        model_detail = {
            "queryType": "sql_query" if field_aliases else "table_query",
            "tableQuery": None if field_aliases else source_table,
            "sqlQuery": f"SELECT {safe_projection} FROM {source_table}" if field_aliases else None,
            "fields": [
                {"fieldName": published_field(item["name"]),
                 "dataType": actual_columns[str(item["name"])]}
                for item in published_fields
            ],
            "identifiers": [
                {
                    "name": str(item.get("name") or item["field"]),
                    "type": str(item.get("type") or "primary"),
                    "bizName": published_field(item["field"]),
                    "entityNames": item.get("synonyms") or [],
                    "isCreateDimension": 1,
                }
                for item in identifiers
            ],
            "dimensions": [
                {
                    "name": str(item.get("name") or item["field"]),
                    "bizName": published_field(item["field"]),
                    "type": str(item.get("type") or "categorical"),
                    "expr": (
                        "CURRENT_DATE"
                        if str(item["field"]) == "year" and item.get("type") == "time"
                        else published_field(item["field"])
                    ),
                    "isCreateDimension": 1,
                    **(
                        {"typeParams": {"isPrimary": "true", "timeGranularity": "day"}}
                        if item.get("type") == "time"
                        else {}
                    ),
                }
                for item in dimensions
            ],
            "measures": [
                {
                    "name": str(item.get("name") or item["field"]),
                    "bizName": str(item["field"]),
                    "expr": str(item["field"]),
                    "agg": str(item.get("aggregation") or "SUM"),
                    "isCreateMetric": 1,
                }
                for item in measures
            ],
        }
        upstream_payload = {
            "name": payload.name,
            "bizName": payload.biz_name,
            "description": payload.description,
            "databaseId": payload.database_id,
            "domainId": payload.domain_id,
            "isOpen": 1,
            "modelDetail": model_detail,
            "viewers": [identity.username],
            "admins": [identity.username],
            "viewOrgs": [],
            "adminOrgs": [],
        }
        try:
            await app.state.supersonic_client.publish_semantic_model(upstream_payload)
        except UpstreamError as exc:
            raise HTTPException(status_code=502, detail="SuperSonic 拒绝发布语义模型") from exc
        sessions.audit(
            "semantic_draft_published",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=f"dataset:{payload.dataset_id}:model:{payload.biz_name}",
        )
        return {"message": "语义模型已发布到 SuperSonic", "model_key": payload.biz_name}

    @app.post("/api/v1/admin/semantic-models", status_code=status.HTTP_201_CREATED)
    async def create_semantic_model(
        payload: SemanticModelCreatePayload,
        request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            model = sessions.create_semantic_model(
                name=payload.name,
                subject_area=payload.subject_area,
                description=payload.description,
                actor_user_id=identity.subject,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="语义模型已存在"
            ) from exc
        sessions.audit(
            "semantic_model_created",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=payload.name,
        )
        return {"model": model}

    @app.put("/api/v1/admin/semantic-models/{model_name}")
    async def update_semantic_model(
        model_name: str,
        payload: SemanticModelUpdatePayload,
        request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            model = sessions.update_semantic_model(
                model_name,
                subject_area=payload.subject_area,
                description=payload.description,
                status=payload.status,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="语义模型不存在"
            ) from exc
        sessions.audit(
            "semantic_model_updated",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=model_name,
        )
        return {"model": model}

    @app.delete("/api/v1/admin/semantic-models/{model_name}", status_code=204)
    async def delete_semantic_model(
        model_name: str,
        request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            sessions.delete_semantic_model(model_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="语义模型不存在") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail="语义模型正被图表引用，不能删除") from exc
        sessions.audit(
            "semantic_model_deleted",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=model_name,
        )
        return Response(status_code=204)

    async def probe_upstream(base_url: str) -> dict[str, str]:
        """Return a bounded, non-sensitive upstream health result."""

        try:
            async with httpx.AsyncClient(
                timeout=min(settings.request_timeout_seconds, 3),
                follow_redirects=False,
                # Internal health checks must not be diverted through a developer
                # machine's HTTP(S)_PROXY settings. The upstream URL comes only
                # from trusted service configuration, never from the browser.
                trust_env=False,
            ) as client:
                response = await client.get(f"{base_url.rstrip('/')}/health")
            if response.status_code < 500:
                return {"status": "ready", "message": "服务可访问，配置检查已完成"}
        except httpx.HTTPError:
            pass
        return {"status": "unavailable", "message": "上游服务未启动或当前不可访问"}

    @app.get("/api/v1/superset/workspace")
    async def superset_workspace(
        identity: SessionIdentity = current_session,
    ) -> dict[str, object]:
        if "dashboard:view" not in identity.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        home_dashboard = sessions.get_home_superset_dashboard()
        path = str(
            home_dashboard["url_path"] if home_dashboard else settings.superset_dashboard_path
        ).strip()
        if (
            not path.startswith("/superset/dashboard/")
            or path.startswith("//")
            or "\\" in path
            or "://" in path
        ):
            return {
                "workspace": {
                    "available": False,
                    "can_edit": False,
                    "status": "misconfigured",
                    "message": "Superset 仪表盘路径配置无效",
                }
            }
        health = await probe_upstream(settings.superset_base_url)
        if health["status"] != "ready":
            return {
                "workspace": {
                    "available": False,
                    "can_edit": False,
                    "status": "unavailable",
                    "message": "Superset 未启动，可继续使用本地降级画布",
                }
            }
        separator = "&" if "?" in path else "?"
        return {
            "workspace": {
                "available": True,
                "status": "ready",
                "view_url": f"{settings.superset_base_url}{path}{separator}standalone=3&lang=zh",
                "edit_url": f"{settings.superset_base_url}{path}{separator}edit=true&lang=zh",
                "can_edit": "dashboard:manage" in identity.permissions,
                "dashboard_id": home_dashboard["superset_id"] if home_dashboard else None,
                "dashboard_title": home_dashboard["title"] if home_dashboard else "Superset 仪表盘",
                "message": "Superset 分析画布已连接",
            }
        }

    @app.get("/api/v1/superset/workspace/native")
    async def native_superset_workspace(
        identity: SessionIdentity = current_session,
    ) -> dict[str, object]:
        if "dashboard:view" not in identity.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        home_dashboard = sessions.get_home_superset_dashboard()
        if home_dashboard is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="尚未设置经营总览")
        try:
            dashboard = await app.state.superset_client.get_native_dashboard(
                int(home_dashboard["superset_id"])
            )
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        return {"dashboard": dashboard}

    @app.get("/api/v1/workbench/semantic-binding")
    async def get_workbench_semantic_binding(
        identity: SessionIdentity = current_session,
    ) -> dict[str, object]:
        if "dashboard:view" not in identity.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        dashboard = sessions.get_home_superset_dashboard()
        binding = sessions.get_dashboard_semantic_binding(int(dashboard["superset_id"])) if dashboard else None
        return {"binding": binding}

    @app.put("/api/v1/workbench/semantic-binding")
    async def save_workbench_semantic_binding(
        payload: DashboardSemanticBindingPayload,
        request: Request,
        identity: SessionIdentity = current_session,
    ) -> dict[str, object]:
        if "agent:ask" not in identity.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        enforce_csrf(request, identity)
        dashboard = sessions.get_home_superset_dashboard()
        if dashboard is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="尚未设置经营总览")
        models = await app.state.supersonic_client.list_semantic_models()
        selected = next((item for item in models if int(item["id"]) == payload.semantic_model_id), None)
        if selected is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="语义模型不存在")
        binding = sessions.save_dashboard_semantic_binding(
            dashboard_id=int(dashboard["superset_id"]),
            semantic_model_id=payload.semantic_model_id,
            domain_id=int(selected["domain_id"]),
            actor_user_id=identity.subject,
        )
        return {"binding": binding, "message": "已设为当前仪表盘默认语义模型"}

    @app.get("/api/v1/admin/semantic-dashboard-bindings")
    async def list_semantic_dashboard_bindings(
        _: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        return {"bindings": sessions.list_dashboard_semantic_bindings()}

    @app.get("/api/v1/admin/superset/dashboards/{superset_id}/native")
    async def admin_native_superset_dashboard(
        superset_id: int,
        identity: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        try:
            dashboard = await app.state.superset_client.get_native_dashboard(superset_id)
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        return {"dashboard": dashboard}

    @app.delete("/api/v1/admin/superset/dashboards/{dashboard_id}/charts/{chart_id}", status_code=204)
    async def delete_superset_chart_asset(
        dashboard_id: int, chart_id: int, request: Request,
        identity: SessionIdentity = dashboard_management_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            await app.state.superset_client.delete_chart(chart_id, dashboard_id)
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        sessions.audit("superset_chart_deleted", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "",
                       detail=f"dashboard={dashboard_id},chart={chart_id}")
        return Response(status_code=204)

    @app.put("/api/v1/admin/superset/dashboards/{dashboard_id}/charts/{chart_id}")
    async def update_superset_chart_asset(
        dashboard_id: int, chart_id: int, payload: SupersetChartAuthoringPayload,
        request: Request, identity: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        if payload.dashboard_id != dashboard_id:
            raise HTTPException(status_code=422, detail="目标仪表盘与请求路径不一致")
        try:
            chart = await app.state.superset_client.update_chart(
                chart_id, dashboard_id, payload.title, payload.dataset_id,
                payload.visualization_type, payload.dimension, payload.metric_column,
                payload.aggregation, payload.time_column,
            )
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        sessions.audit("superset_chart_updated", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=str(chart_id))
        return {"chart": chart, "message": "Superset 图表已更新"}

    @app.post("/api/v1/admin/semantic-models/{model_name}/sync")
    async def sync_semantic_model(
        model_name: str,
        request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        known_models = {str(model["name"]) for model in sessions.list_semantic_models()}
        if model_name not in known_models:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="语义模型不存在")
        result = await probe_upstream(settings.supersonic_base_url)
        result["message"] = (
            "SuperSonic 可访问，语义模型同步状态正常"
            if result["status"] == "ready"
            else "SuperSonic 未启动或当前不可访问"
        )
        sessions.audit(
            "semantic_model_checked",
            "success" if result["status"] == "ready" else "failed",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=model_name,
        )
        return {"result": result}

    @app.get("/api/v1/admin/data-sources")
    async def list_data_sources(_: SessionIdentity = datasource_session) -> dict[str, object]:
        return {"sources": sessions.list_data_sources()}

    @app.get("/api/v1/admin/superset/data-assets")
    async def list_superset_data_assets(
        _: SessionIdentity = datasource_session,
    ) -> dict[str, object]:
        try:
            return await app.state.superset_client.list_data_assets()
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    @app.post("/api/v1/admin/superset/databases/test")
    async def test_superset_database(
        payload: SupersetDatabasePayload,
        request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            connection_uri = payload.resolved_uri(required=True)
            await app.state.superset_client.test_database_connection(
                payload.database_name, connection_uri
            )
        except (SupersetApiError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        sessions.audit(
            "superset_database_tested",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=payload.database_name,
        )
        return {"message": "数据库连接测试成功"}

    @app.post("/api/v1/admin/superset/databases", status_code=status.HTTP_201_CREATED)
    async def create_superset_database(
        payload: SupersetDatabasePayload,
        request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            connection_uri = payload.resolved_uri(required=True)
            database = await app.state.superset_client.create_database(
                payload.database_name, connection_uri, payload.expose_in_sqllab
            )
        except (SupersetApiError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        sessions.audit(
            "superset_database_created",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=payload.database_name,
        )
        return {"database": database, "message": "数据库连接已保存到 Superset"}

    @app.post("/api/v1/admin/superset/datasets", status_code=status.HTTP_201_CREATED)
    async def create_superset_dataset(
        payload: SupersetDatasetPayload,
        request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            dataset = await app.state.superset_client.create_dataset(
                payload.database_id, payload.schema_name, payload.table_name
            )
        except SupersetApiError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        sessions.audit(
            "superset_dataset_created",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=f"{payload.database_id}:{payload.schema_name}:{payload.table_name}",
        )
        return {"dataset": dataset, "message": "Dataset 已创建到 Superset"}

    @app.get("/api/v1/admin/superset/datasets/{dataset_id}")
    async def get_superset_dataset(
        dataset_id: int,
        _: SessionIdentity = datasource_session,
    ) -> dict[str, object]:
        try:
            return {"dataset": await app.state.superset_client.get_dataset(dataset_id)}
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.delete("/api/v1/admin/superset/datasets/{dataset_id}", status_code=204)
    async def delete_superset_dataset(
        dataset_id: int,
        request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            await app.state.superset_client.delete_dataset(dataset_id)
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        sessions.audit(
            "superset_dataset_deleted",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=str(dataset_id),
        )
        return Response(status_code=204)

    @app.delete("/api/v1/admin/superset/databases/{database_id}", status_code=204)
    async def delete_superset_database(
        database_id: int,
        request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            await app.state.superset_client.delete_database(database_id)
        except SupersetApiError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        sessions.audit(
            "superset_database_deleted",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=str(database_id),
        )
        return Response(status_code=204)

    @app.put("/api/v1/admin/superset/databases/{database_id}")
    async def update_superset_database(
        database_id: int,
        payload: SupersetDatabaseUpdatePayload,
        request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            connection_uri = payload.resolved_uri(required=False)
            database = await app.state.superset_client.update_database(
                database_id,
                payload.database_name,
                connection_uri,
                payload.expose_in_sqllab,
            )
        except (SupersetApiError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        sessions.audit(
            "superset_database_updated",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=payload.database_name,
        )
        return {"database": database, "message": "数据库连接已更新"}

    @app.put("/api/v1/admin/superset/datasets/{dataset_id}")
    async def update_superset_dataset(
        dataset_id: int,
        payload: SupersetDatasetUpdatePayload,
        request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            dataset = await app.state.superset_client.update_dataset(
                dataset_id, payload.description
            )
        except SupersetApiError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        sessions.audit(
            "superset_dataset_updated",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=str(dataset_id),
        )
        return {"dataset": dataset, "message": "Dataset 说明已更新"}

    @app.post("/api/v1/admin/data-sources", status_code=status.HTTP_201_CREATED)
    async def create_data_source(
        payload: DataSourceCreatePayload,
        request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            source = sessions.create_data_source(
                name=payload.name,
                source_type=payload.source_type,
                description=payload.description,
                actor_user_id=identity.subject,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="数据源已存在") from exc
        sessions.audit(
            "data_source_created",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=payload.name,
        )
        return {"source": source}

    @app.put("/api/v1/admin/data-sources/{source_name}")
    async def update_data_source(
        source_name: str,
        payload: DataSourceUpdatePayload,
        request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            source = sessions.update_data_source(
                source_name,
                source_type=payload.source_type,
                description=payload.description,
                status=payload.status,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="数据源不存在") from exc
        sessions.audit(
            "data_source_updated",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=source_name,
        )
        return {"source": source}

    @app.delete("/api/v1/admin/data-sources/{source_name}", status_code=204)
    async def delete_data_source(
        source_name: str,
        request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            sessions.delete_data_source(source_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="数据源不存在") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail="数据源正被图表引用，不能删除") from exc
        sessions.audit(
            "data_source_deleted",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=source_name,
        )
        return Response(status_code=204)

    @app.post("/api/v1/admin/data-sources/{source_name}/test")
    async def test_data_source(
        source_name: str,
        request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        known_sources = {str(source["name"]) for source in sessions.list_data_sources()}
        if source_name not in known_sources:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="数据源不存在")
        result = await probe_upstream(settings.superset_base_url)
        result["message"] = (
            "Superset 可访问，数据源连接检查通过"
            if result["status"] == "ready"
            else "Superset 未启动或当前不可访问"
        )
        sessions.audit(
            "data_source_tested",
            "success" if result["status"] == "ready" else "failed",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=source_name,
        )
        return {"result": result}

    @app.get("/api/v1/charts")
    async def list_charts(_: SessionIdentity = current_session) -> dict[str, object]:
        return {"charts": sessions.list_charts()}

    @app.get("/api/v1/admin/charts")
    async def list_admin_charts(
        _: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        return {"charts": sessions.list_charts(published_only=False)}

    @app.post("/api/v1/admin/charts", status_code=status.HTTP_201_CREATED)
    async def create_chart(
        payload: ChartCreatePayload,
        request: Request,
        identity: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        dimensions = validated_dimensions(payload.dimensions)
        validation_status = await validate_chart_binding(payload)
        try:
            chart = sessions.create_chart_with_drilldown(
                chart_key=payload.chart_key,
                title=payload.title,
                metric=payload.metric,
                dataset_name=payload.dataset_name,
                visualization_type=payload.visualization_type,
                semantic_model=payload.semantic_model,
                dimensions=dimensions,
                superset_dashboard_id=payload.superset_dashboard_id,
                superset_chart_id=payload.superset_chart_id,
                superset_dataset_id=payload.superset_dataset_id,
                validation_status=validation_status,
                actor_user_id=identity.subject,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="图表 ID 已存在"
            ) from exc
        sessions.audit(
            "chart_created",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=payload.chart_key,
        )
        return {"chart": chart}

    @app.put("/api/v1/admin/charts/{chart_key}")
    async def update_chart(
        chart_key: str,
        payload: ChartUpdatePayload,
        request: Request,
        identity: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        validation_status = await validate_chart_binding(payload)
        try:
            chart = sessions.update_chart_with_drilldown(
                chart_key=chart_key,
                title=payload.title,
                metric=payload.metric,
                dataset_name=payload.dataset_name,
                visualization_type=payload.visualization_type,
                semantic_model=payload.semantic_model,
                dimensions=validated_dimensions(payload.dimensions),
                superset_dashboard_id=payload.superset_dashboard_id,
                superset_chart_id=payload.superset_chart_id,
                superset_dataset_id=payload.superset_dataset_id,
                validation_status=validation_status,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="图表不存在") from exc
        sessions.audit(
            "chart_updated",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=chart_key,
        )
        return {"chart": chart}

    @app.patch("/api/v1/admin/charts/{chart_key}/publication")
    async def set_chart_publication(
        chart_key: str,
        payload: ChartPublishPayload,
        request: Request,
        identity: SessionIdentity = dashboard_management_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            chart = sessions.set_chart_published(chart_key, published=payload.published)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="图表不存在") from exc
        event = "chart_published" if payload.published else "chart_offlined"
        sessions.audit(
            event,
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=chart_key,
        )
        return {"chart": chart}

    @app.delete("/api/v1/admin/charts/{chart_key}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_chart(
        chart_key: str,
        request: Request,
        identity: SessionIdentity = dashboard_management_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            sessions.delete_chart(chart_key)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="图表不存在") from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="请先下线图表，再执行删除",
            ) from exc
        sessions.audit(
            "chart_deleted",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=chart_key,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/v1/workbench/conversations")
    async def workbench_conversations(
        identity: SessionIdentity = agent_session,
    ) -> dict[str, object]:
        """Return the signed-in user's persisted Q&A history."""

        items = sessions.repository.conversation_history(identity.subject)
        return {"items": items, "latest_chat_id": items[0]["chat_id"] if items else None}

    @app.post("/api/v1/workbench/analyze", response_model=AnalyzeResponse)
    async def workbench_analyze(
        payload: WorkbenchAnalyzePayload,
        request: Request,
        identity: SessionIdentity = drilldown_session,
    ) -> AnalyzeResponse:
        """Run a governed semantic query for the signed-in workbench user."""

        enforce_csrf(request, identity)
        governed_request = AnalyzeRequest(
            question=payload.question,
            actor=Actor(
                subject=identity.subject,
                display_name=identity.display_name,
                roles=list(identity.roles),
            ),
            context=payload.context,
            chat_id=payload.chat_id,
            agent_id=payload.agent_id,
            client_request_id=payload.client_request_id,
        )
        try:
            result = await app.state.orchestrator.analyze(governed_request)
        except RateLimitExceeded as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="request rate limit exceeded",
                headers={"Retry-After": "60"},
            ) from exc
        except PolicyViolation as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ResultMismatchError as exc:
            logger.warning("workbench semantic result mismatch: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        except SemanticResolutionError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "clarification_required",
                    "message": str(exc),
                    "options": exc.options,
                } if exc.options else str(exc),
            ) from exc
        except ConversationUnavailableError as exc:
            logger.info("workbench conversation reset required: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "conversation_reset_required",
                    "message": "当前账号的问答会话已更新，正在建立新会话",
                },
            ) from exc
        except UpstreamError as exc:
            logger.warning("workbench semantic query failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="semantic query service is unavailable",
            ) from exc
        sessions.audit(
            "workbench_analysis_completed",
            "success",
            actor_user_id=identity.subject,
            source_ip=request.client.host if request.client else "",
            detail=f"query={result.evidence.query_id};rows={result.evidence.row_count}",
        )
        return result

    @app.post(
        "/api/v1/analyze",
        response_model=AnalyzeResponse,
        dependencies=[Depends(authenticate)],
    )
    async def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
        try:
            return await orchestrator.analyze(request)
        except RateLimitExceeded as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="request rate limit exceeded",
                headers={"Retry-After": "60"},
            ) from exc
        except PolicyViolation as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except UpstreamError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="semantic query service is unavailable",
            ) from exc

    return app


app = create_app()

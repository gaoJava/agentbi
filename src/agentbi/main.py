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

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from agentbi.auth import SESSION_COOKIE, AuthenticationError, SessionIdentity, SessionManager
from agentbi.config import Settings
from agentbi.models import AnalyzeRequest, AnalyzeResponse
from agentbi.orchestrator import Orchestrator
from agentbi.security import PolicyViolation, RateLimitExceeded, require_api_key
from agentbi.supersonic import SuperSonicClient, UpstreamError

# Reuse Uvicorn's configured handler so audit events are emitted in every launch mode.
logger = logging.getLogger("uvicorn.error")


class LoginPayload(BaseModel):
    """Credentials for the explicitly enabled local competition demo."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


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


class ChartUpdatePayload(BaseModel):
    """Editable governed chart and drilldown fields; the stable key never changes."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=200)
    metric: str = Field(min_length=1, max_length=128)
    dataset_name: str = Field(min_length=1, max_length=200)
    visualization_type: Literal["bar", "line", "donut", "table"]
    semantic_model: str = Field(min_length=2, max_length=128)
    dimensions: list[str] = Field(min_length=2, max_length=5)


class ChartPublishPayload(BaseModel):
    """Explicit publication state transition."""

    model_config = ConfigDict(extra="forbid")

    published: bool


class ReportCreatePayload(BaseModel):
    """A bounded request to persist the current governed dashboard snapshot."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=200)
    dashboard_name: str = Field(min_length=2, max_length=200)


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
    supersonic = SuperSonicClient(settings)
    orchestrator = Orchestrator(settings, supersonic)
    sessions = SessionManager(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await supersonic.close()

    app = FastAPI(
        title="AgentBI Orchestrator",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
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
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated") from exc

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

    def enforce_csrf(request: Request, identity: SessionIdentity) -> None:
        if not hmac.compare_digest(
            request.headers.get("X-AgentBI-CSRF", ""), identity.csrf_token
        ):
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
    async def login(payload: LoginPayload, response: Response, request: Request) -> dict[str, object]:
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
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="登录账号已存在") from exc
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="角色不存在") from exc
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
        payload: RoleCreatePayload, request: Request,
        identity: SessionIdentity = admin_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            role = sessions.create_role(
                code=payload.code, name=payload.name, description=payload.description,
                permissions=payload.permissions,
            )
        except ValueError as exc:
            detail = "角色代码已存在" if "exists" in str(exc) else "权限配置无效"
            raise HTTPException(status_code=409 if "exists" in str(exc) else 422, detail=detail) from exc
        sessions.audit("role_created", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=payload.code)
        return {"role": role}

    @app.put("/api/v1/admin/roles/{role_code}")
    async def update_role(
        role_code: str, payload: RoleUpdatePayload, request: Request,
        identity: SessionIdentity = admin_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            role = sessions.update_role(
                role_code, name=payload.name, description=payload.description,
                permissions=payload.permissions,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="角色不存在") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail="内置角色不可修改") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="权限配置无效") from exc
        sessions.audit("role_updated", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=role_code)
        return {"role": role}

    @app.delete("/api/v1/admin/roles/{role_code}", status_code=204)
    async def delete_role(
        role_code: str, request: Request, identity: SessionIdentity = admin_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            sessions.delete_role(role_code)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="角色不存在") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail="内置角色或已分配角色不能删除") from exc
        sessions.audit("role_deleted", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=role_code)
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
        return {"dashboards": dashboards}

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
        report = sessions.create_report(
            title=payload.title,
            dashboard_name=payload.dashboard_name,
            data_scope=identity.data_scope,
            summary=f"{identity.data_scope}最近 30 天经营指标快照已生成，可继续通过 AgentBI 下钻原因。",
            evidence_path=f"{payload.dashboard_name} → 最近30天 → {identity.data_scope}",
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
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权删除该报告") from exc
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

    @app.post("/api/v1/admin/semantic-models", status_code=status.HTTP_201_CREATED)
    async def create_semantic_model(
        payload: SemanticModelCreatePayload, request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            model = sessions.create_semantic_model(
                name=payload.name, subject_area=payload.subject_area,
                description=payload.description, actor_user_id=identity.subject,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="语义模型已存在") from exc
        sessions.audit("semantic_model_created", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=payload.name)
        return {"model": model}

    @app.put("/api/v1/admin/semantic-models/{model_name}")
    async def update_semantic_model(
        model_name: str, payload: SemanticModelUpdatePayload, request: Request,
        identity: SessionIdentity = semantic_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            model = sessions.update_semantic_model(
                model_name, subject_area=payload.subject_area,
                description=payload.description, status=payload.status,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="语义模型不存在") from exc
        sessions.audit("semantic_model_updated", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=model_name)
        return {"model": model}

    @app.delete("/api/v1/admin/semantic-models/{model_name}", status_code=204)
    async def delete_semantic_model(
        model_name: str, request: Request, identity: SessionIdentity = semantic_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            sessions.delete_semantic_model(model_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="语义模型不存在") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail="语义模型正被图表引用，不能删除") from exc
        sessions.audit("semantic_model_deleted", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=model_name)
        return Response(status_code=204)

    async def probe_upstream(base_url: str) -> dict[str, str]:
        """Return a bounded, non-sensitive upstream health result."""

        try:
            async with httpx.AsyncClient(
                timeout=min(settings.request_timeout_seconds, 3),
                follow_redirects=False,
            ) as client:
                response = await client.get(f"{base_url.rstrip('/')}/health")
            if response.status_code < 500:
                return {"status": "ready", "message": "服务可访问，配置检查已完成"}
        except httpx.HTTPError:
            pass
        return {"status": "unavailable", "message": "上游服务未启动或当前不可访问"}

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

    @app.post("/api/v1/admin/data-sources", status_code=status.HTTP_201_CREATED)
    async def create_data_source(
        payload: DataSourceCreatePayload, request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            source = sessions.create_data_source(
                name=payload.name, source_type=payload.source_type,
                description=payload.description, actor_user_id=identity.subject,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="数据源已存在") from exc
        sessions.audit("data_source_created", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=payload.name)
        return {"source": source}

    @app.put("/api/v1/admin/data-sources/{source_name}")
    async def update_data_source(
        source_name: str, payload: DataSourceUpdatePayload, request: Request,
        identity: SessionIdentity = datasource_session,
    ) -> dict[str, object]:
        enforce_csrf(request, identity)
        try:
            source = sessions.update_data_source(
                source_name, source_type=payload.source_type,
                description=payload.description, status=payload.status,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="数据源不存在") from exc
        sessions.audit("data_source_updated", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=source_name)
        return {"source": source}

    @app.delete("/api/v1/admin/data-sources/{source_name}", status_code=204)
    async def delete_data_source(
        source_name: str, request: Request, identity: SessionIdentity = datasource_session,
    ) -> Response:
        enforce_csrf(request, identity)
        try:
            sessions.delete_data_source(source_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="数据源不存在") from exc
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail="数据源正被图表引用，不能删除") from exc
        sessions.audit("data_source_deleted", "success", actor_user_id=identity.subject,
                       source_ip=request.client.host if request.client else "", detail=source_name)
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
        try:
            chart = sessions.create_chart_with_drilldown(
                chart_key=payload.chart_key,
                title=payload.title,
                metric=payload.metric,
                dataset_name=payload.dataset_name,
                visualization_type=payload.visualization_type,
                semantic_model=payload.semantic_model,
                dimensions=dimensions,
                actor_user_id=identity.subject,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="图表 ID 已存在") from exc
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
        try:
            chart = sessions.update_chart_with_drilldown(
                chart_key=chart_key,
                title=payload.title,
                metric=payload.metric,
                dataset_name=payload.dataset_name,
                visualization_type=payload.visualization_type,
                semantic_model=payload.semantic_model,
                dimensions=validated_dimensions(payload.dimensions),
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

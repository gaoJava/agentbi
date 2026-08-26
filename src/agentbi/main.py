"""FastAPI application factory for the AgentBI orchestration service."""

from __future__ import annotations

import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

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
        allow_methods=["GET", "POST"],
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

    def require_admin(identity: SessionIdentity = current_session) -> SessionIdentity:
        if not identity.is_admin or "user:manage" not in identity.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return identity

    admin_session = Depends(require_admin)

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
        if not hmac.compare_digest(
            request.headers.get("X-AgentBI-CSRF", ""), identity.csrf_token
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid CSRF token")
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

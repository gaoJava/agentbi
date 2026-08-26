"""Security policy enforced independently of model output."""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections import deque
from threading import Lock

from fastapi import Header, HTTPException, status

from agentbi.config import Settings
from agentbi.models import AnalyzeRequest

_PROMPT_CONTROL_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"绕过.{0,8}(权限|限制)"),
    re.compile(r"忽略.{0,8}(指令|规则)"),
)


class PolicyViolation(ValueError):
    """Raised when a request violates a deterministic security policy."""


class RateLimitExceeded(PolicyViolation):
    """Raised before an upstream call when one actor exceeds its request budget."""


class SlidingWindowRateLimiter:
    """Small process-local limiter that bounds accidental or abusive query bursts."""

    def __init__(self, limit: int, window_seconds: float = 60.0):
        if limit <= 0 or window_seconds <= 0:
            raise ValueError("rate limit values must be positive")
        self._limit = limit
        self._window_seconds = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = Lock()

    def enforce(self, actor_key: str, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        cutoff = current - self._window_seconds
        with self._lock:
            events = self._events.setdefault(actor_key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._limit:
                raise RateLimitExceeded("request rate limit exceeded")
            events.append(current)


def require_api_key(settings: Settings):
    """Build a FastAPI dependency without placing secrets in application globals."""

    def verify(x_agentbi_key: str = Header(default="")) -> None:
        if not hmac.compare_digest(x_agentbi_key, settings.api_key):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")

    return verify


def enforce_request_policy(request: AnalyzeRequest) -> None:
    """Reject prompt-control attempts before any upstream or LLM call occurs."""

    if any(pattern.search(request.question) for pattern in _PROMPT_CONTROL_PATTERNS):
        raise PolicyViolation("question contains a prompt-control pattern")
    if not request.actor.roles:
        raise PolicyViolation("at least one authenticated role is required")


def sql_fingerprint(sql: str | None) -> str | None:
    """Return a traceable fingerprint without exposing SQL text to the browser."""

    if not sql:
        return None
    normalized = " ".join(sql.split()).lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

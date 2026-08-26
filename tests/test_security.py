"""Focused tests for deterministic security controls."""

from __future__ import annotations

import pytest

from agentbi.security import RateLimitExceeded, SlidingWindowRateLimiter, sql_fingerprint


def test_rate_limiter_releases_budget_after_window() -> None:
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=10)
    limiter.enforce("user-1", now=100)
    limiter.enforce("user-1", now=101)

    with pytest.raises(RateLimitExceeded):
        limiter.enforce("user-1", now=102)

    limiter.enforce("user-1", now=111)


def test_rate_limiter_is_scoped_per_actor() -> None:
    limiter = SlidingWindowRateLimiter(limit=1)
    limiter.enforce("user-1", now=100)
    limiter.enforce("user-2", now=100)


def test_sql_fingerprint_is_stable_without_exposing_sql() -> None:
    first = sql_fingerprint("SELECT  *  FROM sales")
    second = sql_fingerprint("select * from SALES")

    assert first == second
    assert first is not None and len(first) == 16
    assert "select" not in first

"""Stable execution evidence contracts; they contain no business interpretation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agentbi.runtime_contracts import ExecutionErrorCode


class ExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    SKIPPED = "SKIPPED"



class ExecutionError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: ExecutionErrorCode
    message: str
    technical_message: str | None = None


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    execution_id: str = Field(default_factory=lambda: str(uuid4()))
    analysis_plan_id: str | None = None
    analysis_step_id: str | None = None
    status: ExecutionStatus
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_ms: int = Field(default=0, ge=0)
    query_id: str | None = None
    rows: tuple[dict[str, Any], ...] = ()
    row_count: int = Field(default=0, ge=0)
    columns: tuple[str, ...] = ()
    error: ExecutionError | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    generated_sql: str | None = None


class StepExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    analysis_plan_id: str
    analysis_step_id: str
    status: ExecutionStatus
    results: tuple[ExecutionResult, ...] = ()
    dependency_warning: str | None = None

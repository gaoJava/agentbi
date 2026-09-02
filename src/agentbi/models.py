"""Typed API contracts shared by the Superset adapter and orchestration service."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FilterOperator(StrEnum):
    EQ = "EQ"
    IN = "IN"
    GTE = "GTE"
    LTE = "LTE"


class ScreenFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1, max_length=128)
    operator: FilterOperator
    value: str | int | float | list[str | int | float]


class SelectedDatum(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=256)
    value: str | int | float | None = None
    dimension: str | None = Field(default=None, max_length=128)


class ScreenContext(BaseModel):
    """The minimum trusted context captured from the visible Superset dashboard."""

    model_config = ConfigDict(extra="forbid")

    dashboard_id: str = Field(min_length=1, max_length=128)
    chart_id: str | None = Field(default=None, max_length=128)
    dataset_id: str | None = Field(default=None, max_length=128)
    semantic_model_id: int = Field(gt=0)
    time_range: str = Field(default="", max_length=256)
    filters: list[ScreenFilter] = Field(default_factory=list, max_length=50)
    selected: SelectedDatum | None = None


class Actor(BaseModel):
    """Identity asserted by the trusted Superset backend, never by free-form prompts."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=128)
    roles: list[str] = Field(default_factory=list, max_length=50)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=2, max_length=2000)
    actor: Actor
    context: ScreenContext
    chat_id: int | None = Field(default=None, gt=0)
    agent_id: int | None = Field(default=None, gt=0)
    client_request_id: str | None = Field(
        default=None,
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        return " ".join(value.split())


class AnalysisRanking(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Literal["top", "bottom"] = "top"
    limit: int = Field(default=10, ge=1, le=100)


class AnalysisPlan(BaseModel):
    """Allow-listed plan produced by rules or an LLM before governed execution."""

    model_config = ConfigDict(extra="forbid")
    dimension: Literal["publisher", "platform", "genre"] | None = None
    metric: Literal["global_sales", "na_sales", "eu_sales", "jp_sales", "other_sales"] | None = None
    operation: Literal[
        "list", "rank", "sum", "average", "difference", "ratio", "share",
        "rank_difference", "rank_value",
    ] | None = None
    ranking: AnalysisRanking | None = None
    members: list[str] = Field(default_factory=list, max_length=2)
    ranks: list[int] = Field(default_factory=list, max_length=2)
    confidence: float = Field(default=0.0, ge=0, le=1)
    assumptions: list[str] = Field(default_factory=list, max_length=5)
    needs_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=256)
    clarification_options: list[str] = Field(default_factory=list, max_length=6)


class StepStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class AnalysisStep(BaseModel):
    name: str
    status: StepStatus
    duration_ms: int = Field(ge=0)
    detail: str | None = None


class Evidence(BaseModel):
    query_id: str
    semantic_model_id: int
    question: str
    time_range: str
    filters: list[ScreenFilter]
    row_count: int = Field(ge=0)
    query_time_ms: int | None = Field(default=None, ge=0)
    sql_fingerprint: str | None = None
    generated_sql: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AnalysisReport(BaseModel):
    """Server-generated report content tied to the governed query evidence."""

    title: str
    source_url: str | None = None
    summary: str
    observations: list[str]
    suggested_actions: list[str]
    markdown: str


class AnalyzeResponse(BaseModel):
    request_id: str
    chat_id: int | None = Field(default=None, gt=0)
    answer: str
    data: list[dict[str, Any]]
    evidence: Evidence
    report: AnalysisReport
    steps: list[AnalysisStep]
    warnings: list[str] = Field(default_factory=list)

"""Natural-language-to-IR parser boundary.

Concrete rule, LLM, and hybrid parsers will live behind this port.  NoopSemanticParser
is deliberately non-executable and exists only for dependency assembly during migration.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from agentbi.dashboard_context import DashboardContext
from agentbi.semantic_query_ir import SemanticQueryIR


class ParseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=2_000)
    ontology_version: str = Field(min_length=1, max_length=64)
    dashboard_id: str | None = Field(default=None, max_length=128)
    actor_id: str | None = Field(default=None, max_length=128)
    context: SemanticParseContext | None = None
    dashboard_context: DashboardContext | None = None


class SemanticParseContext(BaseModel):
    """Small structured Workbench context; user text always has precedence."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    metric: str | None = Field(default=None, max_length=256)
    filters: tuple[tuple[str, str], ...] = Field(default_factory=tuple)
    time_range: str | None = Field(default=None, max_length=256)


class Clarification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: str = Field(min_length=1, max_length=512)
    options: tuple[str, ...] = Field(default_factory=tuple, max_length=6)


class ParseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: SemanticQueryIR | None = None
    clarification: Clarification | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class SemanticParser(Protocol):
    async def parse(self, request: ParseRequest) -> ParseResult: ...


class NoopSemanticParser:
    """Migration-safe parser which makes no interpretation and executes nothing."""

    async def parse(self, request: ParseRequest) -> ParseResult:
        return ParseResult(
            clarification=Clarification(reason="semantic parser is not enabled"),
            confidence=0.0,
        )

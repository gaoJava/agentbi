"""Vendor-neutral, bounded dashboard context for semantic interpretation.

This contract is deliberately descriptive.  It can help the parser fill an
omitted metric, filter, or time window, but it carries neither identity nor
authorization and cannot become an executable query on its own.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class DashboardContextSource(StrEnum):
    """How the host adapter obtained its bounded dashboard state."""

    HOST_ADAPTER = "HOST_ADAPTER"


class DashboardContextProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: DashboardContextSource = DashboardContextSource.HOST_ADAPTER
    provider: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    verified_fields: tuple[str, ...] = Field(default_factory=tuple, max_length=16)


class DashboardFilter(BaseModel):
    """A host-visible filter, not an ontology predicate or permission scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str = Field(min_length=1, max_length=128)
    value: str | int | float | tuple[str | int | float, ...]


class DashboardContext(BaseModel):
    """Bounded context supplied by a host adapter after allow-listing.

    All values are hints only.  The semantic parser must still resolve them
    through the published ontology; RuntimeBinding and request authorization
    remain independent enforcement points.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dashboard_id: str = Field(min_length=1, max_length=128)
    time_range: str | None = Field(default=None, min_length=1, max_length=128)
    filters: tuple[DashboardFilter, ...] = Field(default_factory=tuple, max_length=10)
    focused_metric: str | None = Field(default=None, min_length=1, max_length=256)
    selected_chart_id: str | None = Field(default=None, min_length=1, max_length=128)
    selected_dataset_id: str | None = Field(default=None, min_length=1, max_length=128)
    provenance: DashboardContextProvenance


class DashboardContextProvider(Protocol):
    """Port implemented by dashboard hosts; Core never imports a host SDK."""

    def get_dashboard_context(self) -> DashboardContext | None: ...

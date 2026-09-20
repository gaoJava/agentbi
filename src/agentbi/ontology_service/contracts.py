"""Ports and immutable contracts for the AgentBI enterprise ontology."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentbi.semantic_query_ir import AggregateFunction, AnalysisIntent, TimeGrain


class OntologyAssetKind(StrEnum):
    ENTITY = "entity"
    METRIC = "metric"
    DIMENSION = "dimension"
    DATA_MODEL = "data_model"
    SEMANTIC = "semantic"
    RULE = "rule"
    ANALYSIS_STRATEGY = "analysis_strategy"
    CAPABILITY = "capability"


class OntologyRelationType(StrEnum):
    """v0.2 relations with stable semantics for future planning reads.

    ``OntologyRelation`` still accepts an unrecognised string so published v0.1
    releases (for example ``PLACED_BY``) remain readable and immutable.
    """

    MEASURE_OF = "MEASURE_OF"
    HAS_DIMENSION = "HAS_DIMENSION"
    BREAKDOWN_BY = "BREAKDOWN_BY"
    CALCULATED_BY = "CALCULATED_BY"
    MAPPED_TO = "MAPPED_TO"
    DRIVEN_BY = "DRIVEN_BY"
    ANALYZED_BY = "ANALYZED_BY"
    REQUIRES = "REQUIRES"
    PRODUCES = "PRODUCES"


class AnalysisStrategyDefinition(BaseModel):
    """Deliberately small, declarative applicability contract for a strategy.

    It answers whether a strategy may be considered; the future AnalysisPlanner
    will combine it with a grounded target, published ontology context, runtime
    capabilities and observations.  It is intentionally not a general DSL.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    applicable_intents: tuple[AnalysisIntent, ...] = Field(min_length=1)
    requires_target_kind: OntologyAssetKind
    requires_relation: tuple[OntologyRelationType, ...] = Field(default_factory=tuple)
    requires_any_relation: tuple[OntologyRelationType, ...] = Field(default_factory=tuple)
    requires_capability: tuple[str, ...] = Field(default_factory=tuple)
    requires_time_dimension: bool = False

    @model_validator(mode="after")
    def _validate_unique_values(self) -> AnalysisStrategyDefinition:
        for field_name in (
            "applicable_intents",
            "requires_relation",
            "requires_any_relation",
            "requires_capability",
        ):
            values = getattr(self, field_name)
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} values must be unique")
        if any(not capability.strip() for capability in self.requires_capability):
            raise ValueError("requires_capability must not contain blank IDs")
        return self


class PublicationState(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    PUBLISHED = "published"
    RETIRED = "retired"


class ChangeSetState(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class RuntimeBackend(StrEnum):
    """Execution backends whose bindings are governed by ontology publication."""

    SUPERSONIC = "SUPERSONIC"
    SUPERSET = "SUPERSET"


class RuntimeAssetBinding(BaseModel):
    """Exact backend-side identifier for one published metric or dimension."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=128)
    semantic_identifier: str = Field(min_length=1, max_length=256)
    aggregation: AggregateFunction | None = None


class RuntimeTimeGrainBinding(BaseModel):
    """Backend semantic dimension emitted for a governed logical time grain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension_asset_id: str = Field(min_length=1, max_length=128)
    grain: TimeGrain
    semantic_identifier: str = Field(min_length=1, max_length=256)


class RuntimeBinding(BaseModel):
    """Immutable execution contract carried only by an ontology snapshot.

    It deliberately contains semantic identifiers, not SQL or physical source
    fields.  A later structured request builder consumes this exact mapping.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    backend: RuntimeBackend
    connection_id: str = Field(min_length=1, max_length=128)
    data_model_asset_id: str = Field(min_length=1, max_length=128)
    semantic_model_id: int = Field(gt=0)
    semantic_view_id: int = Field(gt=0)
    metric_bindings: tuple[RuntimeAssetBinding, ...] = Field(default_factory=tuple)
    dimension_bindings: tuple[RuntimeAssetBinding, ...] = Field(default_factory=tuple)
    time_grain_bindings: tuple[RuntimeTimeGrainBinding, ...] = Field(default_factory=tuple)
    supported_time_grains: tuple[str, ...] = Field(default_factory=tuple)
    supports_date_filters: bool = False

    @model_validator(mode="after")
    def _validate_exact_membership(self) -> RuntimeBinding:
        for name in ("metric_bindings", "dimension_bindings", "supported_time_grains"):
            values = getattr(self, name)
            keys = [item.asset_id for item in values] if name.endswith("bindings") else list(values)
            if len(keys) != len(set(keys)):
                raise ValueError(f"{name} values must be unique")
        if any(not grain.strip() for grain in self.supported_time_grains):
            raise ValueError("supported_time_grains must not contain blank values")
        time_grain_keys = [(item.dimension_asset_id, item.grain) for item in self.time_grain_bindings]
        if len(time_grain_keys) != len(set(time_grain_keys)):
            raise ValueError("time_grain_bindings values must be unique")
        return self


class OntologyAsset(BaseModel):
    """Versioned business asset; implementation-specific payload stays out of the contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    kind: OntologyAssetKind
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=64)
    state: PublicationState
    aliases: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    description: str = Field(default="", max_length=2_000)
    strategy_definition: AnalysisStrategyDefinition | None = None

    @model_validator(mode="after")
    def _validate_strategy_definition(self) -> OntologyAsset:
        is_strategy = self.kind is OntologyAssetKind.ANALYSIS_STRATEGY
        if is_strategy and self.strategy_definition is None:
            raise ValueError("analysis strategy assets require a strategy_definition")
        if not is_strategy and self.strategy_definition is not None:
            raise ValueError("strategy_definition is only valid for analysis strategy assets")
        return self


class OntologyRelation(BaseModel):
    """Directed relation suitable for projection into NebulaGraph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=128)
    relation: OntologyRelationType | str = Field(min_length=1, max_length=128)
    target_id: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)

    @field_validator("relation")
    @classmethod
    def _normalize_controlled_relation(
        cls, relation: OntologyRelationType | str
    ) -> OntologyRelationType | str:
        """Use the enum for v0.2 values while preserving arbitrary legacy edges."""

        if isinstance(relation, OntologyRelationType):
            return relation
        try:
            return OntologyRelationType(relation)
        except ValueError:
            return relation


class OntologySnapshot(BaseModel):
    """A coherent published ontology view used by parsing and planning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1, max_length=64)
    published_at: datetime
    assets: tuple[OntologyAsset, ...] = Field(default_factory=tuple)
    relations: tuple[OntologyRelation, ...] = Field(default_factory=tuple)
    runtime_bindings: tuple[RuntimeBinding, ...] = Field(default_factory=tuple)


class OntologyChangeSet(BaseModel):
    """Reviewable candidate for one future immutable ontology version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=36)
    target_version: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    state: ChangeSetState
    created_by: str = Field(min_length=1, max_length=128)
    created_at: datetime
    submitted_at: datetime | None = None
    approved_at: datetime | None = None
    published_at: datetime | None = None
    assets: tuple[OntologyAsset, ...] = Field(default_factory=tuple)
    relations: tuple[OntologyRelation, ...] = Field(default_factory=tuple)
    runtime_bindings: tuple[RuntimeBinding, ...] = Field(default_factory=tuple)


class ExecutionMapping(BaseModel):
    """Published semantic/data-model assets associated with one business asset.

    This is declarative mapping context only.  It contains no connector,
    physical-table, SQL, or runtime tool binding details.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=128)
    mapped_assets: tuple[OntologyAsset, ...] = Field(default_factory=tuple)


class OntologyRepository(Protocol):
    """Persistence port; PostgreSQL and NebulaGraph adapters implement this later."""

    async def get_published_snapshot(
        self, version: str | None = None
    ) -> OntologySnapshot | None: ...

    async def find_assets_by_alias(
        self, alias: str, *, kind: OntologyAssetKind | None = None, version: str | None = None
    ) -> tuple[OntologyAsset, ...]: ...


def _validate_published_snapshot(snapshot: OntologySnapshot) -> None:
    """Enforce the immutable-release invariant shared by every repository."""

    assets = {asset.id: asset for asset in snapshot.assets}
    if len(assets) != len(snapshot.assets):
        raise ValueError("ontology asset IDs must be unique within a version")
    if any(asset.version != snapshot.version for asset in snapshot.assets):
        raise ValueError("ontology assets must use the snapshot version")
    if any(asset.state is not PublicationState.PUBLISHED for asset in snapshot.assets):
        raise ValueError("only published assets may appear in a published snapshot")
    for relation in snapshot.relations:
        if relation.version != snapshot.version:
            raise ValueError("ontology relations must use the snapshot version")
        if relation.source_id not in assets or relation.target_id not in assets:
            raise ValueError("ontology relations must reference published snapshot assets")
    _validate_runtime_bindings(snapshot.runtime_bindings, assets)


def _validate_runtime_bindings(
    bindings: tuple[RuntimeBinding, ...], assets: dict[str, OntologyAsset]
) -> None:
    if len({binding.id for binding in bindings}) != len(bindings):
        raise ValueError("runtime binding IDs must be unique within a version")
    for binding in bindings:
        model = assets.get(binding.data_model_asset_id)
        if model is None or model.kind is not OntologyAssetKind.DATA_MODEL:
            raise ValueError("runtime binding must reference a data-model asset")
        for item in binding.metric_bindings:
            asset = assets.get(item.asset_id)
            if asset is None or asset.kind is not OntologyAssetKind.METRIC:
                raise ValueError("metric runtime binding must reference a metric asset")
        for item in binding.dimension_bindings:
            asset = assets.get(item.asset_id)
            if asset is None or asset.kind is not OntologyAssetKind.DIMENSION:
                raise ValueError("dimension runtime binding must reference a dimension asset")
        for item in binding.time_grain_bindings:
            asset = assets.get(item.dimension_asset_id)
            if asset is None or asset.kind is not OntologyAssetKind.DIMENSION:
                raise ValueError("time grain runtime binding must reference a dimension asset")
            if item.grain.value not in binding.supported_time_grains:
                raise ValueError("time grain runtime binding must declare a supported grain")


class InMemoryOntologyRepository:
    """Validated published snapshots for the first ontology-service milestone.

    This adapter is intentionally useful in tests and local composition only.  Its
    validation rules define the publication invariant that a future PostgreSQL /
    NebulaGraph adapter must preserve: an edge can only join assets from the same,
    fully published snapshot.
    """

    def __init__(self, snapshots: tuple[OntologySnapshot, ...] = ()):
        self._snapshots: dict[str, OntologySnapshot] = {}
        for snapshot in snapshots:
            self.publish(snapshot)

    def publish(self, snapshot: OntologySnapshot) -> None:
        if snapshot.version in self._snapshots:
            raise ValueError(f"ontology version already published: {snapshot.version}")
        _validate_published_snapshot(snapshot)
        self._snapshots[snapshot.version] = snapshot

    async def get_published_snapshot(self, version: str | None = None) -> OntologySnapshot | None:
        if version is not None:
            return self._snapshots.get(version)
        if not self._snapshots:
            return None
        return max(self._snapshots.values(), key=lambda snapshot: snapshot.published_at)

    async def find_assets_by_alias(
        self, alias: str, *, kind: OntologyAssetKind | None = None, version: str | None = None
    ) -> tuple[OntologyAsset, ...]:
        snapshot = await self.get_published_snapshot(version)
        if snapshot is None:
            return ()
        normalized = _normalize_alias(alias)
        return tuple(
            asset for asset in snapshot.assets
            if (kind is None or asset.kind is kind)
            and normalized in {_normalize_alias(value) for value in (asset.name, *asset.aliases)}
        )


class OntologyService:
    """Thin application service exposing version-pinned ontology reads."""

    def __init__(self, repository: OntologyRepository):
        self._repository = repository

    async def published_snapshot(self, version: str | None = None) -> OntologySnapshot | None:
        return await self._repository.get_published_snapshot(version)

    async def resolve_alias(
        self, alias: str, *, kind: OntologyAssetKind | None = None, version: str | None = None
    ) -> tuple[OntologyAsset, ...]:
        return await self._repository.find_assets_by_alias(alias, kind=kind, version=version)

    async def resolve_asset(
        self,
        text: str,
        *,
        expected_kind: OntologyAssetKind | None = None,
        version: str | None = None,
    ) -> tuple[OntologyAsset, ...]:
        """Resolve one name/alias only within a published, version-pinned snapshot."""

        return await self.resolve_alias(text, kind=expected_kind, version=version)

    async def get_metric_drivers(
        self, metric_id: str, *, version: str | None = None
    ) -> tuple[OntologyAsset, ...]:
        return await self._direct_related_assets(
            metric_id, relation=OntologyRelationType.DRIVEN_BY,
            target_kind=OntologyAssetKind.METRIC, version=version,
        )

    async def get_breakdown_dimensions(
        self, metric_id: str, *, version: str | None = None
    ) -> tuple[OntologyAsset, ...]:
        """Return only business-approved ``BREAKDOWN_BY`` dimensions."""

        return await self._direct_related_assets(
            metric_id, relation=OntologyRelationType.BREAKDOWN_BY,
            target_kind=OntologyAssetKind.DIMENSION, version=version,
        )

    async def get_dimensions(
        self, metric_id: str, *, version: str | None = None
    ) -> tuple[OntologyAsset, ...]:
        """Return direct HAS_DIMENSION and BREAKDOWN_BY dimensions, preserving their relation semantics elsewhere."""

        snapshot = await self.published_snapshot(version)
        if snapshot is None:
            return ()
        assets = {asset.id: asset for asset in snapshot.assets}
        seen: set[str] = set()
        dimensions: list[OntologyAsset] = []
        for relation in snapshot.relations:
            if relation.source_id != metric_id or relation.relation not in {
                OntologyRelationType.HAS_DIMENSION, OntologyRelationType.BREAKDOWN_BY,
            }:
                continue
            asset = assets.get(relation.target_id)
            if asset is not None and asset.kind is OntologyAssetKind.DIMENSION and asset.id not in seen:
                seen.add(asset.id)
                dimensions.append(asset)
        return tuple(dimensions)

    async def get_strategy_definitions(
        self, *, version: str | None = None
    ) -> tuple[OntologyAsset, ...]:
        snapshot = await self.published_snapshot(version)
        if snapshot is None:
            return ()
        return tuple(
            asset for asset in snapshot.assets
            if asset.kind is OntologyAssetKind.ANALYSIS_STRATEGY
        )

    async def get_capabilities(self, *, version: str | None = None) -> tuple[OntologyAsset, ...]:
        snapshot = await self.published_snapshot(version)
        if snapshot is None:
            return ()
        return tuple(asset for asset in snapshot.assets if asset.kind is OntologyAssetKind.CAPABILITY)

    async def get_execution_mapping(
        self, asset_id: str, *, version: str | None = None
    ) -> ExecutionMapping:
        """Return published semantic/data-model mappings without execution details.

        ``IMPLEMENTS`` is intentionally accepted as a read-only v0.1 mapping
        compatibility edge while new releases should use ``MAPPED_TO``.
        """

        snapshot = await self.published_snapshot(version)
        if snapshot is None:
            return ExecutionMapping(asset_id=asset_id)
        assets = {asset.id: asset for asset in snapshot.assets}
        compatible_relations = {OntologyRelationType.MAPPED_TO, "IMPLEMENTS"}
        related: list[OntologyAsset] = []
        for relation in snapshot.relations:
            if relation.relation not in compatible_relations:
                continue
            other_id = (
                relation.target_id if relation.source_id == asset_id
                else relation.source_id if relation.target_id == asset_id
                else None
            )
            asset = assets.get(other_id) if other_id is not None else None
            if asset is not None and asset.kind in {OntologyAssetKind.DATA_MODEL, OntologyAssetKind.SEMANTIC}:
                related.append(asset)
        return ExecutionMapping(asset_id=asset_id, mapped_assets=tuple(related))

    async def get_relation_types(
        self, asset_id: str, *, version: str | None = None
    ) -> tuple[OntologyRelationType | str, ...]:
        """Expose only direct, typed business structure for applicability checks."""

        snapshot = await self.published_snapshot(version)
        if snapshot is None:
            return ()
        return tuple(relation.relation for relation in snapshot.relations if relation.source_id == asset_id)

    async def _direct_related_assets(
        self,
        asset_id: str,
        *,
        relation: OntologyRelationType,
        target_kind: OntologyAssetKind,
        version: str | None,
    ) -> tuple[OntologyAsset, ...]:
        snapshot = await self.published_snapshot(version)
        if snapshot is None:
            return ()
        assets = {asset.id: asset for asset in snapshot.assets}
        return tuple(
            asset for edge in snapshot.relations
            if edge.source_id == asset_id and edge.relation is relation
            if (asset := assets.get(edge.target_id)) is not None and asset.kind is target_kind
        )

    async def related_assets(
        self,
        asset_id: str,
        *,
        relation: str | None = None,
        version: str | None = None,
        max_hops: int = 1,
    ) -> tuple[OntologyAsset, ...]:
        """Return reachable business assets in a version-pinned directed graph.

        The caller controls the relation type and hop count explicitly; this keeps
        graph traversal deterministic and prevents a broad graph search from being
        mistaken for an executable data-access decision.
        """

        if not 1 <= max_hops <= 5:
            raise ValueError("max_hops must be between 1 and 5")
        snapshot = await self.published_snapshot(version)
        if snapshot is None or asset_id not in {asset.id for asset in snapshot.assets}:
            return ()
        adjacency: dict[str, list[str]] = {}
        for edge in snapshot.relations:
            if relation is None or edge.relation == relation:
                adjacency.setdefault(edge.source_id, []).append(edge.target_id)
        assets = {asset.id: asset for asset in snapshot.assets}
        seen = {asset_id}
        queue: deque[tuple[str, int]] = deque([(asset_id, 0)])
        result: list[OntologyAsset] = []
        while queue:
            source_id, depth = queue.popleft()
            if depth == max_hops:
                continue
            for target_id in adjacency.get(source_id, []):
                if target_id in seen:
                    continue
                seen.add(target_id)
                result.append(assets[target_id])
                queue.append((target_id, depth + 1))
        return tuple(result)


def _normalize_alias(value: str) -> str:
    return " ".join(value.strip().casefold().split())

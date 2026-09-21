"""Deterministic resolution of published runtime bindings only."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agentbi.runtime_contracts import ExecutionErrorCode

from .contracts import (
    OntologyAssetKind,
    OntologyService,
    RuntimeAssetBinding,
    RuntimeBackend,
    RuntimeBinding,
)


class RuntimeBindingResolutionError(RuntimeError):
    """Typed, deterministic refusal to resolve an execution binding."""

    def __init__(self, code: ExecutionErrorCode, message: str):
        super().__init__(message)
        self.code = code


class ResolvedRuntimeBinding(BaseModel):
    """Version-pinned exact result for a later semantic request builder."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_version: str
    binding: RuntimeBinding
    asset_id: str
    semantic_identifier: str


class RuntimeBindingResolver:
    """Resolve asset IDs exactly; aliases, field names and fallbacks are forbidden."""

    def __init__(self, ontology_service: OntologyService):
        self._ontology_service = ontology_service

    async def resolve(
        self, *, snapshot_version: str, asset_id: str, backend: RuntimeBackend
    ) -> ResolvedRuntimeBinding:
        snapshot = await self._ontology_service.published_snapshot(snapshot_version)
        if snapshot is None:
            raise RuntimeBindingResolutionError(
                ExecutionErrorCode.UNPUBLISHED_RUNTIME_BINDING,
                f"no published ontology snapshot: {snapshot_version}",
            )
        asset = next((item for item in snapshot.assets if item.id == asset_id), None)
        if asset is None:
            raise RuntimeBindingResolutionError(
                ExecutionErrorCode.MISSING_RUNTIME_BINDING,
                f"no published runtime binding for asset: {asset_id}",
            )
        bindings = tuple(
            item for item in snapshot.runtime_bindings
            if item.data_model_asset_id == asset_id
            or any(entry.asset_id == asset_id for entry in item.metric_bindings)
            or any(entry.asset_id == asset_id for entry in item.dimension_bindings)
        )
        matching_backend = tuple(item for item in bindings if item.backend is backend)
        if bindings and not matching_backend:
            raise RuntimeBindingResolutionError(
                ExecutionErrorCode.RUNTIME_BACKEND_MISMATCH,
                f"asset {asset_id} is not bound to backend {backend.value}",
            )
        if not matching_backend:
            raise RuntimeBindingResolutionError(
                ExecutionErrorCode.UNSUPPORTED_RUNTIME_CAPABILITY,
                f"published backend {backend.value} does not support asset: {asset_id}",
            )
        binding = matching_backend[0]
        entry = _entry_for(asset.kind, asset_id, binding)
        if entry is None:
            raise RuntimeBindingResolutionError(
                ExecutionErrorCode.UNSUPPORTED_RUNTIME_CAPABILITY,
                f"published backend {backend.value} does not support asset: {asset_id}",
            )
        return ResolvedRuntimeBinding(
            snapshot_version=snapshot.version,
            binding=binding,
            asset_id=asset_id,
            semantic_identifier=entry.semantic_identifier,
        )


def _entry_for(
    kind: OntologyAssetKind, asset_id: str, binding: RuntimeBinding
) -> RuntimeAssetBinding | None:
    if kind is OntologyAssetKind.DATA_MODEL and binding.data_model_asset_id == asset_id:
        return RuntimeAssetBinding(asset_id=asset_id, semantic_identifier=str(binding.semantic_model_id))
    entries = binding.metric_bindings if kind is OntologyAssetKind.METRIC else binding.dimension_bindings
    return next((item for item in entries if item.asset_id == asset_id), None)

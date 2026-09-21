"""Governance and exact-resolution coverage for Phase 6.3B RuntimeBinding."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agentbi.execution import ExecutionErrorCode
from agentbi.ontology_service import (
    OntologyAsset,
    OntologyAssetKind,
    OntologyService,
    PostgresOntologyRepository,
    PublicationState,
    RuntimeBackend,
    RuntimeBindingResolutionError,
    RuntimeBindingResolver,
    verified_supersonic_sales_binding,
)


def _assets(version: str, *, state: PublicationState = PublicationState.DRAFT):
    items = [
        ("datamodel.sales_order_line", OntologyAssetKind.DATA_MODEL, "销售订单行"),
        ("metric.revenue", OntologyAssetKind.METRIC, "Revenue"),
        ("metric.customer_count", OntologyAssetKind.METRIC, "CustomerCount"),
        ("metric.order_count", OntologyAssetKind.METRIC, "OrderCount"),
        ("metric.purchase_frequency", OntologyAssetKind.METRIC, "PurchaseFrequency"),
        ("metric.average_order_value", OntologyAssetKind.METRIC, "AverageOrderValue"),
        ("dimension.region", OntologyAssetKind.DIMENSION, "Region"),
        ("dimension.product", OntologyAssetKind.DIMENSION, "Product"),
        ("dimension.customer", OntologyAssetKind.DIMENSION, "Customer"),
        ("dimension.order_date", OntologyAssetKind.DIMENSION, "OrderDate"),
        # Intentionally present in ontology but absent from verified runtime capability.
        ("dimension.channel", OntologyAssetKind.DIMENSION, "Channel"),
    ]
    return tuple(OntologyAsset(id=key, kind=kind, name=name, version=version, state=state)
                 for key, kind, name in items)


async def _publish(repository: PostgresOntologyRepository, version: str) -> None:
    proposal = await repository.create_change_set(
        target_version=version, title="verified SuperSonic sales runtime binding",
        created_by="data-owner", assets=_assets(version), relations=(),
        runtime_bindings=(verified_supersonic_sales_binding(),),
    )
    await repository.submit_for_review(proposal.id, actor="data-owner")
    await repository.record_review(proposal.id, reviewer="business-owner", approved=True)
    await repository.publish_change_set(
        proposal.id, publisher="release-manager",
        published_at=datetime(2026, 9, 16, tzinfo=UTC),
    )


def _resolver(repository: PostgresOntologyRepository) -> RuntimeBindingResolver:
    return RuntimeBindingResolver(OntologyService(repository))


def test_draft_binding_cannot_resolve_before_publication() -> None:
    repository = PostgresOntologyRepository("sqlite:///:memory:")
    repository.initialize()
    asyncio.run(repository.create_change_set(
        target_version="2026.3b-draft", title="draft binding", created_by="owner",
        assets=_assets("2026.3b-draft"), relations=(),
        runtime_bindings=(verified_supersonic_sales_binding(),),
    ))
    with pytest.raises(RuntimeBindingResolutionError) as error:
        asyncio.run(_resolver(repository).resolve(
            snapshot_version="2026.3b-draft", asset_id="metric.revenue",
            backend=RuntimeBackend.SUPERSONIC,
        ))
    assert error.value.code is ExecutionErrorCode.UNPUBLISHED_RUNTIME_BINDING


def test_published_binding_resolves_exact_metrics_and_dimensions() -> None:
    version = "2026.3b.1"
    repository = PostgresOntologyRepository("sqlite:///:memory:")
    repository.initialize(); asyncio.run(_publish(repository, version))
    resolver = _resolver(repository)
    metric = asyncio.run(resolver.resolve(
        snapshot_version=version, asset_id="metric.average_order_value",
        backend=RuntimeBackend.SUPERSONIC,
    ))
    dimension = asyncio.run(resolver.resolve(
        snapshot_version=version, asset_id="dimension.order_date",
        backend=RuntimeBackend.SUPERSONIC,
    ))
    assert metric.binding.semantic_model_id == 13 and metric.binding.semantic_view_id == 8
    assert metric.semantic_identifier == "average_order_value"
    assert dimension.semantic_identifier == "order_date"


def test_channel_unknown_asset_and_backend_mismatch_never_guess() -> None:
    version = "2026.3b.2"
    repository = PostgresOntologyRepository("sqlite:///:memory:")
    repository.initialize(); asyncio.run(_publish(repository, version))
    resolver = _resolver(repository)
    for asset_id, backend, code in (
        ("dimension.channel", RuntimeBackend.SUPERSONIC, ExecutionErrorCode.UNSUPPORTED_RUNTIME_CAPABILITY),
        ("dimension.region_alias", RuntimeBackend.SUPERSONIC, ExecutionErrorCode.MISSING_RUNTIME_BINDING),
        ("metric.revenue", RuntimeBackend.SUPERSET, ExecutionErrorCode.RUNTIME_BACKEND_MISMATCH),
    ):
        with pytest.raises(RuntimeBindingResolutionError) as error:
            asyncio.run(resolver.resolve(
                snapshot_version=version, asset_id=asset_id, backend=backend,
            ))
        assert error.value.code is code


def test_published_snapshot_is_immutable_and_binding_survives_reload(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'ontology.db'}"
    old_version, new_version = "2026.3b.3", "2026.3b.4"
    repository = PostgresOntologyRepository(database_url)
    repository.initialize(); asyncio.run(_publish(repository, old_version))
    # A new ChangeSet yields a new snapshot; publishing it cannot alter v1.
    asyncio.run(_publish(repository, new_version))
    reloaded = PostgresOntologyRepository(database_url)
    reloaded.initialize()
    resolver = _resolver(reloaded)
    old = asyncio.run(resolver.resolve(
        snapshot_version=old_version, asset_id="metric.revenue", backend=RuntimeBackend.SUPERSONIC,
    ))
    new = asyncio.run(resolver.resolve(
        snapshot_version=new_version, asset_id="metric.revenue", backend=RuntimeBackend.SUPERSONIC,
    ))
    assert old.snapshot_version == old_version
    assert new.snapshot_version == new_version
    assert old.binding == new.binding
    assert old.binding.semantic_view_id == 8

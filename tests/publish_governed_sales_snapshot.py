"""One-off governed publication command for Product Integration Closure."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import create_engine

# Preserve the existing package initialization order used by execution tests.
import agentbi.execution  # noqa: F401
from agentbi.config import Settings
from agentbi.ontology_service import OntologyService, PostgresOntologyRepository
from agentbi.ontology_service.demo import (
    verified_supersonic_sales_binding,
    verified_supersonic_sales_ontology,
)

VERSION = "2026.3b-governed-sales-v3"


async def publish() -> None:
    repository = PostgresOntologyRepository(create_engine(Settings.from_env().database_url))
    service = OntologyService(repository)
    existing = await service.published_snapshot(VERSION)
    if existing is not None:
        print(f"EXISTING {existing.version} bindings={len(existing.runtime_bindings)}")
        return
    assets, relations = verified_supersonic_sales_ontology(VERSION)
    proposal = await repository.create_change_set(
        target_version=VERSION,
        title="Governed SuperSonic sales runtime binding",
        created_by="agentbi-product-integration",
        assets=assets,
        relations=relations,
        runtime_bindings=(verified_supersonic_sales_binding(),),
    )
    await repository.submit_for_review(proposal.id, actor="agentbi-product-integration")
    await repository.record_review(proposal.id, reviewer="agentbi-product-review", approved=True,
                                   comment="verified Model 13 / View 8 binding")
    snapshot = await repository.publish_change_set(
        proposal.id, publisher="agentbi-product-release",
        published_at=datetime.now(UTC),
    )
    reloaded = await service.published_snapshot(VERSION)
    assert reloaded is not None
    print(f"CHANGESET {proposal.id}")
    print(f"PUBLISHED {snapshot.version} assets={len(snapshot.assets)} bindings={len(snapshot.runtime_bindings)}")
    print(f"RELOADED {reloaded.version} assets={len(reloaded.assets)} bindings={len(reloaded.runtime_bindings)}")


if __name__ == "__main__":
    asyncio.run(publish())

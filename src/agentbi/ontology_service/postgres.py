"""SQLAlchemy persistence adapter for immutable published ontology snapshots.

PostgreSQL is the authoritative registry.  The model deliberately uses portable
SQLAlchemy columns so contract tests can exercise the same publication rules on
SQLite; production uses ``AGENTBI_DATABASE_URL`` with PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agentbi.database import (
    Base,
    OntologyAssetRecord,
    OntologyChangeSetAssetRecord,
    OntologyChangeSetRecord,
    OntologyChangeSetRelationRecord,
    OntologyChangeSetReviewRecord,
    OntologyPublicationRecord,
    OntologyRelationRecord,
    utc_now,
)

from .contracts import (
    AnalysisStrategyDefinition,
    ChangeSetState,
    OntologyAsset,
    OntologyAssetKind,
    OntologyChangeSet,
    OntologyRelation,
    OntologySnapshot,
    PublicationState,
    RuntimeBinding,
    _normalize_alias,
    _validate_published_snapshot,
    _validate_runtime_bindings,
)


class PostgresOntologyRepository:
    """Persist and retrieve immutable ontology releases.

    ``Postgres`` names its production responsibility rather than its test
    dialect.  Snapshot creation is one transaction: callers never observe an
    incomplete release, and a version can never be overwritten.
    """

    def __init__(self, engine_or_url: Engine | str):
        if isinstance(engine_or_url, str):
            engine_args: dict[str, object] = {}
            if engine_or_url.startswith("sqlite"):
                engine_args["connect_args"] = {"check_same_thread": False}
            if engine_or_url == "sqlite:///:memory:":
                engine_args["poolclass"] = StaticPool
            self._engine = create_engine(engine_or_url, **engine_args)
        else:
            self._engine = engine_or_url

    def initialize(self) -> None:
        """Create registry tables without modifying an already published release."""

        Base.metadata.create_all(self._engine)
        # ``create_all`` is additive only for new tables.  The registry predates
        # strategy definitions, so keep its one-column migration local and
        # additive until the project adopts a general migration framework.
        with self._engine.begin() as connection:
            for table_name in ("ontology_assets", "ontology_change_set_assets"):
                columns = {
                    column["name"]
                    for column in connection.dialect.get_columns(
                        connection, table_name
                    )
                }
                if "strategy_definition_json" not in columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} "
                        "ADD COLUMN strategy_definition_json TEXT DEFAULT 'null' NOT NULL"
                    )
            for table_name in ("ontology_publications", "ontology_change_sets"):
                columns = {
                    column["name"]
                    for column in connection.dialect.get_columns(connection, table_name)
                }
                if "runtime_bindings_json" not in columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} "
                        "ADD COLUMN runtime_bindings_json TEXT DEFAULT '[]' NOT NULL"
                    )

    async def publish(
        self, snapshot: OntologySnapshot, *, published_by: str | None = None
    ) -> None:
        _validate_published_snapshot(snapshot)
        with Session(self._engine) as db, db.begin():
            _persist_snapshot(db, snapshot, published_by=published_by)

    async def create_change_set(
        self,
        *,
        target_version: str,
        title: str,
        created_by: str,
        assets: tuple[OntologyAsset, ...],
        relations: tuple[OntologyRelation, ...],
        runtime_bindings: tuple[RuntimeBinding, ...] = (),
    ) -> OntologyChangeSet:
        """Create an editable proposal; it is deliberately not queryable as published truth."""

        _validate_change_set_members(target_version, assets, relations, runtime_bindings)
        change_set_id = str(uuid4())
        with Session(self._engine) as db, db.begin():
            if db.get(OntologyPublicationRecord, target_version) is not None:
                raise ValueError(f"ontology version already published: {target_version}")
            if db.scalar(select(OntologyChangeSetRecord.id).where(
                OntologyChangeSetRecord.target_version == target_version
            )) is not None:
                raise ValueError(f"ontology change set already exists: {target_version}")
            db.add(OntologyChangeSetRecord(
                id=change_set_id, target_version=target_version, title=title,
                status=ChangeSetState.DRAFT.value, created_by=created_by,
                runtime_bindings_json=_runtime_bindings_json(runtime_bindings),
            ))
            # There are no ORM relationships between the normalized records;
            # flush the parent explicitly so PostgreSQL can enforce the child FK.
            db.flush()
            db.add_all(OntologyChangeSetAssetRecord(
                change_set_id=change_set_id, asset_id=asset.id, kind=asset.kind.value,
                name=asset.name, state=asset.state.value,
                aliases_json=json.dumps(list(asset.aliases), ensure_ascii=False),
                description=asset.description,
                strategy_definition_json=_strategy_definition_json(asset.strategy_definition),
            ) for asset in assets)
            db.add_all(OntologyChangeSetRelationRecord(
                change_set_id=change_set_id, source_id=relation.source_id,
                relation=relation.relation, target_id=relation.target_id,
            ) for relation in relations)
        created = await self.get_change_set(change_set_id)
        assert created is not None
        return created

    async def get_change_set(self, change_set_id: str) -> OntologyChangeSet | None:
        with Session(self._engine) as db:
            record = db.get(OntologyChangeSetRecord, change_set_id)
            if record is None:
                return None
            assets = tuple(
                OntologyAsset(
                    id=item.asset_id, kind=OntologyAssetKind(item.kind), name=item.name,
                    version=record.target_version, state=PublicationState(item.state),
                    aliases=_aliases(item.aliases_json), description=item.description,
                    strategy_definition=_strategy_definition(item.strategy_definition_json),
                )
                for item in db.scalars(select(OntologyChangeSetAssetRecord).where(
                    OntologyChangeSetAssetRecord.change_set_id == change_set_id
                ).order_by(OntologyChangeSetAssetRecord.asset_id))
            )
            relations = tuple(
                OntologyRelation(source_id=item.source_id, relation=item.relation,
                                 target_id=item.target_id, version=record.target_version)
                for item in db.scalars(select(OntologyChangeSetRelationRecord).where(
                    OntologyChangeSetRelationRecord.change_set_id == change_set_id
                ).order_by(OntologyChangeSetRelationRecord.source_id,
                           OntologyChangeSetRelationRecord.relation,
                           OntologyChangeSetRelationRecord.target_id))
            )
            return OntologyChangeSet(
                id=record.id, target_version=record.target_version, title=record.title,
                state=ChangeSetState(record.status), created_by=record.created_by,
                created_at=_utc(record.created_at), submitted_at=_utc(record.submitted_at),
                approved_at=_utc(record.approved_at), published_at=_utc(record.published_at),
                assets=assets, relations=relations,
                runtime_bindings=_runtime_bindings(record.runtime_bindings_json),
            )

    async def list_change_sets(self) -> tuple[OntologyChangeSet, ...]:
        """List proposals newest first for the interactive governance workbench."""

        with Session(self._engine) as db:
            identifiers = list(db.scalars(select(OntologyChangeSetRecord.id).order_by(
                OntologyChangeSetRecord.created_at.desc()
            )))
        items = [await self.get_change_set(identifier) for identifier in identifiers]
        return tuple(item for item in items if item is not None)

    async def submit_for_review(self, change_set_id: str, *, actor: str) -> OntologyChangeSet:
        with Session(self._engine) as db, db.begin():
            record = _required_change_set(db, change_set_id)
            if record.created_by != actor:
                raise ValueError("only the change-set author may submit it for review")
            if record.status != ChangeSetState.DRAFT.value:
                raise ValueError("only a draft change set may be submitted")
            record.status = ChangeSetState.IN_REVIEW.value
            record.submitted_at = utc_now()
        submitted = await self.get_change_set(change_set_id)
        assert submitted is not None
        return submitted

    async def record_review(
        self, change_set_id: str, *, reviewer: str, approved: bool, comment: str = ""
    ) -> OntologyChangeSet:
        """Record one accountable decision; self-approval is rejected."""

        with Session(self._engine) as db, db.begin():
            record = _required_change_set(db, change_set_id)
            if record.created_by == reviewer:
                raise ValueError("change-set author cannot approve their own proposal")
            if record.status != ChangeSetState.IN_REVIEW.value:
                raise ValueError("only an in-review change set may be decided")
            db.add(OntologyChangeSetReviewRecord(
                change_set_id=change_set_id, reviewer=reviewer,
                decision="approved" if approved else "rejected", comment=comment,
            ))
            record.status = (ChangeSetState.APPROVED if approved else ChangeSetState.REJECTED).value
            if approved:
                record.approved_at = utc_now()
        reviewed = await self.get_change_set(change_set_id)
        assert reviewed is not None
        return reviewed

    async def publish_change_set(
        self, change_set_id: str, *, publisher: str, published_at: datetime | None = None
    ) -> OntologySnapshot:
        """Atomically turn an approved proposal into the immutable source of truth."""

        proposal = await self.get_change_set(change_set_id)
        if proposal is None:
            raise ValueError("ontology change set does not exist")
        if proposal.state is not ChangeSetState.APPROVED:
            raise ValueError("only an approved change set may be published")
        snapshot = OntologySnapshot(
            version=proposal.target_version, published_at=published_at or utc_now(),
            assets=tuple(asset.model_copy(update={"state": PublicationState.PUBLISHED})
                         for asset in proposal.assets),
            relations=proposal.relations,
            runtime_bindings=proposal.runtime_bindings,
        )
        _validate_published_snapshot(snapshot)
        with Session(self._engine) as db, db.begin():
            record = _required_change_set(db, change_set_id)
            if record.status != ChangeSetState.APPROVED.value:
                raise ValueError("only an approved change set may be published")
            _persist_snapshot(db, snapshot, published_by=publisher)
            record.status = ChangeSetState.PUBLISHED.value
            record.published_at = snapshot.published_at
        return snapshot

    async def get_published_snapshot(
        self, version: str | None = None
    ) -> OntologySnapshot | None:
        with Session(self._engine) as db:
            publication = (
                db.get(OntologyPublicationRecord, version)
                if version is not None
                else db.scalar(
                    select(OntologyPublicationRecord).order_by(
                        OntologyPublicationRecord.published_at.desc(),
                        OntologyPublicationRecord.version.desc(),
                    )
                )
            )
            if publication is None:
                return None
            assets = tuple(
                _asset_from_record(item)
                for item in db.scalars(
                    select(OntologyAssetRecord).where(
                        OntologyAssetRecord.version == publication.version
                    ).order_by(OntologyAssetRecord.asset_id)
                )
            )
            relations = tuple(
                OntologyRelation(
                    source_id=item.source_id,
                    relation=item.relation,
                    target_id=item.target_id,
                    version=item.version,
                )
                for item in db.scalars(
                    select(OntologyRelationRecord).where(
                        OntologyRelationRecord.version == publication.version
                    ).order_by(
                        OntologyRelationRecord.source_id,
                        OntologyRelationRecord.relation,
                        OntologyRelationRecord.target_id,
                    )
                )
            )
            return OntologySnapshot(
                version=publication.version,
                published_at=_utc(publication.published_at),
                assets=assets,
                relations=relations,
                runtime_bindings=_runtime_bindings(publication.runtime_bindings_json),
            )

    async def find_assets_by_alias(
        self, alias: str, *, kind: OntologyAssetKind | None = None,
        version: str | None = None,
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


def _asset_from_record(record: OntologyAssetRecord) -> OntologyAsset:
    aliases = _aliases(record.aliases_json)
    return OntologyAsset(
        id=record.asset_id,
        kind=OntologyAssetKind(record.kind),
        name=record.name,
        version=record.version,
        state=PublicationState(record.state),
        aliases=aliases,
        description=record.description,
        strategy_definition=_strategy_definition(record.strategy_definition_json),
    )


def _snapshot_checksum(snapshot: OntologySnapshot) -> str:
    payload = {
        "assets": [asset.model_dump(mode="json") for asset in snapshot.assets],
        "relations": [relation.model_dump(mode="json") for relation in snapshot.relations],
        "runtime_bindings": [binding.model_dump(mode="json") for binding in snapshot.runtime_bindings],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _persist_snapshot(
    db: Session, snapshot: OntologySnapshot, *, published_by: str | None
) -> None:
    checksum = _snapshot_checksum(snapshot)
    if db.get(OntologyPublicationRecord, snapshot.version) is not None:
        raise ValueError(f"ontology version already published: {snapshot.version}")
    if db.scalar(select(OntologyPublicationRecord.version).where(
        OntologyPublicationRecord.checksum == checksum
    )) is not None:
        raise ValueError("an identical ontology snapshot is already published")
    db.add(OntologyPublicationRecord(
        version=snapshot.version, published_at=snapshot.published_at,
        checksum=checksum, published_by=published_by,
        runtime_bindings_json=_runtime_bindings_json(snapshot.runtime_bindings),
    ))
    # As above, make the immutable snapshot header visible before its normalized
    # asset/relation children are inserted under PostgreSQL foreign keys.
    db.flush()
    db.add_all(OntologyAssetRecord(
        version=asset.version, asset_id=asset.id, kind=asset.kind.value,
        name=asset.name, state=asset.state.value,
        aliases_json=json.dumps(list(asset.aliases), ensure_ascii=False),
        description=asset.description,
        strategy_definition_json=_strategy_definition_json(asset.strategy_definition),
    ) for asset in snapshot.assets)
    db.add_all(OntologyRelationRecord(
        version=relation.version, source_id=relation.source_id,
        relation=relation.relation, target_id=relation.target_id,
    ) for relation in snapshot.relations)


def _validate_change_set_members(
    target_version: str, assets: tuple[OntologyAsset, ...], relations: tuple[OntologyRelation, ...],
    runtime_bindings: tuple[RuntimeBinding, ...],
) -> None:
    asset_ids = {asset.id for asset in assets}
    if len(asset_ids) != len(assets):
        raise ValueError("ontology asset IDs must be unique within a change set")
    if any(asset.version != target_version for asset in assets):
        raise ValueError("change-set assets must use the target version")
    if any(relation.version != target_version for relation in relations):
        raise ValueError("change-set relations must use the target version")
    if any(edge.source_id not in asset_ids or edge.target_id not in asset_ids for edge in relations):
        raise ValueError("change-set relations must reference change-set assets")
    _validate_runtime_bindings(runtime_bindings, {asset.id: asset for asset in assets})


def _required_change_set(db: Session, change_set_id: str) -> OntologyChangeSetRecord:
    record = db.get(OntologyChangeSetRecord, change_set_id)
    if record is None:
        raise ValueError("ontology change set does not exist")
    return record


def _aliases(value: str) -> tuple[str, ...]:
    try:
        loaded = json.loads(value)
        return tuple(str(item) for item in loaded) if isinstance(loaded, list) else ()
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()


def _strategy_definition_json(value: AnalysisStrategyDefinition | None) -> str:
    return json.dumps(
        value.model_dump(mode="json") if value is not None else None,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _strategy_definition(value: str | None) -> AnalysisStrategyDefinition | None:
    if not value:
        return None
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return AnalysisStrategyDefinition.model_validate(loaded) if loaded is not None else None


def _runtime_bindings_json(value: tuple[RuntimeBinding, ...]) -> str:
    return json.dumps(
        [item.model_dump(mode="json") for item in value], ensure_ascii=False,
        sort_keys=True, separators=(",", ":"),
    )


def _runtime_bindings(value: str | None) -> tuple[RuntimeBinding, ...]:
    try:
        loaded = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    return tuple(RuntimeBinding.model_validate(item) for item in loaded) if isinstance(loaded, list) else ()


def _utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)

"""Configuration and rollback snapshots written inside the caller's transaction."""

from collections.abc import Callable
from typing import Any, cast
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.owned import (
    Document,
    DocumentParserConfig,
    Knowledge,
    KnowledgeParserConfig,
    KnowledgeShare,
)
from ..parser_configuration import DocumentConfiguration, KnowledgeConfiguration, Scope, to_legacy
from ..parser_configuration.conversion import validate_values
from ..parser_configuration.entities import Configuration, ConfigurationError
from ..parser_configuration.fields import fields_for
from ..parser_configuration.legacy import document_configuration_from_legacy

KnowledgeChange = Callable[[KnowledgeConfiguration | None], KnowledgeConfiguration]
DocumentChange = Callable[[DocumentConfiguration | None], DocumentConfiguration]


def _hide_configuration_values(error: Exception) -> None:
    """Keep SQL error classification, but never propagate credentials in loggable text."""
    if isinstance(error, StatementError):
        error.statement = None
        error.params = None
        error.args = ("Parser configuration persistence failed",)
        error.detail.clear()
        # Keep orig for SQLSTATE/retry classification, not as a rendered exception chain.
        error.__cause__ = None
        error.__context__ = None
        error.__suppress_context__ = True


def knowledge_configuration(owner: Knowledge) -> KnowledgeConfiguration:
    return cast(KnowledgeConfiguration, _loaded_configuration(owner, Scope.KNOWLEDGE))


def document_configuration(owner: Document) -> DocumentConfiguration:
    return cast(DocumentConfiguration, _loaded_configuration(owner, Scope.DOCUMENT))


def _loaded_configuration(owner: Any, scope: Scope) -> Configuration:
    row = owner.configuration
    if row is None:
        raise ConfigurationError("configuration_not_migrated")
    values = {
        spec.column: getattr(owner if spec.owner else row, spec.column)
        for spec in fields_for(scope)
    }
    values["extras"] = row.extras
    if scope == Scope.KNOWLEDGE:
        values.update(knowledge_type=owner.type, source=owner.source,
                      index_strategy=owner.index_strategy)
    return validate_values(values, scope)


class ParserConfigurationRepository:
    def __init__(self, session: Session):
        self.session = session

    def _knowledge(self, owner_id: UUID, lock: bool = False) -> Knowledge:
        statement = select(Knowledge).where(Knowledge.id == owner_id)
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        owner = self.session.execute(statement).scalar_one_or_none()
        if owner is None:
            raise ConfigurationError("knowledge_not_found")
        return owner

    def _read(self, owner_id: UUID, scope: Scope, owner: Knowledge | None = None) -> Any:
        table = (
            KnowledgeParserConfig if scope == Scope.KNOWLEDGE else DocumentParserConfig
        ).__table__
        key = "knowledge_id" if scope == Scope.KNOWLEDGE else "document_id"
        row = (
            self.session.execute(select(table).where(table.c[key] == owner_id))
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        values = {spec.column: row[spec.column] for spec in fields_for(scope) if not spec.owner}
        values["extras"] = row["extras"]
        if scope == Scope.KNOWLEDGE:
            assert owner is not None
            values.update(
                knowledge_type=owner.type, source=owner.source, index_strategy=owner.index_strategy
            )
            values.update(
                {
                    spec.column: getattr(owner, spec.column)
                    for spec in fields_for(scope)
                    if spec.owner
                }
            )
        return validate_values(values, scope)

    def get_knowledge(self, owner_id: UUID) -> KnowledgeConfiguration | None:
        return self._read(owner_id, Scope.KNOWLEDGE, self._knowledge(owner_id))

    def get_effective_knowledge(self, owner_id: UUID) -> KnowledgeConfiguration | None:
        owner = self._knowledge(owner_id)
        if owner.permission_id == "Share":
            source_id = self.session.execute(
                select(KnowledgeShare.source_kb_id).where(KnowledgeShare.target_kb_id == owner_id)
            ).scalar_one_or_none()
            if source_id is None:
                raise ConfigurationError("shared_source_not_found")
            return self.get_knowledge(source_id)
        return self._read(owner_id, Scope.KNOWLEDGE, owner)

    def get_document(self, owner_id: UUID) -> DocumentConfiguration | None:
        return self._read(owner_id, Scope.DOCUMENT)

    @staticmethod
    def _set_knowledge_fields(owner: Knowledge, config: KnowledgeConfiguration) -> None:
        if owner.type is not None and owner.type != config.knowledge_type:
            raise ConfigurationError("source_type_mismatch", "type")
        for name, value in {
            "type": config.knowledge_type,
            "source": config.source,
            "index_strategy": config.index_strategy,
        }.items():
            setattr(owner, name, value)
        for spec in fields_for(Scope.KNOWLEDGE):
            if spec.owner:
                setattr(owner, spec.column, getattr(config, spec.column))

    def _write(self, owner: Knowledge | Document, config: Configuration, exists: bool) -> None:
        config = validate_values(config.model_dump(), config.scope)
        table = (
            KnowledgeParserConfig if config.scope == Scope.KNOWLEDGE else DocumentParserConfig
        ).__table__
        key = "knowledge_id" if config.scope == Scope.KNOWLEDGE else "document_id"
        values = {
            spec.column: getattr(config, spec.column)
            for spec in fields_for(config.scope)
            if not spec.owner
        }
        values["extras"] = config.extras
        # Compute the snapshot before writing either representation.
        snapshot = to_legacy(config, include_extras=True)
        if exists:
            self.session.execute(table.update().where(table.c[key] == owner.id).values(**values))
        else:
            self.session.execute(table.insert().values(**{key: owner.id}, **values))
        cast(Any, owner).legacy_parser_config_snapshot = snapshot
        self.session.flush()
        self.session.expire(owner, ["configuration"])
        self.session.refresh(owner, ["configuration"])

    def write_knowledge(
        self, owner: Knowledge | UUID, change: KnowledgeChange
    ) -> KnowledgeConfiguration:
        try:
            with self.session.no_autoflush:
                new = isinstance(owner, Knowledge) and (
                    inspect(owner).transient or inspect(owner).pending
                )
                if new:
                    assert isinstance(owner, Knowledge)
                    config = change(None)
                    config = KnowledgeConfiguration.model_validate(config.model_dump())
                    self._set_knowledge_fields(owner, config)
                    self.session.add(owner)
                    self.session.flush([owner])
                owner_id = cast(UUID, owner.id) if isinstance(owner, Knowledge) else owner
                locked = self._knowledge(owner_id, lock=True)
                current = self._read(owner_id, Scope.KNOWLEDGE, locked)
                source_before = {
                    spec.column: getattr(current, spec.column)
                    for spec in fields_for(Scope.KNOWLEDGE)
                    if current is not None and spec.api_path.startswith("sources.")
                }
                immutable = (
                    {
                        name: getattr(current, name)
                        for name in (
                            "knowledge_type",
                            "source",
                            "index_strategy",
                            "third_party_platform",
                        )
                    }
                    if current is not None
                    else {}
                )
                if not new:
                    config = change(current)
                for name, original in immutable.items():
                    if getattr(config, name) != original:
                        if name == "third_party_platform" and original is None:
                            config.require_complete_source()
                            continue
                        raise ConfigurationError("immutable_field", name)
                if any(getattr(config, name) != value for name, value in source_before.items()):
                    config.require_complete_source()
                self._set_knowledge_fields(locked, config)
                self._write(locked, config, current is not None)
                return config
        except Exception as exc:
            _hide_configuration_values(exc)
            self.session.rollback()
            raise

    def write_document(
        self, knowledge_id: UUID, owner: Document | UUID, change: DocumentChange
    ) -> DocumentConfiguration:
        try:
            with self.session.no_autoflush:
                self._knowledge(knowledge_id, lock=True)
                if isinstance(owner, Document) and (
                    inspect(owner).transient or inspect(owner).pending
                ):
                    if owner.kb_id != knowledge_id:
                        raise ConfigurationError("document_knowledge_mismatch")
                    self.session.add(owner)
                    self.session.flush([owner])
                owner_id = cast(UUID, owner.id) if isinstance(owner, Document) else owner
                locked = self.session.execute(
                    select(Document)
                    .where(Document.id == owner_id, Document.kb_id == knowledge_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).scalar_one_or_none()
                if locked is None:
                    raise ConfigurationError("document_not_found")
                current = self._read(owner_id, Scope.DOCUMENT)
                config = change(current)
                self._write(locked, config, current is not None)
                return config
        except Exception as exc:
            _hide_configuration_values(exc)
            self.session.rollback()
            raise

    def write_legacy_document(
        self, knowledge_id: UUID, owner: Document | UUID, data: dict[str, Any],
    ) -> DocumentConfiguration:
        def change(current: DocumentConfiguration | None) -> DocumentConfiguration:
            knowledge = self.get_knowledge(knowledge_id)
            if knowledge is None:
                raise ConfigurationError("configuration_not_migrated")
            return document_configuration_from_legacy(data, knowledge, current=current)

        return self.write_document(knowledge_id, owner, change)


class AsyncParserConfigurationRepository:
    """SQLAlchemy's async bridge keeps exactly the same transaction and lock ordering."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_knowledge(self, owner_id: UUID) -> KnowledgeConfiguration | None:
        return await self.session.run_sync(
            lambda session: ParserConfigurationRepository(session).get_knowledge(owner_id)
        )

    async def get_effective_knowledge(self, owner_id: UUID) -> KnowledgeConfiguration | None:
        return await self.session.run_sync(
            lambda session: ParserConfigurationRepository(session).get_effective_knowledge(owner_id)
        )

    async def get_document(self, owner_id: UUID) -> DocumentConfiguration | None:
        return await self.session.run_sync(
            lambda session: ParserConfigurationRepository(session).get_document(owner_id)
        )

    async def write_knowledge(
        self, owner: Knowledge | UUID, change: KnowledgeChange
    ) -> KnowledgeConfiguration:
        return await self.session.run_sync(
            lambda session: ParserConfigurationRepository(session).write_knowledge(owner, change)
        )

    async def write_document(
        self, knowledge_id: UUID, owner: Document | UUID, change: DocumentChange
    ) -> DocumentConfiguration:
        return await self.session.run_sync(
            lambda session: ParserConfigurationRepository(session).write_document(
                knowledge_id, owner, change
            )
        )

    async def write_legacy_document(
        self, knowledge_id: UUID, owner: Document | UUID, data: dict[str, Any],
    ) -> DocumentConfiguration:
        return await self.session.run_sync(
            lambda session: ParserConfigurationRepository(session).write_legacy_document(
                knowledge_id, owner, data
            )
        )

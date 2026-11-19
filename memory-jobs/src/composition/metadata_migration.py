"""Wiring for the one-time metadata migration task domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from task_bridge.metadata_migration import MIGRATION_CUTOFF

from src.application.metadata_migration import (
    MetadataMigrationUseCase,
    ScanMetadataMigrationUseCase,
)
from src.application.ports.llm import ChatModelFactory
from src.application.ports.prompts import PromptCatalog
from src.application.ports.publishing import UnitPublisher
from src.composition.shared import SharedDependencies
from src.infrastructure.lock.metadata_migration_circuit_breaker import (
    MetadataMigrationCircuitBreaker,
)
from src.infrastructure.lock.metadata_write_lock import MetadataWriteLock
from src.infrastructure.persistence.postgres.uow import SqlAlchemyUnitOfWork
from src.runtime import JobsRuntime


@dataclass(frozen=True, slots=True)
class MetadataMigrationWiring:
    uow: SqlAlchemyUnitOfWork
    chat_models: ChatModelFactory
    prompts: PromptCatalog
    lock: MetadataWriteLock
    circuit_breaker: MetadataMigrationCircuitBreaker
    publisher: UnitPublisher
    page_size: int
    cutoff: datetime
    enabled: bool

    def scan(self) -> ScanMetadataMigrationUseCase:
        return ScanMetadataMigrationUseCase(
            uow=self.uow,
            lock=self.lock,
            circuit_breaker=self.circuit_breaker,
            publisher=self.publisher,
            page_size=self.page_size,
            cutoff=self.cutoff,
            enabled=self.enabled,
        )

    def do(self) -> MetadataMigrationUseCase:
        return MetadataMigrationUseCase(
            uow=self.uow,
            chat_models=self.chat_models,
            prompts=self.prompts,
            lock=self.lock,
            circuit_breaker=self.circuit_breaker,
            enabled=self.enabled,
        )


def build_metadata_migration_wiring(
    runtime: JobsRuntime, shared: SharedDependencies
) -> MetadataMigrationWiring:
    return MetadataMigrationWiring(
        uow=shared.unit_of_work,
        chat_models=shared.chat_models,
        prompts=shared.prompts,
        lock=MetadataWriteLock(lambda: runtime.memory_write_redis.client()),
        circuit_breaker=MetadataMigrationCircuitBreaker(
            lambda: runtime.redis.client(),
            ttl_seconds=shared.settings.metadata_migration_circuit_breaker_seconds,
        ),
        publisher=shared.publisher,
        page_size=shared.settings.metadata_migration_page_size,
        cutoff=MIGRATION_CUTOFF,
        enabled=shared.settings.metadata_migration_enabled,
    )

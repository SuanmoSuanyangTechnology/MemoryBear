"""Storage-outbox domain wiring: the projector and the retention cleaner.

The domain owns both adapters. The store leases rows through the business
PostgreSQL pool (the same database the API producer writes); the projector reads
the authoritative node from the shared Neo4j client and writes the Elasticsearch
document. Only settings and the process runtime are shared with other domains.

The two use cases are built lazily from this facade, so nothing here connects:
the store and the projector hold lazy clients/pool references until their first
call. The projector's ``warm_up`` (index checks) runs inside the first consume
batch, never while the container is being assembled.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.application.outbox.cleanup import CleanupOutboxUseCase
from src.application.outbox.consume import (
    MAX_HEARTBEAT_INTERVAL_SECONDS,
    ConsumeOutboxBatchUseCase,
)
from src.application.ports.outbox.outbox import ConsumeOptions
from src.composition.shared import SharedDependencies
from src.config import JobsSettings
from src.infrastructure.outbox.projection import MemoryProjection
from src.infrastructure.outbox.repository import PostgresOutboxStore
from src.runtime import JobsRuntime

#: Cap of the API's ``asyncio.timeout`` per attempt. This worker cannot enforce a
#: synchronous deadline; the value is kept for config symmetry and for the batch
#: log, and the real wall-clock bound is the ES/Neo4j client timeout.
MAX_ATTEMPT_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class OutboxDependencies:
    """Everything the outbox use cases need, including this module's adapters."""

    settings: JobsSettings
    store: PostgresOutboxStore
    projector: MemoryProjection


def build_outbox_dependencies(
    runtime: JobsRuntime, shared: SharedDependencies
) -> OutboxDependencies:
    """Assemble this domain's graph from shared services and runtime clients."""

    settings = shared.settings
    return OutboxDependencies(
        settings=settings,
        store=PostgresOutboxStore(
            runtime.postgres.session_factory(),
            processing_timeout=settings.outbox_processing_timeout_seconds,
            retention_days=settings.outbox_retention_days,
            failed_retention_days=settings.outbox_failed_retention_days,
            error_max_length=settings.outbox_error_max_length,
        ),
        projector=MemoryProjection(
            elasticsearch=runtime.elasticsearch,
            neo4j=runtime.neo4j,
            settings=settings,
        ),
    )


class OutboxWiring:
    """Build this domain's use cases from its dependencies."""

    def __init__(self, dependencies: OutboxDependencies) -> None:
        self._dependencies = dependencies

    def projection(self) -> ConsumeOutboxBatchUseCase:
        """One bounded projection batch; the heartbeat cadence is store-derived."""

        settings = self._dependencies.settings
        timeout = settings.outbox_processing_timeout_seconds
        return ConsumeOutboxBatchUseCase(
            store=self._dependencies.store,
            projector=self._dependencies.projector,
            options=ConsumeOptions(
                batch_size=settings.outbox_batch_size,
                # Not enforced by the synchronous loop (see the use case's
                # docstring); recorded for the batch log and config symmetry.
                attempt_timeout_seconds=min(MAX_ATTEMPT_TIMEOUT_SECONDS, timeout / 2),
                heartbeat_interval_seconds=min(MAX_HEARTBEAT_INTERVAL_SECONDS, timeout / 3),
            ),
        )

    def cleanup(self) -> CleanupOutboxUseCase:
        return CleanupOutboxUseCase(
            store=self._dependencies.store,
            batch_size=self._dependencies.settings.outbox_batch_size,
        )


def build_outbox_wiring(
    runtime: JobsRuntime, shared: SharedDependencies
) -> OutboxWiring:
    """Build the domain facade from its own adapters and the shared services."""

    return OutboxWiring(build_outbox_dependencies(runtime, shared))


__all__ = [
    "MAX_ATTEMPT_TIMEOUT_SECONDS",
    "MAX_HEARTBEAT_INTERVAL_SECONDS",
    "OutboxDependencies",
    "OutboxWiring",
    "build_outbox_dependencies",
    "build_outbox_wiring",
]
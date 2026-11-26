"""Dispatch-admission domain wiring: one facade for its two Celery tasks.

The domain owns both of its adapters. The store reads the admission keyspace
from the business Redis client (the same database the producer writes) and work
task results from a second client pointed at the Celery result backend; the
dispatcher publishes through ``current_app``. Only the settings and the process
runtime are shared with other domains.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.application.admission.dispatch import DispatchAdmissionUseCase
from src.application.admission.recover import RecoverAdmissionsUseCase
from src.composition.shared import SharedDependencies
from src.config import JobsSettings
from src.infrastructure.admission.celery_dispatcher import CeleryJobDispatcher
from src.infrastructure.admission.redis_store import RedisAdmissionStore
from src.runtime import JobsRuntime


@dataclass(frozen=True, slots=True)
class AdmissionDependencies:
    """Everything the admission tasks need, including this module's adapters."""

    settings: JobsSettings
    store: RedisAdmissionStore
    dispatcher: CeleryJobDispatcher


def build_admission_dependencies(
    runtime: JobsRuntime, shared: SharedDependencies
) -> AdmissionDependencies:
    """Assemble this domain's graph from shared services and runtime clients."""

    return AdmissionDependencies(
        settings=shared.settings,
        store=RedisAdmissionStore(
            client_provider=lambda: runtime.redis.client(),
            result_client_provider=lambda: runtime.result_backend_redis.client(),
            heartbeat_ttl_seconds=shared.settings.admission_heartbeat_ttl_seconds,
        ),
        dispatcher=CeleryJobDispatcher(),
    )


class AdmissionWiring:
    """Build this domain's use cases from its dependencies."""

    def __init__(self, dependencies: AdmissionDependencies) -> None:
        self._dependencies = dependencies

    def dispatch(self) -> DispatchAdmissionUseCase:
        return DispatchAdmissionUseCase(
            store=self._dependencies.store,
            dispatcher=self._dependencies.dispatcher,
        )

    def recover(self) -> RecoverAdmissionsUseCase:
        settings = self._dependencies.settings
        return RecoverAdmissionsUseCase(
            store=self._dependencies.store,
            dispatcher=self._dependencies.dispatcher,
            attempt_timeout_seconds=settings.admission_attempt_timeout_seconds,
            pending_batch=settings.admission_pending_batch,
            scan_units=settings.admission_scan_units,
            ready_batch=settings.admission_ready_batch,
        )


def build_admission_wiring(
    runtime: JobsRuntime, shared: SharedDependencies
) -> AdmissionWiring:
    """Build the domain facade from its own adapters and the shared services."""

    return AdmissionWiring(build_admission_dependencies(runtime, shared))
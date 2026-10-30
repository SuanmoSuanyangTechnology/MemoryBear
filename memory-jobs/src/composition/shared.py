"""Shared dependencies: built once per process and handed to domain wiring.

Every adapter here is stateless or holds only a lazy client provider, so one
instance is safe to share across the worker's threads. A domain wiring module
must not build its own copy — new domains add a wiring module, not adapters.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.application.ports.publishing import UnitPublisher
from src.application.ports.scene_names import SceneNameLookup
from src.config import JobsSettings
from src.infrastructure.cache.redis_interest_cache import RedisInterestCache
from src.infrastructure.clock.system_clock import SystemClock
from src.infrastructure.llm.model_service_factory import ModelServiceChatModelFactory
from src.infrastructure.lock.redis_inflight_lock import RedisInflightLock
from src.infrastructure.persistence.neo4j.interest_source_reader import (
    Neo4jInterestSourceReader,
)
from src.infrastructure.persistence.neo4j.profile_source_reader import (
    Neo4jProfileSourceReader,
)
from src.infrastructure.persistence.postgres.uow import SqlAlchemyUnitOfWork
from src.infrastructure.prompts.jinja_prompt_catalog import JinjaPromptCatalog
from src.runtime import JobsRuntime


@dataclass(frozen=True, slots=True)
class SharedDependencies:
    """Process-wide settings and adapters every domain wiring may use."""

    settings: JobsSettings
    unit_of_work: SqlAlchemyUnitOfWork
    inflight_lock: RedisInflightLock
    interest_cache: RedisInterestCache
    chat_models: ModelServiceChatModelFactory
    prompts: JinjaPromptCatalog
    clock: SystemClock
    publisher: UnitPublisher
    profile_sources: Neo4jProfileSourceReader
    interest_source: Neo4jInterestSourceReader
    scene_names: SceneNameLookup


def _celery_publisher() -> UnitPublisher:
    # Celery is the delivery mechanism and lives in the interface layer; the
    # import stays local so loading the task package cannot cycle through us.
    from src.interfaces.tasks.publishing import CeleryUnitPublisher

    return CeleryUnitPublisher()


def build_shared_dependencies(runtime: JobsRuntime) -> SharedDependencies:
    """Construct the process-wide graph; connections stay lazy inside clients."""

    return SharedDependencies(
        settings=runtime.settings,
        unit_of_work=SqlAlchemyUnitOfWork(runtime.postgres.session_factory()),
        inflight_lock=RedisInflightLock(lambda: runtime.redis.client()),
        interest_cache=RedisInterestCache(
            lambda: runtime.redis.client(),
            runtime.settings.interest_cache_expire_seconds,
        ),
        chat_models=ModelServiceChatModelFactory(runtime.model_service),
        prompts=JinjaPromptCatalog(),
        clock=SystemClock(),
        publisher=_celery_publisher(),
        profile_sources=Neo4jProfileSourceReader(runtime.neo4j),
        interest_source=Neo4jInterestSourceReader(runtime.neo4j),
        scene_names=runtime.memory_enterprise.scene_names,
    )

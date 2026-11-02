"""Profile-cache domain wiring: one facade for its Celery tasks.

The module owns its adapters. The in-flight lock, the profile source reader and
the optional enterprise scene-name lookup are assembled here from the runtime's
lazy clients; only cross-domain services come from :class:`SharedDependencies`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from src.application.ports.publishing import UnitPublisher
from src.application.ports.scene_names import SceneNameLookup
from src.application.profile_cache.fanout import FanOutWorkspaceUseCase
from src.application.profile_cache.generate import GenerateProfileCacheUseCase
from src.application.profile_cache.scan import ScanProfileCachesUseCase
from src.composition.shared import SharedDependencies
from src.config import JobsSettings
from src.domain.profile.policies import CacheFreshnessPolicy
from src.infrastructure.clock.system_clock import SystemClock
from src.infrastructure.llm.model_service_factory import ModelServiceChatModelFactory
from src.infrastructure.lock.redis_inflight_lock import RedisInflightLock
from src.infrastructure.memory_enterprise_scene_names import (
    MemoryEnterpriseSceneNames,
)
from src.infrastructure.persistence.neo4j.profile_source_reader import (
    Neo4jProfileSourceReader,
)
from src.infrastructure.persistence.postgres.uow import SqlAlchemyUnitOfWork
from src.infrastructure.prompts.jinja_prompt_catalog import JinjaPromptCatalog
from src.runtime import JobsRuntime

#: The sweep has always used Chinese; keep it until a per-user preference exists.
SCAN_LANGUAGE = "zh"


@dataclass(frozen=True, slots=True)
class ProfileCacheDependencies:
    """Everything the profile-cache tasks need, including this module's adapters."""

    settings: JobsSettings
    unit_of_work: SqlAlchemyUnitOfWork
    inflight_lock: RedisInflightLock
    chat_models: ModelServiceChatModelFactory
    prompts: JinjaPromptCatalog
    clock: SystemClock
    publisher: UnitPublisher
    profile_sources: Neo4jProfileSourceReader
    #: ``{}`` when no enterprise service is configured (the open build);
    #: ``None`` when it is configured but currently unavailable.
    scene_names: SceneNameLookup


def build_profile_cache_dependencies(
    runtime: JobsRuntime, shared: SharedDependencies
) -> ProfileCacheDependencies:
    """Assemble this domain's graph from shared services and runtime clients."""

    return ProfileCacheDependencies(
        settings=shared.settings,
        unit_of_work=shared.unit_of_work,
        inflight_lock=RedisInflightLock(lambda: runtime.redis.client()),
        chat_models=shared.chat_models,
        prompts=shared.prompts,
        clock=SystemClock(),
        publisher=shared.publisher,
        profile_sources=Neo4jProfileSourceReader(runtime.neo4j),
        scene_names=MemoryEnterpriseSceneNames(runtime.memory_enterprise),
    )


class ProfileCacheWiring:
    """Build this domain's use cases from its dependencies."""

    def __init__(self, dependencies: ProfileCacheDependencies) -> None:
        self._dependencies = dependencies

    def generate(self) -> GenerateProfileCacheUseCase:
        sources = self._dependencies.profile_sources
        return GenerateProfileCacheUseCase(
            uow=self._dependencies.unit_of_work,
            insight_statements=sources,
            summary_sources=sources,
            chat_models=self._dependencies.chat_models,
            prompts=self._dependencies.prompts,
            clock=self._dependencies.clock,
            lock=self._dependencies.inflight_lock,
            scene_names=self._dependencies.scene_names,
        )

    def scan(self, language: str = SCAN_LANGUAGE) -> ScanProfileCachesUseCase:
        return ScanProfileCachesUseCase(
            uow=self._dependencies.unit_of_work,
            policy=CacheFreshnessPolicy(
                timedelta(hours=self._dependencies.settings.memory_cache_fresh_hours)
            ),
            lock=self._dependencies.inflight_lock,
            publisher=self._dependencies.publisher,
            language=language,
            page_size=self._dependencies.settings.scan_page_size,
        )

    def fan_out(self, language: str) -> FanOutWorkspaceUseCase:
        return FanOutWorkspaceUseCase(
            uow=self._dependencies.unit_of_work,
            publisher=self._dependencies.publisher,
            language=language,
        )


def build_profile_cache_wiring(
    runtime: JobsRuntime, shared: SharedDependencies
) -> ProfileCacheWiring:
    """Build the domain facade from its own adapters and the shared services."""

    return ProfileCacheWiring(build_profile_cache_dependencies(runtime, shared))

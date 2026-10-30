"""Process composition: runtime -> adapters -> use cases.

Tasks ask the container for a use case; they never assemble a Unit of Work,
a Neo4j reader or a Redis adapter themselves. The container is cheap to build
(adapters are lazy about connections) and a process-wide singleton by default;
tests can replace it with :func:`set_container`.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import timedelta

from src.application.interest.generate import GenerateInterestDistributionUseCase
from src.application.interest.prewarm import PrewarmInterestDistributionsUseCase
from src.application.profile_cache.fanout import FanOutWorkspaceUseCase
from src.application.profile_cache.generate import GenerateProfileCacheUseCase
from src.application.profile_cache.scan import ScanProfileCachesUseCase
from src.domain.profile.policies import CacheFreshnessPolicy
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
from src.runtime import JobsRuntime, get_runtime

#: The sweep has always used Chinese; keep it until a per-user preference exists.
SCAN_LANGUAGE = "zh"


@dataclass(slots=True)
class Container:
    runtime: JobsRuntime

    # ------------------------------------------------------------- adapters

    def unit_of_work(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.runtime.postgres.session_factory())

    def inflight_lock(self) -> RedisInflightLock:
        return RedisInflightLock(lambda: self.runtime.redis.client())

    def interest_cache(self) -> RedisInterestCache:
        return RedisInterestCache(
            lambda: self.runtime.redis.client(),
            self.runtime.settings.interest_cache_expire_seconds,
        )

    def _chat_models(self) -> ModelServiceChatModelFactory:
        return ModelServiceChatModelFactory(self.runtime.model_service)

    def _prompts(self) -> JinjaPromptCatalog:
        return JinjaPromptCatalog()

    def _profile_sources(self) -> Neo4jProfileSourceReader:
        return Neo4jProfileSourceReader(self.runtime.neo4j)

    def _interest_source(self) -> Neo4jInterestSourceReader:
        return Neo4jInterestSourceReader(self.runtime.neo4j)

    def _publisher(self):
        # Celery is the delivery mechanism and lives in the interface layer; the
        # import stays local so loading the task package cannot cycle through us.
        from src.interfaces.tasks.publishing import CeleryUnitPublisher

        return CeleryUnitPublisher()

    # -------------------------------------------------------------- use cases

    def generate_profile_cache(self) -> GenerateProfileCacheUseCase:
        sources = self._profile_sources()
        return GenerateProfileCacheUseCase(
            uow=self.unit_of_work(),
            insight_statements=sources,
            summary_sources=sources,
            chat_models=self._chat_models(),
            prompts=self._prompts(),
            clock=SystemClock(),
            scene_names=self.runtime.memory_enterprise.scene_names,
        )

    def scan_profile_caches(self, language: str = SCAN_LANGUAGE) -> ScanProfileCachesUseCase:
        return ScanProfileCachesUseCase(
            uow=self.unit_of_work(),
            policy=CacheFreshnessPolicy(
                timedelta(hours=self.runtime.settings.memory_cache_fresh_hours)
            ),
            lock=self.inflight_lock(),
            publisher=self._publisher(),
            language=language,
        )

    def fan_out_workspace(self, language: str) -> FanOutWorkspaceUseCase:
        return FanOutWorkspaceUseCase(
            uow=self.unit_of_work(),
            publisher=self._publisher(),
            language=language,
        )

    def generate_interest_distribution(self) -> GenerateInterestDistributionUseCase:
        return GenerateInterestDistributionUseCase(
            uow=self.unit_of_work(),
            source=self._interest_source(),
            chat_models=self._chat_models(),
            prompts=self._prompts(),
        )

    def prewarm_interest_distributions(
        self, language: str, limit: int
    ) -> PrewarmInterestDistributionsUseCase:
        return PrewarmInterestDistributionsUseCase(
            uow=self.unit_of_work(),
            cache=self.interest_cache(),
            publisher=self._publisher(),
            language=language,
            limit=limit,
        )


_container: Container | None = None
_container_lock = threading.Lock()


def get_container() -> Container:
    """Return the process-wide container, creating it on first use."""

    global _container
    if _container is None:
        with _container_lock:
            if _container is None:
                _container = Container(get_runtime())
    return _container


def set_container(container: Container | None) -> None:
    """Replace the process container (tests and startup wiring)."""

    global _container
    with _container_lock:
        _container = container

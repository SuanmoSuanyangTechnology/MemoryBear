"""Interest-distribution domain wiring: one facade for its Celery tasks.

The module owns its adapters. The interest cache and the interest source reader
are assembled here from the runtime's lazy clients; only cross-domain services
come from :class:`SharedDependencies`.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.application.interest.generate import GenerateInterestDistributionUseCase
from src.application.interest.prewarm import PrewarmInterestDistributionsUseCase
from src.application.ports.publishing import UnitPublisher
from src.composition.shared import SharedDependencies
from src.infrastructure.cache.redis_interest_cache import RedisInterestCache
from src.infrastructure.llm.model_service_factory import ModelServiceChatModelFactory
from src.infrastructure.persistence.neo4j.interest_source_reader import (
    Neo4jInterestSourceReader,
)
from src.infrastructure.persistence.postgres.uow import SqlAlchemyUnitOfWork
from src.infrastructure.prompts.jinja_prompt_catalog import JinjaPromptCatalog
from src.runtime import JobsRuntime


@dataclass(frozen=True, slots=True)
class InterestDependencies:
    """Everything the interest-distribution tasks need, including its adapters."""

    unit_of_work: SqlAlchemyUnitOfWork
    chat_models: ModelServiceChatModelFactory
    prompts: JinjaPromptCatalog
    publisher: UnitPublisher
    interest_cache: RedisInterestCache
    interest_source: Neo4jInterestSourceReader


def build_interest_dependencies(
    runtime: JobsRuntime, shared: SharedDependencies
) -> InterestDependencies:
    """Assemble this domain's graph from shared services and runtime clients."""

    return InterestDependencies(
        unit_of_work=shared.unit_of_work,
        chat_models=shared.chat_models,
        prompts=shared.prompts,
        publisher=shared.publisher,
        interest_cache=RedisInterestCache(
            lambda: runtime.redis.client(),
            runtime.settings.interest_cache_expire_seconds,
        ),
        interest_source=Neo4jInterestSourceReader(runtime.neo4j),
    )


class InterestWiring:
    """Build this domain's use cases from its dependencies."""

    def __init__(self, dependencies: InterestDependencies) -> None:
        self._dependencies = dependencies

    def generate(self) -> GenerateInterestDistributionUseCase:
        return GenerateInterestDistributionUseCase(
            uow=self._dependencies.unit_of_work,
            source=self._dependencies.interest_source,
            chat_models=self._dependencies.chat_models,
            prompts=self._dependencies.prompts,
            cache=self._dependencies.interest_cache,
        )

    def prewarm(self, language: str, limit: int) -> PrewarmInterestDistributionsUseCase:
        return PrewarmInterestDistributionsUseCase(
            uow=self._dependencies.unit_of_work,
            cache=self._dependencies.interest_cache,
            publisher=self._dependencies.publisher,
            language=language,
            limit=limit,
        )


def build_interest_wiring(runtime: JobsRuntime, shared: SharedDependencies) -> InterestWiring:
    """Build the domain facade from its own adapters and the shared services."""

    return InterestWiring(build_interest_dependencies(runtime, shared))

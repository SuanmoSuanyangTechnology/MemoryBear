"""Interest-distribution domain wiring: one facade for its Celery tasks."""

from __future__ import annotations

from src.application.interest.generate import GenerateInterestDistributionUseCase
from src.application.interest.prewarm import PrewarmInterestDistributionsUseCase
from src.composition.shared import SharedDependencies


class InterestWiring:
    """Build this domain's use cases from the shared dependencies."""

    def __init__(self, dependencies: SharedDependencies) -> None:
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

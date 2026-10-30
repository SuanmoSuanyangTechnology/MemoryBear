"""Profile-cache domain wiring: one facade for its Celery tasks."""

from __future__ import annotations

from datetime import timedelta

from src.application.profile_cache.fanout import FanOutWorkspaceUseCase
from src.application.profile_cache.generate import GenerateProfileCacheUseCase
from src.application.profile_cache.scan import ScanProfileCachesUseCase
from src.composition.shared import SharedDependencies
from src.domain.profile.policies import CacheFreshnessPolicy

#: The sweep has always used Chinese; keep it until a per-user preference exists.
SCAN_LANGUAGE = "zh"


class ProfileCacheWiring:
    """Build this domain's use cases from the shared dependencies."""

    def __init__(self, dependencies: SharedDependencies) -> None:
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
                timedelta(
                    hours=self._dependencies.settings.memory_cache_fresh_hours
                )
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

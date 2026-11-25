"""Rag Profile wiring: domain adapters plus shared services."""

from dataclasses import dataclass

from src.application.rag_profile.generate import GenerateRAGProfileUseCase
from src.composition.shared import SharedDependencies
from src.infrastructure.persistence.elasticsearch.rag_profile_source import (
    ElasticsearchRAGProfileSource,
)
from src.runtime import JobsRuntime


@dataclass(frozen=True, slots=True)
class RAGProfileWiring:
    shared: SharedDependencies
    source: ElasticsearchRAGProfileSource

    def generate(self) -> GenerateRAGProfileUseCase:
        return GenerateRAGProfileUseCase(
            self.shared.unit_of_work,
            self.source,
            self.shared.chat_models,
            self.shared.prompts,
            self.shared.task_cache,
        )


def build_rag_profile_wiring(runtime: JobsRuntime, shared: SharedDependencies) -> RAGProfileWiring:
    return RAGProfileWiring(shared, ElasticsearchRAGProfileSource(runtime.elasticsearch))

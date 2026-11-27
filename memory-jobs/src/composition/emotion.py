"""Emotion wiring: domain adapters plus shared services."""

from dataclasses import dataclass

from src.application.emotion.generate import GenerateEmotionSuggestionsUseCase
from src.composition.shared import SharedDependencies
from src.infrastructure.persistence.neo4j.emotion_source import Neo4jEmotionSource
from src.runtime import JobsRuntime


@dataclass(frozen=True, slots=True)
class EmotionWiring:
    shared: SharedDependencies
    source: Neo4jEmotionSource

    def generate(self) -> GenerateEmotionSuggestionsUseCase:
        return GenerateEmotionSuggestionsUseCase(
            self.shared.unit_of_work,
            self.source,
            self.shared.chat_models,
            self.shared.prompts,
            self.shared.task_cache,
        )


def build_emotion_wiring(runtime: JobsRuntime, shared: SharedDependencies) -> EmotionWiring:
    return EmotionWiring(shared, Neo4jEmotionSource(runtime.neo4j))

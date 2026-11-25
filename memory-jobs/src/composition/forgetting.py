"""Forgetting wiring: domain adapters plus shared services."""

from dataclasses import dataclass

from src.application.forgetting.trigger import TriggerForgettingUseCase
from src.composition.shared import SharedDependencies
from src.infrastructure.persistence.neo4j.forgetting_source import Neo4jForgettingSource
from src.runtime import JobsRuntime


@dataclass(frozen=True, slots=True)
class ForgettingWiring:
    shared: SharedDependencies
    source: Neo4jForgettingSource

    def trigger(self) -> TriggerForgettingUseCase:
        return TriggerForgettingUseCase(
            self.shared.unit_of_work,
            self.source,
            self.shared.chat_models,
            self.shared.prompts,
            self.shared.task_cache,
        )


def build_forgetting_wiring(runtime: JobsRuntime, shared: SharedDependencies) -> ForgettingWiring:
    return ForgettingWiring(shared, Neo4jForgettingSource(runtime.neo4j))

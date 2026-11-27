"""Working Memory wiring: domain adapters plus shared services."""

from dataclasses import dataclass

from src.application.working_memory.generate import GenerateConversationDetailUseCase
from src.composition.shared import SharedDependencies
from src.runtime import JobsRuntime


@dataclass(frozen=True, slots=True)
class WorkingMemoryWiring:
    shared: SharedDependencies

    def generate(self) -> GenerateConversationDetailUseCase:
        return GenerateConversationDetailUseCase(
            self.shared.unit_of_work,
            self.shared.chat_models,
            self.shared.prompts,
            self.shared.task_cache,
        )


def build_working_memory_wiring(
    runtime: JobsRuntime, shared: SharedDependencies
) -> WorkingMemoryWiring:
    return WorkingMemoryWiring(shared)

"""Inbound job: validate payload and invoke the context's application use case."""

from collections.abc import Mapping
from typing import Any

from src.application.emotion.dto import EmotionSuggestionsResult, GenerateEmotionSuggestionsCommand
from src.application.emotion.generate import GenerateEmotionSuggestionsUseCase
from src.application.jobs.payload import text
from src.application.jobs.task_scope import task_scope
from src.application.ports.jobs import JobSpec, ResourceClass


class GenerateEmotionSuggestionsJob:
    spec = JobSpec(resource_class=ResourceClass.LLM_BOUND, retryable=False)

    def __init__(self, use_case: GenerateEmotionSuggestionsUseCase) -> None:
        self._use_case = use_case

    def handle(self, payload: Mapping[str, Any], *, attempt_token: str) -> EmotionSuggestionsResult:
        if not attempt_token:
            raise ValueError("attempt_token is required")
        return self._use_case.execute(
            GenerateEmotionSuggestionsCommand(
                scope=task_scope(payload),
                language=text(payload, "language", "zh") or "zh",
            )
        )

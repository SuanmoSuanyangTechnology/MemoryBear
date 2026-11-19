"""Inbound job: validate payload and invoke the context's application use case."""

from collections.abc import Mapping
from typing import Any

from src.application.jobs.payload import integer, text
from src.application.jobs.task_scope import task_scope
from src.application.ports.jobs import JobSpec, ResourceClass
from src.application.rag_profile.dto import GenerateRAGProfileCommand, RAGProfileResult
from src.application.rag_profile.generate import GenerateRAGProfileUseCase


class GenerateRAGProfileJob:
    spec = JobSpec(resource_class=ResourceClass.LLM_BOUND, retryable=False)

    def __init__(self, use_case: GenerateRAGProfileUseCase) -> None:
        self._use_case = use_case

    def handle(self, payload: Mapping[str, Any], *, attempt_token: str) -> RAGProfileResult:
        if not attempt_token:
            raise ValueError("attempt_token is required")
        limit = integer(payload, "limit", 15)
        maximum = integer(payload, "max_tags", 10)
        if not 1 <= limit <= 100 or not 1 <= maximum <= 100:
            raise ValueError("invalid RAG generation limits")
        return self._use_case.execute(
            GenerateRAGProfileCommand(
                scope=task_scope(payload),
                limit=limit,
                max_tags=maximum,
                language=text(payload, "language", "zh") or "zh",
            )
        )

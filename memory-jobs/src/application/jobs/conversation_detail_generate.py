"""Inbound job: validate payload and invoke the context's application use case."""

from collections.abc import Mapping
from typing import Any

from src.application.jobs.task_scope import task_scope, uuid_field
from src.application.ports.jobs import JobSpec, ResourceClass
from src.application.working_memory.dto import (
    ConversationDetailResult,
    GenerateConversationDetailCommand,
)
from src.application.working_memory.generate import GenerateConversationDetailUseCase


class GenerateConversationDetailJob:
    spec = JobSpec(resource_class=ResourceClass.LLM_BOUND, retryable=False)

    def __init__(self, use_case: GenerateConversationDetailUseCase) -> None:
        self._use_case = use_case

    def handle(self, payload: Mapping[str, Any], *, attempt_token: str) -> ConversationDetailResult:
        if not attempt_token:
            raise ValueError("attempt_token is required")
        return self._use_case.execute(
            GenerateConversationDetailCommand(
                scope=task_scope(payload),
                conversation_id=uuid_field(payload, "conversation_id"),
            )
        )

"""Inbound job: validate payload and invoke the context's application use case."""

from collections.abc import Mapping
from typing import Any

from src.application.forgetting.dto import ForgettingResult, TriggerForgettingCommand
from src.application.forgetting.trigger import TriggerForgettingUseCase
from src.application.jobs.payload import integer, text
from src.application.jobs.task_scope import task_scope
from src.application.ports.jobs import JobSpec, ResourceClass


class TriggerForgettingJob:
    spec = JobSpec(resource_class=ResourceClass.LLM_BOUND, retryable=False)

    def __init__(self, use_case: TriggerForgettingUseCase) -> None:
        self._use_case = use_case

    def handle(self, payload: Mapping[str, Any], *, attempt_token: str) -> ForgettingResult:
        if not attempt_token:
            raise ValueError("attempt_token is required")
        maximum = integer(payload, "max_merge_batch_size", 100)
        days = integer(payload, "min_days_since_access", 30)
        if not 1 <= maximum <= 1000 or not 1 <= days <= 365:
            raise ValueError("invalid forgetting limits")
        return self._use_case.execute(
            TriggerForgettingCommand(
                scope=task_scope(payload),
                attempt_token=attempt_token,
                max_merge_batch_size=maximum,
                min_days_since_access=days,
                language=text(payload, "language", "zh") or "zh",
            )
        )

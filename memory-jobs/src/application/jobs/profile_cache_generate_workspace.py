"""The workspace fan-out job.

It answers in the batch shape instead of running the batch: the generation runs
in the per-user units this job queues, so its own budget only covers
enumeration and dispatch.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.application.dto import FanOutReport
from src.application.jobs.payload import text, workspace_id_from
from src.application.ports.jobs import JobSpec, ResourceClass
from src.application.profile_cache.fanout import FanOutWorkspaceUseCase


class FanOutWorkspaceJob:
    """Fan a workspace out into per-user cache units."""

    spec = JobSpec(resource_class=ResourceClass.BULK, acks_late=False)

    def __init__(
        self, use_cases: Callable[[str], FanOutWorkspaceUseCase]
    ) -> None:
        # The use case takes its language at construction, which is only known
        # from the payload; the factory resolves it per attempt.
        self._use_cases = use_cases

    def handle(
        self, payload: Mapping[str, Any], *, attempt_token: str
    ) -> FanOutReport:
        use_case: FanOutWorkspaceUseCase = self._use_cases(
            text(payload, "language", "zh")
        )
        return use_case.execute(workspace_id_from(payload))

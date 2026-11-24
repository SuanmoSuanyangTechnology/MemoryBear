"""The dispatch job: advance one admitted unit by one step.

The task is published on the admission queue by a producer (a message was just
admitted into an idle unit) or by a work task's ``after_return`` (the unit's
attempt finished). A wake is an at-least-once signal: it may arrive twice, or
not at all, which is why the use case is idempotent and the recovery sweep
re-wakes units.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.application.admission.dispatch import DispatchAdmissionUseCase
from src.application.jobs.payload import required_text, text
from src.application.ports.admission.admission import DispatchReport
from src.application.ports.jobs import JobSpec, ResourceClass


class DispatchAdmissionJob:
    """Publish the next work task of an admitted unit."""

    spec = JobSpec(resource_class=ResourceClass.QUICK, acks_late=True)

    def __init__(self, use_case: DispatchAdmissionUseCase) -> None:
        self._use_case = use_case

    def handle(
        self, payload: Mapping[str, Any], *, attempt_token: str
    ) -> DispatchReport:
        return self._use_case.execute(_validated(payload))


def _validated(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """A producer wake carries ``unit_key``; a completion carries ``task_id``."""

    if text(payload, "task_id", ""):
        return payload
    required_text(payload, "unit_key")
    return payload
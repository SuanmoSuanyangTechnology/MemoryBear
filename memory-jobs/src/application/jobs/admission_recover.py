"""The recovery job: expire dead attempts and wake queued-but-idle units.

The sweep repairs state that a lost worker or a lost wake left behind, so a
missed run only means the next run sees the same state again. It is therefore
declared with a non-late acknowledgement: re-running it is always safe.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.application.admission.recover import RecoverAdmissionsUseCase
from src.application.ports.admission.admission import RecoverReport
from src.application.ports.jobs import JobSpec, ResourceClass


class RecoverAdmissionsJob:
    """Run one recovery sweep of the dispatch-admission layer."""

    spec = JobSpec(resource_class=ResourceClass.BULK, acks_late=False)

    def __init__(self, use_case: RecoverAdmissionsUseCase) -> None:
        self._use_case = use_case

    def handle(
        self, _payload: Mapping[str, Any], *, attempt_token: str
    ) -> RecoverReport:
        return self._use_case.execute()
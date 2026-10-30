"""The periodic profile-cache sweep job.

Enumeration, freshness rules, the inflight probe and publishing all live in
``ScanProfileCachesUseCase``; the handler only renders its report. A lost sweep
only means the next run enumerates again, hence the non-late acknowledgement
the spec declares.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.application.dto import ScanReport
from src.application.ports.jobs import JobSpec, ResourceClass
from src.application.profile_cache.scan import ScanProfileCachesUseCase


class ScanProfileCachesJob:
    """Dispatch one ``generate`` unit per user whose cache is out of date."""

    spec = JobSpec(resource_class=ResourceClass.BULK, acks_late=False)

    def __init__(self, use_case: ScanProfileCachesUseCase) -> None:
        self._use_case = use_case

    def handle(
        self, _payload: Mapping[str, Any], *, attempt_token: str
    ) -> ScanReport:
        return self._use_case.execute()

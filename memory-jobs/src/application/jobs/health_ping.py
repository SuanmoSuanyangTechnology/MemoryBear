"""The health job: probe every dependency from inside a worker process."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.application.ports.health import DependencyProbe
from src.application.ports.jobs import JobSpec, ResourceClass


@dataclass(frozen=True, slots=True)
class HealthReport:
    dependencies: dict[str, str]


class PingJob:
    """Report each dependency's reachability; the probe never raises.

    A probe failure is a health answer, not a job failure, so the executor's
    retry policy can replay it freely (``retryable``): a worker whose database
    came back a minute later still answers the same probe.
    """

    spec = JobSpec(resource_class=ResourceClass.DEFAULT, retryable=True)

    def __init__(self, probe: DependencyProbe) -> None:
        self._probe = probe

    def handle(
        self, _payload: Mapping[str, Any], *, attempt_token: str
    ) -> HealthReport:
        return HealthReport(dependencies=self._probe.dependency_health())

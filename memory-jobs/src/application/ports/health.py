"""Read-only dependency probe used by the health job.

The job only reports; probing lives in the infrastructure runtime. Keeping the
probe behind a port is what lets the ping handler live in the application layer
while the clients stay in infrastructure.
"""

from __future__ import annotations

from typing import Protocol


class DependencyProbe(Protocol):
    """Ping every dependency once and report ``up``/``down``; never raises."""

    def dependency_health(self) -> dict[str, str]: ...

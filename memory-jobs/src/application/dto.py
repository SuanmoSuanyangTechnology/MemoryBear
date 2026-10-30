"""Use-case commands and reports.

Reports are plain application results. The wire shape — status fields, key
names, and the transport's ``task_id`` — belongs to the executor adapter
(``src/interfaces/tasks/serialization.py``), so a use case never decides what a
caller sees and swapping the executor does not touch these objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ScanReport:
    dispatched: int = 0
    skip_no_change: int = 0
    skip_fresh: int = 0
    skip_inflight: int = 0
    failed: int = 0
    elapsed_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class FanOutReport:
    total_users: int = 0
    successful: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PrewarmReport:
    dispatched: int = 0
    skipped_cached: int = 0
    skipped_missing: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

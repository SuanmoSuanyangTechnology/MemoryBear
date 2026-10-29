"""Use-case commands and reports.

Reports own the external payload shape: the Celery adapter only adds the fields
that belong to the transport (``task_id``) or to its own parsing (``invalid``).
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

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": "SUCCESS",
            "dispatched": self.dispatched,
            "skip_no_change": self.skip_no_change,
            "skip_fresh": self.skip_fresh,
            "skip_inflight": self.skip_inflight,
            "failed": self.failed,
            "elapsed_seconds": self.elapsed_seconds,
        }


@dataclass(frozen=True, slots=True)
class FanOutReport:
    total_users: int = 0
    successful: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "total_users": self.total_users,
            "successful": self.successful,
            "failed": self.failed,
            "errors": self.errors,
        }


@dataclass(frozen=True, slots=True)
class PrewarmReport:
    dispatched: int = 0
    skipped_cached: int = 0
    skipped_missing: int = 0
    failed: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

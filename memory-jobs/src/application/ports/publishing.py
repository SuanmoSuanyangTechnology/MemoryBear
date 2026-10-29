"""Ports for publishing per-user work units.

Priority is the application's vocabulary; the Celery adapter maps it onto the
shared ``task_bridge`` contract.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Protocol


class Priority(IntEnum):
    """Matches the shared AMQP contract: higher is more urgent."""

    INTERACTIVE = 9
    NORMAL = 5
    BACKGROUND = 0


class UnitPublisher(Protocol):
    def publish_profile_cache(
        self,
        *,
        end_user_id: str,
        workspace_id: str,
        language: str,
        refresh_insight: bool,
        refresh_summary: bool,
        priority: Priority,
        index: int,
    ) -> None: ...

    def publish_interest_distribution(
        self,
        *,
        end_user_id: str,
        workspace_id: str,
        language: str,
        limit: int,
        write_cache: bool,
        priority: Priority,
        index: int,
    ) -> None: ...

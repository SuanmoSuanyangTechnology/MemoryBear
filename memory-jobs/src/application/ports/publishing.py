"""Ports for publishing per-user work units.

``Priority`` is the application's vocabulary: a semantic level, not a broker
number. The executor adapter maps it onto its own transport contract.

``dispatch_index`` is the position of the unit in the caller's burst. It is the
only pacing information the application provides; whether and how to delay the
unit is the adapter's decision.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class Priority(StrEnum):
    """How urgent a unit is relative to the caller's other work."""

    INTERACTIVE = "interactive"
    NORMAL = "normal"
    BACKGROUND = "background"


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
        dispatch_index: int,
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
        dispatch_index: int,
    ) -> None: ...

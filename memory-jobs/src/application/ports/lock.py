"""Port for the per-user in-flight lock."""

from __future__ import annotations

from typing import Protocol


class InflightLock(Protocol):
    def acquire(self, end_user_id: str, owner: str) -> bool: ...

    def release(self, end_user_id: str, owner: str) -> None: ...

    def is_locked(self, end_user_id: str) -> bool: ...

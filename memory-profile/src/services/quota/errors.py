"""Typed quota denial rendered by the memory-config handler."""

from __future__ import annotations

from typing import Any


class QuotaDenied(Exception):
    """A quota check failed.

    The handler renders the legacy non-standard 402 body; keeping the typed
    error here means the admission code never knows about HTTP or i18n.
    """

    def __init__(
        self,
        *,
        resource: str,
        current_usage: int | float,
        quota_limit: int | float,
        quota_type: str = "memory_engine_quota",
    ) -> None:
        super().__init__(f"{resource} quota exceeded")
        self.resource = resource
        self.current_usage = current_usage
        self.quota_limit = quota_limit
        self.quota_type = quota_type

    def as_data(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "current_usage": self.current_usage,
            "quota_limit": self.quota_limit,
        }

"""Port for the interest-distribution cache."""

from __future__ import annotations

from typing import Any, Protocol


class InterestCache(Protocol):
    def exists(self, end_user_id: str, language: str) -> bool: ...

    def write(
        self, end_user_id: str, language: str, items: list[dict[str, Any]]
    ) -> None: ...

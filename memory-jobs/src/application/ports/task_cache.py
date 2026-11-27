"""Business cache and resource-lock boundary shared by migrated tasks."""

from contextlib import AbstractContextManager
from typing import Protocol


class TaskCache(Protocol):
    def lock(self, key: str) -> AbstractContextManager[object]: ...
    def invalidate(self, user_id: str, workspace_id: str) -> None: ...

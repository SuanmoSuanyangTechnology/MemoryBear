"""Unit of Work: one explicit transaction boundary per use-case step.

The application opens a read scope or a transaction; the infrastructure binds a
session to it and hands back repositories that participate in that scope.
Repositories never own a session or commit on their own, so a use case decides
exactly which write is atomic.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from src.domain.end_user.repository import EndUserDirectoryRepository
from src.domain.model_config.repository import ModelConfigRepository
from src.domain.profile.repository import (
    MemoryInsightRepository,
    ProfileRefreshReader,
    UserSummaryRepository,
)
from src.domain.workspace.repository import WorkspaceRepository


@dataclass(frozen=True, slots=True)
class Repositories:
    """Repositories bound to the current scope's session."""

    workspaces: WorkspaceRepository
    model_configs: ModelConfigRepository
    end_users: EndUserDirectoryRepository
    profile_refresh: ProfileRefreshReader
    insights: MemoryInsightRepository
    summaries: UserSummaryRepository


class UnitOfWork(Protocol):
    """A factory for scoped repository bundles."""

    def read(self) -> AbstractContextManager[Repositories]:
        """Open a read-only scope; ends the transaction when it exits."""
        ...

    def transaction(self) -> AbstractContextManager[Repositories]:
        """Open a write scope; commits on success and rolls back on error."""
        ...

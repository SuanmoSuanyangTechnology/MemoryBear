"""Repository interface for the profile refresh read model."""

from __future__ import annotations

from typing import Protocol

from src.domain.profile.model import MemoryInsight, UserSummary, WriteOutcome
from src.domain.profile.read_models import (
    EntityTag,
    InsightSource,
    RefreshCandidate,
    StatementSample,
)
from src.domain.shared.ids import EndUserId, WorkspaceId


class MemoryInsightRepository(Protocol):
    def find_source(
        self, end_user_id: EndUserId, workspace_id: WorkspaceId
    ) -> InsightSource | None: ...

    def save_if_source_unchanged(self, insight: MemoryInsight) -> WriteOutcome: ...


class UserSummaryRepository(Protocol):
    def save(self, summary: UserSummary) -> WriteOutcome: ...


class InsightStatementReader(Protocol):
    """Neo4j fallback input for the insight prompt."""

    def recent_user_statements(
        self, end_user_id: str, statement_types: list[str], limit: int
    ) -> list[str]: ...


class SummarySourceReader(Protocol):
    """Neo4j inputs for the summary prompt."""

    def recent_statements(
        self, end_user_id: str, limit: int
    ) -> list[StatementSample]: ...

    def raw_entity_tags(self, end_user_id: str, limit: int) -> list[EntityTag]: ...


class ProfileRefreshReader(Protocol):
    """The scan's outer loop and its keyset-paged candidate set."""

    def active_workspace_ids(self) -> list[WorkspaceId]:
        """Every active workspace, ordered by id."""
        ...

    def refresh_candidates(
        self,
        workspace_id: WorkspaceId,
        *,
        after: EndUserId | None,
        limit: int,
    ) -> tuple[list[RefreshCandidate], EndUserId | None]:
        """One page of cache/source timestamps for the workspace's users.

        Ordered by end-user id; returns the page and the cursor for the next
        call, or ``None`` when the workspace has no further users. Paging keeps
        a sweep's memory bounded by ``limit`` on a large workspace.
        """
        ...

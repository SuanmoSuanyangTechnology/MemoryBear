"""SQLAlchemy implementation of the profile refresh read model.

Candidates are read one keyset page at a time (ordered by end-user id), so the
worker's memory is bounded by the page size instead of by the workspace's user
count. The scan opens a fresh read scope per page, so no connection is held
while the page's units are published.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.profile.read_models import RefreshCandidate
from src.domain.shared.ids import EndUserId, WorkspaceId
from src.infrastructure.persistence.postgres.mappers import to_refresh_candidate
from src.models import EndUser, EndUserInfo, Workspace


class SqlAlchemyProfileRefreshReader:
    def __init__(self, session: Session) -> None:
        self._session = session

    def active_workspace_ids(self) -> list[WorkspaceId]:
        statement = select(Workspace.id).where(Workspace.is_active.is_(True))
        rows = self._session.execute(statement).all()
        return [WorkspaceId(row[0]) for row in rows]

    def refresh_candidates(
        self,
        workspace_id: WorkspaceId,
        *,
        after: EndUserId | None,
        limit: int,
    ) -> tuple[list[RefreshCandidate], EndUserId | None]:
        if limit <= 0:
            return [], None
        statement = (
            select(
                EndUser.id.label("end_user_id"),
                EndUser.workspace_id,
                EndUser.write_time,
                EndUserInfo.updated_at.label("metadata_updated_at"),
                EndUser.memory_insight_updated_at,
                EndUser.user_summary_updated_at,
            )
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .outerjoin(EndUserInfo, EndUserInfo.end_user_id == EndUser.id)
            .where(
                EndUser.workspace_id == workspace_id.value,
                EndUser.is_active.is_(True),
                # Same predicate as the API's scanner: a host with zero memories
                # has nothing to summarise, so dispatching it only burns an LLM
                # call to produce the "insufficient memory" placeholder.
                EndUser.memory_count > 0,
                Workspace.is_active.is_(True),
                sa.or_(Workspace.storage_type.is_(None), Workspace.storage_type == "neo4j"),
                *([] if after is None else [EndUser.id > after.value]),
            )
            .order_by(EndUser.id)
            .limit(limit)
        )
        rows = self._session.execute(statement).mappings().all()
        candidates = [to_refresh_candidate(row) for row in rows]
        # A short (or empty) page is the last one; a full page hands back its
        # last id as the exclusive cursor for the next call.
        next_cursor = (
            candidates[-1].end_user_id
            if candidates and len(candidates) == limit
            else None
        )
        return candidates, next_cursor

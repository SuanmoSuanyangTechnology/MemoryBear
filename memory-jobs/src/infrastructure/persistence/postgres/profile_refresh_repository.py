"""SQLAlchemy implementation of the profile refresh read model.

One query per workspace keeps memory flat on large tenants; the outer loop is
the active workspace list.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.profile.read_models import RefreshCandidate
from src.domain.shared.ids import WorkspaceId
from src.infrastructure.persistence.postgres.mappers import to_refresh_candidate
from src.models import EndUser, EndUserInfo, Workspace


class SqlAlchemyProfileRefreshReader:
    def __init__(self, session: Session) -> None:
        self._session = session

    def active_workspace_ids(self) -> list[WorkspaceId]:
        statement = select(Workspace.id).where(Workspace.is_active.is_(True))
        rows = self._session.execute(statement).all()
        return [WorkspaceId(row[0]) for row in rows]

    def refresh_candidates(self, workspace_id: WorkspaceId) -> list[RefreshCandidate]:
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
            )
        )
        rows = self._session.execute(statement).mappings().all()
        return [to_refresh_candidate(row) for row in rows]

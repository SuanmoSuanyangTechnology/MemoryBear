"""SQLAlchemy implementation of the end-user directory.

The merge rule itself lives in ``UserResolutionService``; this adapter returns
the direct hit and the valid merge targets, newest first.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.end_user.model import ResolutionCandidates
from src.domain.shared.ids import EndUserId, WorkspaceId
from src.infrastructure.persistence.postgres.conditions import (
    active_user_in_workspace,
    active_users_in_workspace,
)
from src.infrastructure.persistence.postgres.mappers import (
    to_resolution_candidates,
)
from src.models import EndUser, EndUserMerge, Workspace

#: A user rarely has many merges; the newest one decides, so a bound keeps the
#: read flat without changing the rule.
MERGE_CANDIDATE_LIMIT = 50


class SqlAlchemyEndUserDirectoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def active_ids_in(self, workspace_id: WorkspaceId) -> list[EndUserId]:
        statement = (
            select(EndUser.id)
            .where(*active_users_in_workspace(workspace_id.value))
            .order_by(EndUser.id)
        )
        rows = self._session.execute(statement).all()
        return [EndUserId(row[0]) for row in rows]

    def resolution_candidates(
        self, end_user_id: EndUserId, workspace_id: WorkspaceId
    ) -> ResolutionCandidates:
        direct_statement = (
            select(
                EndUser.id,
                EndUser.workspace_id,
                EndUser.other_name,
                EndUser.is_active,
            )
            .select_from(EndUser)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .where(
                *active_user_in_workspace(
                    end_user_id=end_user_id.value, workspace_id=workspace_id.value
                )
            )
            .limit(1)
        )
        merged_statement = (
            select(
                EndUser.id,
                EndUser.workspace_id,
                EndUser.other_name,
                EndUser.is_active,
                EndUserMerge.id.label("sequence"),
            )
            .select_from(EndUserMerge)
            .join(EndUser, EndUser.id == EndUserMerge.target_id)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .where(
                EndUserMerge.origin_id == end_user_id.value,
                EndUserMerge.workspace_id == workspace_id.value,
                *active_users_in_workspace(workspace_id.value),
                Workspace.is_active.is_(True),
            )
            .order_by(EndUserMerge.id.desc())
            .limit(MERGE_CANDIDATE_LIMIT)
        )
        direct = self._session.execute(direct_statement).mappings().one_or_none()
        merges = list(self._session.execute(merged_statement).mappings().all())
        return to_resolution_candidates(end_user_id, workspace_id, direct, merges)

    def display_name(self, end_user_id: EndUserId) -> str | None:
        statement = (
            select(EndUser.other_name)
            .where(
                EndUser.id == end_user_id.value,
                EndUser.is_active.is_(True),
            )
            .limit(1)
        )
        return self._session.execute(statement).scalar()

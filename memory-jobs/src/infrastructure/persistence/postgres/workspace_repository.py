"""SQLAlchemy implementation of the Workspace repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.domain.shared.ids import WorkspaceId
from src.domain.workspace.model import Workspace
from src.infrastructure.persistence.postgres.mappers import to_workspace
from src.models import Workspace as WorkspaceRow


class SqlAlchemyWorkspaceRepository:
    """Load one workspace in the Unit of Work's current transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, workspace_id: WorkspaceId) -> Workspace | None:
        statement = (
            select(
                WorkspaceRow.id,
                WorkspaceRow.name,
                WorkspaceRow.tenant_id,
                WorkspaceRow.is_active,
                WorkspaceRow.storage_type,
                WorkspaceRow.is_default_config,
                WorkspaceRow.memory_config,
                WorkspaceRow.llm,
                WorkspaceRow.embedding,
                WorkspaceRow.rerank,
                WorkspaceRow.vision,
                WorkspaceRow.audio,
                WorkspaceRow.video,
            )
            .where(WorkspaceRow.id == workspace_id.value)
            .limit(1)
        )
        row = self._session.execute(statement).mappings().one_or_none()
        return None if row is None else to_workspace(row)

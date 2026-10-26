"""Workspace reads for validating a generation request and resolving its models."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.infrastructure.postgres import PostgresManager
from src.models import Workspace


class WorkspaceRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    def workspace_row(self, workspace_id: UUID) -> dict[str, Any] | None:
        """One row carrying validation facts and all six model columns."""

        statement = (
            select(
                Workspace.id,
                Workspace.name,
                Workspace.tenant_id,
                Workspace.is_active,
                Workspace.storage_type,
                Workspace.is_default_config,
                Workspace.memory_config,
                Workspace.llm,
                Workspace.embedding,
                Workspace.rerank,
                Workspace.vision,
                Workspace.audio,
                Workspace.video,
            )
            .where(Workspace.id == workspace_id)
            .limit(1)
        )
        with self.postgres.session() as session:
            row = session.execute(statement).mappings().one_or_none()
            return dict(row) if row is not None else None



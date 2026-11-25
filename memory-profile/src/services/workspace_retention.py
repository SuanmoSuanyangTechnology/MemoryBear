"""Use case for the temporary-identity retention policy endpoints.

Reading the policy is two workspace-scoped queries. Updating it is one workspace
row plus the grace-deadline sweep its shorter period implies; the mutation
repository owns that transaction, this service owns the response shape.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.infrastructure.postgres import PostgresManager
from src.repositories.workspace_retention import WorkspaceRetentionRepository
from src.repositories.workspace_retention_mutation import (
    WorkspaceRetentionMutationRepository,
)
from src.schemas.workspace_api import WorkspaceRetentionPolicyData


class WorkspaceRetentionService:
    def __init__(self, postgres: PostgresManager) -> None:
        self.read = WorkspaceRetentionRepository(postgres)
        self.mutation = WorkspaceRetentionMutationRepository(postgres)

    async def policy(self, workspace_id: UUID) -> dict[str, Any]:
        """``{"retention_days": ..., "end_user_count": ...}`` for the GET."""

        return WorkspaceRetentionPolicyData(
            retention_days=await self.read.retention_days(workspace_id),
            end_user_count=await self.read.temporary_end_user_count(workspace_id),
        ).model_dump(mode="json")

    async def update(
        self, workspace_id: UUID, retention_days: int | None
    ) -> dict[str, Any]:
        """``{"retention_days": ...}`` only, matching the host's ``exclude_unset``."""

        updated = await self.mutation.update(workspace_id, retention_days)
        return WorkspaceRetentionPolicyData(retention_days=updated).model_dump(
            mode="json", exclude_unset=True
        )
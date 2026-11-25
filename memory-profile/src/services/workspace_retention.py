"""Read and update workspace temporary-memory retention."""

from __future__ import annotations

from uuid import UUID

from src.common.errors import BizCode, LocalizedProfileError
from src.infrastructure.postgres import PostgresManager
from src.repositories.workspace import WorkspaceRepository
from src.repositories.workspace_retention_mutation import WorkspaceRetentionMutationRepository
from src.schemas.workspace import (
    WorkspaceRetentionPolicyResponse,
    WorkspaceRetentionPolicyResult,
)


class WorkspaceRetentionService:
    def __init__(self, postgres: PostgresManager) -> None:
        self.repository = WorkspaceRepository(postgres)
        self.mutation = WorkspaceRetentionMutationRepository(postgres)

    async def get_workspace_retention_policy(
        self, workspace_id: UUID, user_id: UUID
    ) -> WorkspaceRetentionPolicyResponse:
        await self._require_workspace_access(workspace_id, user_id)
        retention_days, end_user_count = await self.repository.get_workspace_retention_policy(
            workspace_id
        )
        return WorkspaceRetentionPolicyResponse(
            retention_days=retention_days, end_user_count=end_user_count
        )

    async def update_workspace_retention_policy(
        self, workspace_id: UUID, user_id: UUID, retention_days: int | None
    ) -> WorkspaceRetentionPolicyResult:
        await self._require_workspace_access(workspace_id, user_id)
        updated_retention_days = await self.mutation.update_workspace_retention_days(
            workspace_id, retention_days
        )
        return WorkspaceRetentionPolicyResult(retention_days=updated_retention_days)

    async def _require_workspace_access(self, workspace_id: UUID, user_id: UUID) -> None:
        access = await self.repository.workspace_access(workspace_id, user_id)
        if access is None:
            raise LocalizedProfileError(
                BizCode.NOT_FOUND,
                "common.http.not_found",
                detail="Workspace not found",
                status_code=404,
            )
        if access["is_superuser"]:
            if access["user_tenant_id"] == access["workspace_tenant_id"]:
                return
        elif access["is_superuser"] is False and access["is_member"]:
            return
        raise LocalizedProfileError(
            BizCode.FORBIDDEN,
            "workspace.retention_policy.forbidden",
            detail="Forbidden",
            status_code=403,
        )

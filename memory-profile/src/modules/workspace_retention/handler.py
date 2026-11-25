"""Transport adapters for workspace temporary-memory retention."""

from __future__ import annotations

from src.common.responses import ApiResponse
from src.i18n import t
from src.middleware.auth import MemoryPrincipal
from src.modules.workspace_retention.schemas import WorkspaceRetentionPolicyUpdate
from src.runtime import ProfileRuntime
from src.schemas.workspace import (
    WorkspaceRetentionPolicyResponse,
    WorkspaceRetentionPolicyResult,
)
from src.services.workspace_retention import WorkspaceRetentionService


class WorkspaceRetentionHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.service = WorkspaceRetentionService(runtime.postgres)

    async def get_workspace_retention_policy(
        self, context: MemoryPrincipal, language: str
    ) -> ApiResponse[WorkspaceRetentionPolicyResponse]:
        data = await self.service.get_workspace_retention_policy(
            context.workspace_id, context.actor_id
        )
        return ApiResponse(
            data=data, msg=t("workspace.retention_policy.retrieved", language)
        )

    async def update_workspace_retention_policy(
        self, context: MemoryPrincipal, policy: WorkspaceRetentionPolicyUpdate, language: str
    ) -> ApiResponse[WorkspaceRetentionPolicyResult]:
        data = await self.service.update_workspace_retention_policy(
            context.workspace_id, context.actor_id, policy.retention_days
        )
        return ApiResponse(
            data=data, msg=t("workspace.retention_policy.updated", language)
        )

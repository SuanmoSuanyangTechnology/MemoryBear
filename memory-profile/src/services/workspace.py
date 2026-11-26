"""Workspace eligibility rules and the workspace row other modules read.

Reading a workspace is not analytics-specific: validation needs ``is_active`` and
``storage_type``, model resolution needs ``is_default_config``, ``memory_config``
and the six model columns, and display code needs ``name``. One service owns the
row shape so a new caller does not invent a narrower query that drifts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from src.common.errors import BizCode, LocalizedProfileError
from src.infrastructure.postgres import PostgresManager
from src.repositories.workspace import WorkspaceRepository
from src.schemas.workspace import (
    WorkspaceEligibility,
    WorkspaceRetentionPolicyResponse,
    WorkspaceRetentionPolicyResult,
)

#: Reason values are part of the cross-service contract with memory-jobs; they
#: are named so callers compare against a symbol instead of a free string.
REASON_VALID = "valid"
REASON_WORKSPACE_NOT_FOUND = "workspace_not_found"
REASON_WORKSPACE_INACTIVE = "workspace_inactive"
REASON_UNSUPPORTED_STORAGE_TYPE = "unsupported_storage_type"


def validate_workspace_row(
    workspace: Mapping[str, Any] | None,
) -> WorkspaceEligibility:
    """Return the eligibility verdict for an already-loaded workspace row."""

    if workspace is None:
        return WorkspaceEligibility(valid=False, reason=REASON_WORKSPACE_NOT_FOUND)
    raw_storage_type = workspace["storage_type"]
    if not workspace["is_active"]:
        return WorkspaceEligibility(
            valid=False,
            reason=REASON_WORKSPACE_INACTIVE,
            storage_type=raw_storage_type,
        )
    storage_type = "neo4j" if raw_storage_type is None else str(raw_storage_type).lower()
    if storage_type != "neo4j":
        return WorkspaceEligibility(
            valid=False,
            reason=REASON_UNSUPPORTED_STORAGE_TYPE,
            storage_type=storage_type or None,
        )
    return WorkspaceEligibility(
        valid=True, reason=REASON_VALID, storage_type=storage_type
    )


class WorkspaceService:
    """Read workspace data and manage its temporary-memory retention policy."""

    def __init__(self, repository: WorkspaceRepository) -> None:
        self.repository = repository

    @classmethod
    def from_postgres(cls, postgres: PostgresManager) -> WorkspaceService:
        """Build the service with its default repository (the composition path)."""

        return cls(WorkspaceRepository(postgres))

    async def row(self, workspace_id: UUID) -> dict[str, Any] | None:
        return await self.repository.workspace_row(workspace_id)

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
        updated_retention_days = await self.repository.update_workspace_retention_days(
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

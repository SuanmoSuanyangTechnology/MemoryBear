"""Read-only workspace lookup shared by every module.

The row carries the workspace's identity, storage type and all six model
columns, so the generation entrypoint's validation and the model-config
resolution read it once instead of each querying its own subset.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser, Workspace
from src.models.references.user_model import User
from src.models.references.workspace_member_model import WorkspaceMember


class WorkspaceRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def workspace_access(
        self, workspace_id: UUID, user_id: UUID
    ) -> dict[str, Any] | None:
        membership = (
            select(WorkspaceMember.id)
            .where(
                WorkspaceMember.workspace_id == Workspace.id,
                WorkspaceMember.user_id == user_id,
                WorkspaceMember.is_active.is_(True),
            )
            .exists()
        )
        statement = (
            select(
                Workspace.tenant_id.label("workspace_tenant_id"),
                User.tenant_id.label("user_tenant_id"),
                User.is_superuser,
                membership.label("is_member"),
            )
            .select_from(Workspace)
            .outerjoin(User, (User.id == user_id) & User.is_active.is_(True))
            .where(Workspace.id == workspace_id)
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def get_workspace_retention_policy(
        self, workspace_id: UUID
    ) -> tuple[int | None, int]:
        async with self.postgres.session() as session:
            retention_days = await session.scalar(
                select(Workspace.retention_days).where(Workspace.id == workspace_id)
            )
            end_user_count = (
                await session.execute(
                    select(func.count())
                    .select_from(EndUser)
                    .where(
                        EndUser.workspace_id == workspace_id,
                        EndUser.is_active.is_(True),
                        EndUser.identity_status == "temporary",
                        EndUser.memory_count >= 1,
                    )
                )
            ).scalar_one()
        return retention_days, end_user_count

    async def workspace_row(self, workspace_id: UUID) -> dict[str, Any] | None:
        """工作空间所需列（活跃判定、存储类型、六类模型列与默认配置标记）。"""

        async with self.postgres.session() as session:
            row = (
                await session.execute(
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
            ).mappings().one_or_none()
            return dict(row) if row is not None else None

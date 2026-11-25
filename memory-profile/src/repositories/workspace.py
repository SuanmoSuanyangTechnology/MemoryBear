"""Workspace lookup and temporary-memory retention storage.

The row carries the workspace's identity, storage type and all six model
columns, so the generation entrypoint's validation and the model-config
resolution read it once instead of each querying its own subset.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, true, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.timing import utcnow_naive
from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser, Workspace
from src.models.references.user_model import User
from src.models.references.workspace_member_model import WorkspaceMember


logger = logging.getLogger(__name__)
_RETENTION_GRACE_PERIOD_DAYS = 3
_GRACE_PERIOD_UPDATE_BATCH_SIZE = 1000


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

    async def update_workspace_retention_days(
        self, workspace_id: UUID, retention_days: int | None
    ) -> int | None:
        """Update retention and mark newly expired identities in one transaction."""
        async with self.postgres.session() as session, session.begin():
            workspace = await session.scalar(
                select(Workspace).where(Workspace.id == workspace_id).with_for_update()
            )
            if workspace is None:
                raise ValueError(f"Workspace not found: {workspace_id}")

            old_retention_days = workspace.retention_days
            grace_period_affected_count = 0
            if retention_days is not None and (
                old_retention_days is None or retention_days < old_retention_days
            ):
                reference_time = utcnow_naive()
                grace_period_affected_count = await self._mark_grace_period_for_expired_users(
                    session=session,
                    workspace_id=workspace_id,
                    cutoff_time=reference_time - timedelta(days=retention_days),
                    grace_period_until=reference_time
                    + timedelta(days=_RETENTION_GRACE_PERIOD_DAYS),
                )

            if retention_days != old_retention_days:
                workspace.retention_days = retention_days
                workspace.updated_at = utcnow_naive()

        logger.info(
            "更新工作空间保留天数成功: workspace_id=%s, retention_days=%s, "
            "grace_period_affected_count=%s",
            workspace_id,
            retention_days,
            grace_period_affected_count,
        )
        return retention_days

    async def _mark_grace_period_for_expired_users(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        cutoff_time: datetime,
        grace_period_until: datetime,
    ) -> int:
        filters = (
            EndUser.workspace_id == workspace_id,
            EndUser.is_active == true(),
            EndUser.identity_status == "temporary",
            EndUser.write_time.is_not(None),
            EndUser.write_time < cutoff_time,
        )
        affected_count = 0
        last_candidate = None

        while True:
            candidates_query = select(EndUser.id, EndUser.write_time).where(*filters)
            if last_candidate is not None:
                candidates_query = candidates_query.where(
                    tuple_(EndUser.write_time, EndUser.id)
                    > tuple_(last_candidate.write_time, last_candidate.id)
                )
            candidates = (
                await session.execute(
                    candidates_query.order_by(EndUser.write_time.asc(), EndUser.id.asc())
                    .limit(_GRACE_PERIOD_UPDATE_BATCH_SIZE)
                )
            ).all()
            if not candidates:
                break

            result = await session.execute(
                update(EndUser)
                .where(EndUser.id.in_([candidate.id for candidate in candidates]), *filters)
                .values(grace_period_until=grace_period_until, updated_at=utcnow_naive())
            )
            affected_count += max(result.rowcount or 0, 0)
            last_candidate = candidates[-1]
            if len(candidates) < _GRACE_PERIOD_UPDATE_BATCH_SIZE:
                break

        return affected_count

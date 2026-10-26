"""Read/write access for the profile generation pipelines.

The only writes are the two cache columns on ``end_users`` produced by
``generate_cache``; they use the same pooled sessions as the reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select

from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser, EndUserInfo, Workspace
from src.modules.shared.end_user_scope import (
    active_user_in_workspace,
    active_users_in_workspace,
    active_workspace_exists,
)


class MemoryGenerationRepository:
    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def insight_source(
        self, end_user_id: UUID, workspace_id: UUID
    ) -> dict[str, Any] | None:
        """洞察源数据与并发版本（api ``get_scoped_memory_insight_source_async``）。"""

        statement = (
            select(
                EndUserInfo.id.label("metadata_id"),
                EndUserInfo.meta_data.label("meta_data"),
                EndUserInfo.updated_at.label("metadata_updated_at"),
                EndUser.write_time.label("write_time"),
            )
            .select_from(EndUser)
            .join(Workspace, Workspace.id == EndUser.workspace_id)
            .outerjoin(EndUserInfo, EndUserInfo.end_user_id == EndUser.id)
            .where(
                *active_user_in_workspace(
                    end_user_id=end_user_id,
                    workspace_id=workspace_id,
                ),
                sa.or_(
                    Workspace.storage_type.is_(None),
                    Workspace.storage_type == "neo4j",
                ),
            )
            .limit(1)
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().one_or_none()
            if row is None:
                return None
            return {
                "meta_data": row["meta_data"],
                "metadata_row_exists": row["metadata_id"] is not None,
                "metadata_updated_at": row["metadata_updated_at"],
                "write_time": row["write_time"],
            }

    # ── 写：生成缓存（end_users 两组长文本列） ─────────────────────

    async def update_user_summary(
        self,
        end_user_id: UUID,
        user_summary: str,
        personality: str,
        core_values: str,
        one_sentence: str,
        refreshed_at: datetime,
    ) -> bool:
        async with self.postgres.session() as session:
            result = await session.execute(
                sa.update(EndUser)
                .where(EndUser.id == end_user_id, EndUser.is_active.is_(True))
                .values(
                    user_summary=user_summary,
                    personality_traits=personality,
                    core_values=core_values,
                    one_sentence_summary=one_sentence,
                    user_summary_updated_at=refreshed_at,
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            return bool(result.rowcount)

    async def update_insight_if_source_unchanged(
        self,
        workspace_id: UUID,
        end_user_id: UUID,
        *,
        expected_write_time: datetime | None,
        expected_metadata_row_exists: bool,
        expected_metadata_updated_at: datetime | None,
        memory_insight: str,
        key_findings: str,
        refreshed_at: datetime,
    ) -> bool:
        """源数据未变化时写回洞察（api 同名方法同款乐观并发条件）。"""

        workspace_active = active_workspace_exists(
            workspace_id,
            sa.or_(Workspace.storage_type.is_(None), Workspace.storage_type == "neo4j"),
        )
        if expected_metadata_row_exists:
            metadata_unchanged = sa.exists().where(
                EndUserInfo.end_user_id == end_user_id,
                EndUserInfo.updated_at.is_not_distinct_from(
                    expected_metadata_updated_at
                ),
            )
        else:
            metadata_unchanged = ~sa.exists().where(
                EndUserInfo.end_user_id == end_user_id
            )
        async with self.postgres.session() as session:
            result = await session.execute(
                sa.update(EndUser)
                .where(
                    EndUser.id == end_user_id,
                    *active_users_in_workspace(workspace_id),
                    EndUser.write_time.is_not_distinct_from(expected_write_time),
                    workspace_active,
                    metadata_unchanged,
                )
                .values(
                    memory_insight=memory_insight,
                    memory_insight_updated_at=refreshed_at,
                    key_findings=key_findings,
                    behavior_pattern="",
                    growth_trajectory="",
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            return bool(result.rowcount)

    # ── 只读：展示名与用户数 ───────────────────────────────────────

    async def other_name(self, end_user_id: UUID) -> str | None:
        async with self.postgres.session() as session:
            return await session.scalar(
                select(EndUser.other_name)
                .where(EndUser.id == end_user_id, EndUser.is_active.is_(True))
                .limit(1)
            )

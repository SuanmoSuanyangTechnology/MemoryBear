"""Workspace-level profile aggregations over API-owned read-only mappings."""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import func, select

from src.infrastructure.postgres import PostgresManager
from src.models.references import EndUser
from src.modules.shared.end_user_scope import active_users_in_workspace


class WorkspaceProfileRepository:
    """只读的工作空间画像聚合（与 api ``EndUserRepository`` 同款 SQL）。"""

    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def hot_memory_tags(
        self,
        workspace_id: UUID,
        limit: int,
        max_tag_length: int,
    ) -> list[tuple[str, int]]:
        """聚合工作空间活跃终端用户名片 Tag 的 Top-N。

        规范化（空白折叠 / 去空 / 长度上限）与大小写合并整体下沉到 PostgreSQL，
        与 api ``get_hot_memory_tags_by_workspace_async`` 保持同一套 SQL 语义：
        代表文本取同一折叠键下码点序最小值，``frequency`` 为采用该 Tag 的用户数。
        """

        expanded_tag = (
            func.jsonb_array_elements_text(
                sa.case(
                    (
                        func.jsonb_typeof(EndUser.memory_tags) == "array",
                        EndUser.memory_tags,
                    ),
                    else_=func.jsonb_build_array(),
                )
            )
            .table_valued(sa.column("value", sa.Text))
            .lateral()
        )
        tag = func.btrim(
            func.regexp_replace(expanded_tag.c.value, "[[:space:]]+", " ", "g")
        )
        per_user_tags = (
            select(
                EndUser.id.label("end_user_id"),
                tag.label("tag"),
                func.lower(tag).label("tag_key"),
            )
            .select_from(EndUser)
            .join(expanded_tag, sa.true())
            .where(
                *active_users_in_workspace(workspace_id),
                tag != "",
                func.char_length(tag) <= max_tag_length,
            )
            .distinct()
            .cte("per_user_tags")
        )
        hot_tag_name = func.min(sa.collate(per_user_tags.c.tag, "C"))
        user_count = func.count(sa.distinct(per_user_tags.c.end_user_id))
        statement = (
            select(
                hot_tag_name.label("name"),
                user_count.label("frequency"),
            )
            .group_by(per_user_tags.c.tag_key)
            .order_by(user_count.desc(), hot_tag_name.asc())
            .limit(limit)
        )
        async with self.postgres.session() as session:
            result = await session.execute(statement)
            return [(name, int(frequency)) for name, frequency in result.all()]

"""Profile domain constants and read use-cases.

The constants are shared by the statistics service and the presentation layer
so API-contract drift stays visible in one place. ``ProfileQueryService``
orchestrates insight, summary, node statistics, workspace hot tags and the
legacy end-user profile.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.common.errors import BizCode, LocalizedProfileError
from src.common.normalization import parse_uuid
from src.common.vocabulary import COUNT_KEYS
from src.repositories.graph import MemoryGraphRepository
from src.repositories.profile import (
    EndUserProfileRepository,
    MemoryStatisticsRepository,
    UserProfileRepository,
    WorkspaceProfileRepository,
)
from src.repositories.search import MemorySearchRepository
from src.runtime import ProfileRuntime
from src.schemas.profile import MemoryInsight, UserSummary
from src.services._helpers import safe_count, short_term_count
from src.services.end_user import EndUserService

logger = logging.getLogger(__name__)

#: 兴趣分布接口返回的标签数量上限（与 api 的展示上限一致）。
INTEREST_DISPLAY_LIMIT = 5

#: 用户名片 Tag 的字符长度上限（api ``user_card_tags.USER_CARD_TAG_MAX_LENGTH`` 同值）。
USER_CARD_TAG_MAX_LENGTH = 12

#: ``node_statistics`` 的固定返回顺序。
MEMORY_TYPES: tuple[str, ...] = (
    "PERCEPTUAL_MEMORY",
    "WORKING_MEMORY",
    "SHORT_TERM_MEMORY",
    "EXPLICIT_MEMORY",
    "IMPLICIT_MEMORY",
    "EMOTIONAL_MEMORY",
    "EPISODIC_MEMORY",
    "FORGET_MEMORY",
)


def _zero_memory_statistics() -> list[dict[str, Any]]:
    return [{"type": memory_type, "count": 0, "percentage": 0.0} for memory_type in MEMORY_TYPES]


class ProfileQueryService:
    """洞察、摘要、节点统计与 legacy 用户画像的编排。"""

    def __init__(self, runtime: ProfileRuntime) -> None:
        self.end_user_profile = EndUserProfileRepository(runtime.postgres)
        self.end_user = EndUserService.from_postgres(runtime.postgres)
        self.memory_statistics = MemoryStatisticsRepository(runtime.postgres)
        self.memory_graph = MemoryGraphRepository(runtime.neo4j)
        self.memory_search = MemorySearchRepository(runtime.elasticsearch)
        self.workspace_profile = WorkspaceProfileRepository(runtime.postgres)
        self.user_profile_graph = UserProfileRepository(runtime.neo4j)

    async def memory_insight(self, end_user_id: str, workspace_id: UUID) -> MemoryInsight:
        """读缓存的记忆洞察；用户维度的问题一律走空态（文档 3.4 的 B 类响应）。"""

        user_id = parse_uuid(end_user_id)
        if user_id is None:
            return MemoryInsight.empty("analytics.empty.invalid_insight_id")
        row = await self.end_user_profile.get_memory_insight(user_id, workspace_id)
        if row is None:
            return MemoryInsight.empty("analytics.empty.user_not_found")
        return MemoryInsight.from_row(row)

    async def user_summary(self, end_user_id: str, workspace_id: UUID) -> UserSummary:
        """读缓存的用户摘要；只取摘要列，不捎带洞察那几列长文本。"""

        user_id = parse_uuid(end_user_id)
        if user_id is None:
            return UserSummary.empty("analytics.empty.invalid_user_id")
        row = await self.end_user_profile.get_user_summary(user_id, workspace_id)
        if row is None:
            return UserSummary.empty("analytics.empty.user_not_found")
        return UserSummary.from_row(row)

    async def node_statistics(
        self, end_user_id: str, workspace_id: UUID
    ) -> list[dict[str, Any]]:
        # 只做严格存在性判定（access.exists），不把画像长文本列载入后丢掉；
        # 非法 ID 与用户不存在一律空态全零（文档 3.4 的 B 类响应）。
        user_id = parse_uuid(end_user_id)
        if user_id is None or not await self.end_user.exists_in_workspace(
            user_id, workspace_id
        ):
            return _zero_memory_statistics()
        # Fetch PG source counts, Neo4j counts and short-term memo counts concurrently.
        evaluated_at = datetime.now(UTC)
        source_counts, memory_counts, short_term = await asyncio.gather(
            self._memory_type_source_counts(user_id),
            asyncio.gather(
                safe_count(
                    "implicit habits",
                    lambda: self.memory_statistics.habit_count_for_users([end_user_id]),
                ),
                safe_count("episodic", lambda: self.memory_graph.episodic_count(end_user_id)),
                safe_count(
                    "semantic explicit",
                    lambda: self.memory_graph.semantic_explicit_count(end_user_id),
                ),
                safe_count("emotional", lambda: self.memory_graph.emotional_count(end_user_id)),
            ),
            safe_count(
                "short term",
                lambda: short_term_count(
                    self.memory_search, self.memory_graph, [str(user_id)], evaluated_at
                ),
            ),
        )
        implicit_count, episodic, semantic, emotional = memory_counts
        counts = {
            "PERCEPTUAL_MEMORY": source_counts["perceptual"],
            "WORKING_MEMORY": (
                source_counts["active_conversations"] + source_counts["direct_sources"]
            ),
            "SHORT_TERM_MEMORY": short_term,
            "EXPLICIT_MEMORY": episodic + semantic,
            "IMPLICIT_MEMORY": implicit_count,
            "EMOTIONAL_MEMORY": emotional,
            "EPISODIC_MEMORY": episodic,
            "FORGET_MEMORY": source_counts["forgotten"],
        }
        total = sum(counts.values())
        return [
            {
                "type": key,
                "count": counts[key],
                "percentage": round(counts[key] / total * 100, 2) if total else 0.0,
            }
            for key in MEMORY_TYPES
        ]

    async def _memory_type_source_counts(self, user_id: UUID) -> dict[str, int]:
        """PG 侧的 4 个计数器：优先一条查询取回（单连接、单快照）。

        合并查询失败时回落到逐指标查询，从而保留原先「单个指标失败只把该项降级为 0」
        的语义（见 :func:`safe_count`）；这也是仓储层保留那 4 个单指标方法的原因。
        """

        try:
            return await self.memory_statistics.memory_type_counts(user_id)
        except Exception:
            logger.warning(
                "memory type counts merged query degraded to per-metric queries",
                exc_info=True,
            )
        values = await asyncio.gather(
            safe_count("perceptual", lambda: self.memory_statistics.perceptual_count(user_id)),
            safe_count(
                "active conversations",
                lambda: self.memory_statistics.active_conversation_count(user_id),
            ),
            safe_count(
                "direct sources", lambda: self.memory_statistics.direct_source_count(user_id)
            ),
            safe_count("forgotten", lambda: self.memory_statistics.forgotten_count(user_id)),
        )
        return dict(zip(COUNT_KEYS, values, strict=True))

    # ── 工作空间热门标签 / legacy 终端用户画像 ──────────────────────────

    async def workspace_hot_memory_tags(
        self, workspace_id: UUID, limit: int
    ) -> list[dict[str, Any]]:
        """工作空间热门记忆标签：``end_users.memory_tags`` 跨用户精确匹配合并。

        ``limit <= 0`` 时按 api 语义回落 10（api 的请求参数没有上下界校验）。
        """

        effective_limit = limit if limit > 0 else 10
        try:
            tags = await self.workspace_profile.hot_memory_tags(
                workspace_id, effective_limit, USER_CARD_TAG_MAX_LENGTH
            )
        except Exception as exc:
            logger.error(
                "hot memory tags failed: workspace_id=%s error=%s",
                workspace_id,
                type(exc).__name__,
                exc_info=True,
            )
            raise LocalizedProfileError(
                BizCode.INTERNAL_ERROR,
                "analytics.error.hot_memory_tags_failed",
                detail=str(exc),
                status_code=500,
            ) from exc
        return [{"name": name, "frequency": frequency} for name, frequency in tags]

    async def user_profile(
        self, end_user_id: str | None, workspace_id: UUID
    ) -> dict[str, Any]:
        """legacy 管理端用户画像（名字 + 高频实体），只读。

        api 的调用点不传 ``llm_id``，因此 ``tags`` 恒为空列表；这里保持同一结果，
        不在本服务引入额外的 LLM 调用。``end_user_id`` 为空时保留 api 的全局查询
        语义（管理端前端始终会传该参数）。
        """

        default_name = "默认用户"
        if end_user_id is not None:
            end_user = await self.end_user.resolve_in_workspace(end_user_id, workspace_id)
            if end_user is None:
                return {"name": default_name, "tags": [], "hot_tags": []}
            # api 在用户存在时直接使用 other_name（空串也原样返回），只在用户
            # 不存在时回落「默认用户」。
            display_name = end_user.other_name
        else:
            display_name = default_name

        try:
            hot_tags = await self.user_profile_graph.top_entities(end_user_id, 4)
        except Exception as exc:
            # api 的 get_user_profile 对每个分区单独兜底：热门标签查询失败只丢
            # 这一块，不影响名字返回。
            logger.error(
                "user profile hot tags failed: end_user_id=%s error=%s",
                end_user_id,
                type(exc).__name__,
                exc_info=True,
            )
            hot_tags = []
        return {"name": display_name, "tags": [], "hot_tags": hot_tags}

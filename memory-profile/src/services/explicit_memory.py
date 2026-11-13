"""Explicit-memory read use cases (T07–T10).

Every branch mirrors ``MemoryExplicitService`` + ``memory_explicit_controller``
in the monolith: same fallback strings, same double-total arithmetic, same
error codes/messages.  Business validation failures raise
:class:`ProfileError` carrying the monolith's exact code/message; the module
handler converts them into the legacy ``fail(...)`` envelope (HTTP 200), so
proxied responses stay byte-compatible.  No new ownership checks or list caps
are introduced by this execution-only migration.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.common.errors import BizCode, ProfileError
from src.common.normalization import datetime_to_epoch_ms
from src.repositories.explicit_memory import ExplicitMemoryRepository
from src.runtime import ProfileRuntime
from src.schemas.explicit_memory import (
    EpisodicDetails,
    EpisodicListData,
    EpisodicListItem,
    EpisodicOverviewItem,
    EpisodicPageInfo,
    OverviewData,
    SemanticDetails,
    SemanticMemoryItem,
)

logger = logging.getLogger(__name__)

#: Same enum list and message text as the monolith controller.
VALID_EPISODIC_TYPES = [
    "all",
    "conversation",
    "project_work",
    "learning",
    "decision",
    "important_event",
]


def _parse_timestamp(timestamp_value: Any) -> int | None:
    """Monolith ``MemoryBaseService.parse_timestamp``, same inputs and fallbacks."""

    if not timestamp_value:
        return None
    try:
        if hasattr(timestamp_value, "to_native"):
            return datetime_to_epoch_ms(timestamp_value.to_native())
        if isinstance(timestamp_value, datetime):
            return datetime_to_epoch_ms(timestamp_value)
        if isinstance(timestamp_value, str):
            parsed = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
            return datetime_to_epoch_ms(parsed)
        parsed = datetime.fromisoformat(str(timestamp_value).replace("Z", "+00:00"))
        return datetime_to_epoch_ms(parsed)
    except (ValueError, TypeError, AttributeError):
        logger.warning("无法解析时间戳: %s", timestamp_value)
        return None


def _require_workspace(workspace_id: str | None) -> None:
    """Monolith controller guard: an active workspace is mandatory."""

    if not workspace_id:
        raise ProfileError(
            BizCode.INVALID_PARAMETER,
            "请先切换到一个工作空间",
            detail="current_workspace_id is None",
        )


class ExplicitMemoryService:
    """T07–T10 read use cases; holds data access, owns no transport detail."""

    def __init__(self, runtime: ProfileRuntime) -> None:
        self.repo = ExplicitMemoryRepository(runtime.neo4j)

    # ── T07 overview ─────────────────────────────────────────────────────────

    async def overview(self, *, end_user_id: str, workspace_id: str | None) -> OverviewData:
        _require_workspace(workspace_id)
        episodic_records = await self.repo.episodic_overview_records(end_user_id)
        episodic_memories = [
            EpisodicOverviewItem(
                id=record["id"],
                title=record.get("title") or "未命名",
                content=record.get("content") or "",
                created_at=_parse_timestamp(record.get("created_at")),
            )
            for record in episodic_records
        ]
        semantic_records = await self.repo.semantic_explicit_records(end_user_id)
        semantic_memories = [
            SemanticMemoryItem(
                id=record["id"],
                name=record.get("name") or "未命名",
                entity_type=record.get("entity_type") or "未分类",
                core_definition=record.get("core_definition") or "",
            )
            for record in semantic_records
        ]
        return OverviewData(
            total=len(episodic_memories) + len(semantic_memories),
            episodic_memories=episodic_memories,
            semantic_memories=semantic_memories,
        )

    # ── T08 episodics ────────────────────────────────────────────────────────

    async def episodics_page(
        self,
        *,
        end_user_id: str,
        workspace_id: str | None,
        page: int,
        pagesize: int,
        start_date: int | None,
        end_date: int | None,
        episodic_type: str,
    ) -> EpisodicListData:
        _require_workspace(workspace_id)

        if episodic_type not in VALID_EPISODIC_TYPES:
            raise ProfileError(
                BizCode.INVALID_PARAMETER,
                f"无效的情景类型参数，可选值：{', '.join(VALID_EPISODIC_TYPES)}",
            )
        if (start_date is not None and end_date is None) or (
            end_date is not None and start_date is None
        ):
            raise ProfileError(BizCode.INVALID_PARAMETER, "start_date和end_date必须同时提供")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ProfileError(BizCode.INVALID_PARAMETER, "start_date不能大于end_date")

        total_all = await self.repo.episodic_total(end_user_id)
        where_str, params = self.repo.episodic_where(
            end_user_id, start_date, end_date, episodic_type
        )
        filtered_total = await self.repo.episodic_filtered_count(where_str, params)
        records = await self.repo.episodic_page_records(
            where_str,
            params,
            skip=(page - 1) * pagesize,
            limit=pagesize,
        )
        items = [
            EpisodicListItem(
                id=record["id"],
                title=record.get("title") or "未命名",
                memory_type=record.get("memory_type") or "其他",
                content=record.get("content") or "",
                created_at=_parse_timestamp(record.get("created_at")),
            )
            for record in records
        ]
        return EpisodicListData(
            total=total_all,
            items=items,
            page=EpisodicPageInfo(
                page=page,
                pagesize=pagesize,
                total=filtered_total,
                hasnext=(page * pagesize) < filtered_total,
            ),
        )

    # ── T09 semantics ────────────────────────────────────────────────────────

    async def semantics(
        self, *, end_user_id: str, workspace_id: str | None
    ) -> list[SemanticMemoryItem]:
        _require_workspace(workspace_id)
        records = await self.repo.semantic_explicit_records(end_user_id)
        return [
            SemanticMemoryItem(
                id=record["id"],
                name=record.get("name") or "未命名",
                entity_type=record.get("entity_type") or "未分类",
                core_definition=record.get("core_definition") or "",
            )
            for record in records
        ]

    # ── T10 details ──────────────────────────────────────────────────────────

    async def details(
        self, *, end_user_id: str, memory_id: str, workspace_id: str | None
    ) -> EpisodicDetails | SemanticDetails:
        _require_workspace(workspace_id)
        episodic = await self.repo.episodic_detail_record(end_user_id, memory_id)
        if episodic is not None:
            emotion = await self.repo.episodic_emotion(end_user_id, memory_id)
            return EpisodicDetails(
                memory_type="episodic",
                title=episodic.get("title") or "未命名",
                content=episodic.get("content") or "",
                emotion=emotion,
                created_at=_parse_timestamp(episodic.get("created_at")),
            )
        semantic = await self.repo.semantic_detail_record(end_user_id, memory_id)
        if semantic is not None:
            return SemanticDetails(
                memory_type="semantic",
                name=semantic.get("name") or "未命名",
                core_definition=semantic.get("core_definition") or "",
                detailed_notes=semantic.get("detailed_notes") or "",
                created_at=_parse_timestamp(semantic.get("created_at")),
            )
        logger.warning("记忆不存在: memory_id=%s", memory_id)
        raise ProfileError(
            BizCode.INVALID_PARAMETER,
            "显性记忆不存在",
            detail=f"记忆不存在: memory_id={memory_id}",
        )

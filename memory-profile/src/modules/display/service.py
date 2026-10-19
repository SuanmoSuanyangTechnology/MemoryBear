"""Display domain service layer."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.common.errors import BizCode, ProfileError
from src.modules.display.engine_cards import build_cards_from_groups
from src.modules.display.repository.memory_display import MemoryDisplayRepository
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)

ALL_ITEM_LIMIT = 1000


def parse_timestamp_to_utc_naive(ts_ms: int | None) -> datetime | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).replace(tzinfo=None)


def to_timestamp_ms(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


class MemoryDisplayService:
    def __init__(
        self,
        runtime: ProfileRuntime | None = None,
        *,
        repository: MemoryDisplayRepository | None = None,
    ) -> None:
        if repository is not None:
            self.repository = repository
        elif runtime is not None:
            self.repository = MemoryDisplayRepository(runtime.postgres)
        else:
            raise ValueError("Either runtime or repository must be provided")

    async def _ensure_user_in_workspace(
        self, end_user_id: UUID | None, workspace_id: UUID
    ) -> None:
        if end_user_id is None:
            return
        is_member = await self.repository.check_user_in_workspace(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )
        if not is_member:
            raise ProfileError(
                BizCode.USER_NOT_FOUND,
                "终端用户不存在",
                detail="end_user not found in current workspace",
                status_code=404,
            )

    async def query_written(
        self,
        *,
        workspace_id: UUID,
        page: int,
        pagesize: int,
        end_user_id: UUID | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._ensure_user_in_workspace(end_user_id, workspace_id)
        records, total = await self.repository.query_written_paginated(
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=end_user_id,
            start_time=parse_timestamp_to_utc_naive(start_time),
            end_time=parse_timestamp_to_utc_naive(end_time),
        )

        items = [
            {
                "id": str(record["id"]),
                "end_user_id": str(record["end_user_id"]),
                "memory_id": record["memory_id"],
                "memory_type": record["memory_type"],
                "name": record["name"],
                "content": record["content"],
                "occurred_at": to_timestamp_ms(record["occurred_at"]),
            }
            for record in records
        ]
        return items, total

    async def query_retrieved(
        self,
        *,
        workspace_id: UUID,
        page: int,
        pagesize: int,
        end_user_id: UUID | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._ensure_user_in_workspace(end_user_id, workspace_id)
        records, total = await self.repository.query_retrieved_paginated(
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=end_user_id,
            start_time=parse_timestamp_to_utc_naive(start_time),
            end_time=parse_timestamp_to_utc_naive(end_time),
        )

        items = [
            {
                "id": str(record["id"]),
                "end_user_id": str(record["end_user_id"]),
                "search_mode": record["search_mode"],
                "query": record["query"] or "",
                "content": record["content"],
                "occurred_at": to_timestamp_ms(record["occurred_at"]),
            }
            for record in records
        ]
        return items, total

    async def query_engines(
        self,
        *,
        workspace_id: UUID,
        timezone: str,
        language: str,
        page: int,
        pagesize: int,
        end_user_id: UUID | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._ensure_user_in_workspace(end_user_id, workspace_id)
        groups, total = await self.repository.query_engine_groups_paginated(
            workspace_id=workspace_id,
            timezone=timezone,
            page=page,
            pagesize=pagesize,
            end_user_id=end_user_id,
            start_time=parse_timestamp_to_utc_naive(start_time),
            end_time=parse_timestamp_to_utc_naive(end_time),
        )
        cards = build_cards_from_groups(
            groups=groups,
            end_user_id=str(end_user_id) if end_user_id is not None else None,
            timezone=timezone,
            language=language,
        )
        return cards, total

    async def query_all(
        self,
        *,
        workspace_id: UUID,
        timezone: str,
        language: str,
        page: int,
        pagesize: int,
        end_user_id: UUID | None = None,
        include_engines: bool = True,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._ensure_user_in_workspace(end_user_id, workspace_id)

        written_result = await self.query_written(
            workspace_id=workspace_id,
            page=1,
            pagesize=ALL_ITEM_LIMIT,
            end_user_id=end_user_id,
            start_time=start_time,
            end_time=end_time,
        )
        retrieved_result = await self.query_retrieved(
            workspace_id=workspace_id,
            page=1,
            pagesize=ALL_ITEM_LIMIT,
            end_user_id=end_user_id,
            start_time=start_time,
            end_time=end_time,
        )

        blocks: dict[str, tuple[list[dict[str, Any]], int]] = {
            "written": written_result,
            "retrieved": retrieved_result,
        }

        if include_engines:
            engines_result = await self.query_engines(
                workspace_id=workspace_id,
                timezone=timezone,
                language=language,
                page=1,
                pagesize=ALL_ITEM_LIMIT,
                end_user_id=end_user_id,
                start_time=start_time,
                end_time=end_time,
            )
            blocks["engines"] = engines_result

        merged_items: list[dict[str, Any]] = []
        total = 0
        for source, (items, block_total) in blocks.items():
            total += block_total
            merged_items.extend({**item, "source": source} for item in items)
            if block_total > len(items):
                logger.warning(
                    "展示记录聚合查询被截断: end_user_id=%s, source=%s, "
                    "fetched=%s, total=%s, limit=%s",
                    end_user_id,
                    source,
                    len(items),
                    block_total,
                    ALL_ITEM_LIMIT,
                )

        merged_items.sort(
            key=lambda item: (
                -(item.get("occurred_at") or 0),
                item["source"],
                str(item.get("id") or ""),
            )
        )

        offset = (page - 1) * pagesize
        return merged_items[offset : offset + pagesize], total

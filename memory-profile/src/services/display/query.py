"""Display domain service layer."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import TypeAdapter

from src.common.normalization import datetime_to_epoch_ms, epoch_ms_to_datetime
from src.repositories.display import MemoryDisplayRepository
from src.schemas.display import AllItem, EngineItem, RetrievedItem, WrittenItem
from src.services.display.engine_cards import build_cards_from_groups
from src.services.end_user import EndUserService

if TYPE_CHECKING:
    from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)

ALL_ITEM_LIMIT = 1000
_ALL_ITEM_ADAPTER = TypeAdapter(AllItem)


class MemoryDisplayService:
    def __init__(
        self,
        runtime: ProfileRuntime | None = None,
        *,
        repository: MemoryDisplayRepository | None = None,
        end_users: EndUserService | None = None,
    ) -> None:
        if repository is not None:
            self.repository = repository
        elif runtime is not None:
            self.repository = MemoryDisplayRepository(runtime.postgres)
        else:
            raise ValueError("Either runtime or repository must be provided")
        if end_users is not None:
            self.end_users = end_users
        elif runtime is not None:
            self.end_users = EndUserService.from_postgres(runtime.postgres)
        else:
            raise ValueError("Either runtime or end_users must be provided")

    async def _ensure_user_in_workspace(self, end_user_id: UUID | None, workspace_id: UUID) -> None:
        if end_user_id is None:
            return
        await self.end_users.require_in_workspace(
            end_user_id=end_user_id,
            workspace_id=workspace_id,
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
    ) -> tuple[list[WrittenItem], int]:
        await self._ensure_user_in_workspace(end_user_id, workspace_id)
        return await self._load_written(
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=end_user_id,
            start_time=start_time,
            end_time=end_time,
        )

    async def _load_written(
        self,
        *,
        workspace_id: UUID,
        page: int,
        pagesize: int,
        end_user_id: UUID | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> tuple[list[WrittenItem], int]:
        records, total = await self.repository.query_written_paginated(
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=end_user_id,
            start_time=epoch_ms_to_datetime(start_time),
            end_time=epoch_ms_to_datetime(end_time),
        )

        items = [
            WrittenItem.model_validate(
                {
                    "id": str(record["id"]),
                    "end_user_id": str(record["end_user_id"]),
                    "memory_id": record["memory_id"],
                    "memory_type": record["memory_type"],
                    "name": record["name"],
                    "content": record["content"],
                    "occurred_at": datetime_to_epoch_ms(record["occurred_at"]),
                }
            )
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
    ) -> tuple[list[RetrievedItem], int]:
        await self._ensure_user_in_workspace(end_user_id, workspace_id)
        return await self._load_retrieved(
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=end_user_id,
            start_time=start_time,
            end_time=end_time,
        )

    async def _load_retrieved(
        self,
        *,
        workspace_id: UUID,
        page: int,
        pagesize: int,
        end_user_id: UUID | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> tuple[list[RetrievedItem], int]:
        records, total = await self.repository.query_retrieved_paginated(
            workspace_id=workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=end_user_id,
            start_time=epoch_ms_to_datetime(start_time),
            end_time=epoch_ms_to_datetime(end_time),
        )

        items = [
            RetrievedItem.model_validate(
                {
                    "id": str(record["id"]),
                    "end_user_id": str(record["end_user_id"]),
                    "search_mode": record["search_mode"],
                    "query": record["query"] or "",
                    "content": record["content"],
                    "occurred_at": datetime_to_epoch_ms(record["occurred_at"]),
                }
            )
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
    ) -> tuple[list[EngineItem], int]:
        await self._ensure_user_in_workspace(end_user_id, workspace_id)
        return await self._load_engines(
            workspace_id=workspace_id,
            timezone=timezone,
            language=language,
            page=page,
            pagesize=pagesize,
            end_user_id=end_user_id,
            start_time=start_time,
            end_time=end_time,
        )

    async def _load_engines(
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
    ) -> tuple[list[EngineItem], int]:
        groups, total = await self.repository.query_engine_groups_paginated(
            workspace_id=workspace_id,
            timezone=timezone,
            page=page,
            pagesize=pagesize,
            end_user_id=end_user_id,
            start_time=epoch_ms_to_datetime(start_time),
            end_time=epoch_ms_to_datetime(end_time),
        )
        cards = build_cards_from_groups(
            groups=groups,
            end_user_id=str(end_user_id) if end_user_id is not None else None,
            timezone=timezone,
            language=language,
        )
        return [EngineItem.model_validate(card) for card in cards], total

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
    ) -> tuple[list[AllItem], int]:
        await self._ensure_user_in_workspace(end_user_id, workspace_id)

        written_result = await self._load_written(
            workspace_id=workspace_id,
            page=1,
            pagesize=ALL_ITEM_LIMIT,
            end_user_id=end_user_id,
            start_time=start_time,
            end_time=end_time,
        )
        retrieved_result = await self._load_retrieved(
            workspace_id=workspace_id,
            page=1,
            pagesize=ALL_ITEM_LIMIT,
            end_user_id=end_user_id,
            start_time=start_time,
            end_time=end_time,
        )

        blocks: dict[str, tuple[Sequence[WrittenItem | RetrievedItem | EngineItem], int]] = {
            "written": written_result,
            "retrieved": retrieved_result,
        }

        if include_engines:
            engines_result = await self._load_engines(
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
            merged_items.extend({**item.model_dump(), "source": source} for item in items)
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
        return [
            _ALL_ITEM_ADAPTER.validate_python(item)
            for item in merged_items[offset : offset + pagesize]
        ], total

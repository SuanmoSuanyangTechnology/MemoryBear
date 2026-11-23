"""Episodic-memory use cases; all reads stay within the caller's workspace."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID

from src.common.errors import BizCode, ProfileError
from src.common.normalization import datetime_to_epoch_ms
from src.repositories.episodic_memory import EpisodicMemoryRepository
from src.runtime import ProfileRuntime
from src.schemas.episodic_memory import DetailsData, OverviewData
from src.services.end_user import EndUserService


def _community(row: dict[str, Any]) -> dict[str, Any]:
    category = row.get("category_l1")
    if not isinstance(category, str) or not category.strip():
        raise RuntimeError("SceneCommunity.category_l1 is missing")
    result = {
        key: row.get(key)
        for key in ("id", "topic_name", "topic_scope", "category_l1", "scene_count")
    }
    for key in ("created_at", "updated_at"):
        value = row.get(key)
        if hasattr(value, "to_native"):
            value = value.to_native()
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        result[key] = value if isinstance(value, int) else datetime_to_epoch_ms(value)
    return result


class EpisodicMemoryService:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.repo = EpisodicMemoryRepository(
            runtime.elasticsearch, runtime.neo4j, runtime.settings.memory_read_backend
        )
        self.users = EndUserService.from_postgres(runtime.postgres)

    async def _authorize(self, end_user_id: str, workspace_id: UUID) -> UUID:
        try:
            user_id = UUID(end_user_id)
        except ValueError as exc:
            raise ProfileError(BizCode.INVALID_PARAMETER, "无效终端用户 ID") from exc
        if not await self.users.exists_in_workspace(user_id, workspace_id):
            raise ProfileError(BizCode.USER_NOT_FOUND, "终端用户不存在或不可访问")
        return user_id

    async def overview(
        self,
        workspace_id: UUID,
        end_user_id: str,
        category_l1: str | None,
        keyword: str,
        sort: str,
        page: int,
        pagesize: int,
    ) -> OverviewData:
        user_id = await self._authorize(end_user_id, workspace_id)
        async with asyncio.timeout(5):
            result = await self.repo.overview(
                str(user_id), category_l1, keyword, sort, page, pagesize
            )
        result["items"] = [_community(row) for row in result["items"]]
        return OverviewData.model_validate(result)

    async def details(
        self, workspace_id: UUID, end_user_id: str, community_id: str, page: int, pagesize: int
    ) -> DetailsData:
        user_id = await self._authorize(end_user_id, workspace_id)
        async with asyncio.timeout(5):
            row = await self.repo.details(str(user_id), community_id, page, pagesize)
        result = _community(row)
        result.update(summary=row.get("summary") or "", page=row["page"], items=row["items"])
        return DetailsData.model_validate(result)

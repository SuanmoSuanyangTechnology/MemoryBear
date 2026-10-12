"""Shared persistence service for SceneCommunity configuration APIs."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory_config_model import MemoryConfig
from app.schemas.scene_community_schema import (
    SceneCommunityConfig,
    SceneCommunityConfigUpdate,
)
from app.utils.redis_cache import invalidate_cache


class SceneCommunityConfigService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(
        self, config_id: UUID, workspace_id: UUID | None
    ) -> SceneCommunityConfig | None:
        row = await self.db.get(MemoryConfig, config_id)
        if (
            row is None
            or row.workspace_id is None
            or workspace_id is None
            or str(row.workspace_id) != str(workspace_id)
        ):
            return None
        return SceneCommunityConfig.model_validate(row)

    async def update(
        self,
        payload: SceneCommunityConfigUpdate,
        workspace_id: UUID | None,
    ) -> SceneCommunityConfig | None:
        row = await self.db.get(MemoryConfig, payload.config_id)
        if (
            row is None
            or row.workspace_id is None
            or workspace_id is None
            or str(row.workspace_id) != str(workspace_id)
        ):
            return None

        for field, value in payload.model_dump(exclude={"config_id"}).items():
            setattr(row, field, value)
        await self.db.commit()
        await self.db.refresh(row)
        result = SceneCommunityConfig.model_validate(row)
        await invalidate_cache(prefix=f"memory_config:{payload.config_id}")
        return result

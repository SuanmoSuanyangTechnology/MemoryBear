"""Application service for personality-anchor configuration."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.memory_config_repository import MemoryConfigRepository
from app.schemas.personality_anchor_config_schema import (
    PersonalityAnchorConfig,
    PersonalityAnchorConfigResponse,
)


class PersonalityAnchorConfigService:
    """Read and replace settings within the current workspace."""

    def __init__(self, db: AsyncSession) -> None:
        self._repository = MemoryConfigRepository(db)

    async def get(self, config_id: UUID, workspace_id: UUID) -> dict[str, Any] | None:
        config = await self._repository.get_personality_anchor_config_async(
            config_id, workspace_id,
        )
        if config is None:
            return None
        return PersonalityAnchorConfigResponse.model_validate(config).model_dump(mode="json")

    async def update(
        self, config_id: UUID, workspace_id: UUID, values: dict[str, Any],
    ) -> dict[str, Any] | None:
        settings = PersonalityAnchorConfig.model_validate(values)
        config = await self._repository.update_personality_anchor_config_async(
            config_id, workspace_id, settings.model_dump(),
        )
        if config is None:
            return None
        return PersonalityAnchorConfigResponse.model_validate(config).model_dump(mode="json")

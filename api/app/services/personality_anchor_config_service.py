"""Application service for personality-anchor configuration."""

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory_config_model import MemoryConfig
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.schemas.personality_anchor_config_schema import (
    PersonalityAnchorConfig,
    PersonalityAnchorConfigResponse,
)


DEFAULT_PERSONALITY_ANCHOR_BASE_PRINCIPLES = (
    "你是小熊，用户的长期 AI 伙伴。保持坦诚、温和与独立判断，"
    "尊重用户自主选择及隐私，不编造事实或未执行的行动。"
)


class PersonalityAnchorConfigService:
    """Read and replace settings within the current workspace."""

    def __init__(self, db: AsyncSession) -> None:
        self._repository = MemoryConfigRepository(db)

    @staticmethod
    def serialize(config: MemoryConfig) -> dict[str, Any]:
        """Apply response defaults, including the prompt for NULL or empty text, without writes."""
        values = {}
        for field in PersonalityAnchorConfig.model_fields:
            value = getattr(config, field)
            if field == "personality_anchor_base_principles" and value in (None, ""):
                value = DEFAULT_PERSONALITY_ANCHOR_BASE_PRINCIPLES
            elif value is None:
                value = MemoryConfig.__table__.columns[field].default.arg
            values[field] = value
        return PersonalityAnchorConfigResponse(
            config_id=config.config_id,
            is_default=bool(config.is_default),
            **values,
        ).model_dump(mode="json")

    async def get(self, config_id: UUID, workspace_id: UUID) -> dict[str, Any] | None:
        config = await self._repository.get_personality_anchor_config_async(
            config_id, workspace_id,
        )
        if config is None:
            return None
        return self.serialize(config)

    async def update(
        self, config_id: UUID, workspace_id: UUID, values: dict[str, Any],
    ) -> dict[str, Any] | None:
        settings = PersonalityAnchorConfig.model_validate(values)
        config = await self._repository.update_personality_anchor_config_async(
            config_id, workspace_id, settings.model_dump(),
        )
        if config is None:
            return None
        return self.serialize(config)

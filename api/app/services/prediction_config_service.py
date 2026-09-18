"""Application service for prediction-engine configuration."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory_config_model import MemoryConfig
from app.repositories.memory_config_repository import MemoryConfigRepository


_PREDICTION_FIELDS = (
    "prediction_candidate_limit",
    "prediction_participant_limit",
    "prediction_max_steps",
    "prediction_recall_limit",
    "prediction_min_valid_memory_count",
    "prediction_embedding_min_similarity",
)


class PredictionConfigService:
    """Read and update prediction settings within a workspace boundary."""

    def __init__(self, db: AsyncSession) -> None:
        """Initialize the service with an asynchronous database session."""
        self._repository = MemoryConfigRepository(db)

    @staticmethod
    def serialize(config: MemoryConfig) -> dict[str, Any]:
        """Return the minimal prediction configuration response contract."""
        return {
            "config_id": str(config.config_id),
            "is_default": bool(config.is_default),
            **{field: getattr(config, field) for field in _PREDICTION_FIELDS},
        }

    async def get(
        self, config_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> dict[str, Any] | None:
        """Read one configuration only when it belongs to the active workspace."""
        config = await self._repository.get_by_id_async(config_id)
        if config is None or str(config.workspace_id) != str(workspace_id):
            return None
        return self.serialize(config)

    async def update(
        self,
        config_id: uuid.UUID,
        workspace_id: uuid.UUID,
        values: dict[str, int | float],
    ) -> dict[str, Any] | None:
        """Replace prediction settings and return the complete saved contract."""
        config = await self._repository.get_by_id_async(config_id)
        if config is None or str(config.workspace_id) != str(workspace_id):
            return None
        config = await self._repository.update_prediction_config_async(
            config_id, values
        )
        assert config is not None
        return self.serialize(config)

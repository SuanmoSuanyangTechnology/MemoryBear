"""LLM-backed legacy metadata migrator for the end-user merge.

It resolves the same model the API uses (``memory_config.reflection_model_id``,
falling back to the LLM slot), renders the shared migration prompt and returns
the migrated ``meta_data_new`` payload. The merge service owns the lock and the
CAS write; this adapter only performs the model call.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import sqlalchemy as sa

from src.config import ProfileSettings
from src.core.domain.metadata_migration import prepare_migration_metadata
from src.infrastructure.model_service import ModelServiceManager
from src.infrastructure.postgres import PostgresManager
from src.models.references import Workspace
from src.models.references.end_user_model import EndUser
from src.repositories.model_config import ModelConfigRepository
from src.services.model_config import ModelConfigService, ModelSlot

logger = logging.getLogger(__name__)


class MetadataModelUnavailable(RuntimeError):
    """No usable reflection/LLM model could be resolved for the user's workspace."""


class LlmMetadataMigrator:
    """Port implementation: ``(end_user_id, legacy_meta) -> migrated meta``."""

    def __init__(
        self,
        *,
        postgres: PostgresManager,
        model_service: ModelServiceManager,
        settings: ProfileSettings,
    ) -> None:
        self._postgres = postgres
        self._model_service = model_service
        self._settings = settings

    async def __call__(
        self, end_user_id: uuid.UUID, original: object
    ) -> dict[str, object]:
        chat_model = await self._chat_model(end_user_id)
        try:
            merged, applied, rejected = await asyncio.wait_for(
                prepare_migration_metadata(str(end_user_id), original, chat_model),
                timeout=self._settings.metadata_migration_timeout_seconds,
            )
        except TimeoutError as exc:
            raise MetadataModelUnavailable(
                f"metadata migration timed out after "
                f"{self._settings.metadata_migration_timeout_seconds}s "
                f"for end user {end_user_id}"
            ) from exc
        logger.info(
            "[merge_end_users] metadata 补迁完成: user=%s applied=%s rejected=%s",
            end_user_id,
            applied,
            rejected,
        )
        return merged

    async def _chat_model(self, end_user_id: uuid.UUID) -> Any:
        """Resolve the workspace's LLM slot (default-config spaces use the preset)."""

        async with self._postgres.session() as session:
            row = (
                await session.execute(
                    sa.select(
                        Workspace.id,
                        Workspace.tenant_id,
                        Workspace.memory_config,
                        Workspace.is_default_config,
                        Workspace.llm,
                    )
                    .join(EndUser, EndUser.workspace_id == Workspace.id)
                    .where(EndUser.id == end_user_id)
                )
            ).mappings().first()

        if row is None or row["tenant_id"] is None:
            raise MetadataModelUnavailable(
                f"workspace/tenant not found for end user {end_user_id}"
            )

        resolver = ModelConfigService(
            ModelConfigRepository(self._postgres), self._model_service
        )
        resolved = await resolver.resolve_workspace(
            {
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "memory_config": row["memory_config"],
                "is_default_config": row["is_default_config"],
                "llm": row["llm"],
            },
            ModelSlot.LLM,
        )
        return await self._model_service.chat_model(
            resolved.model_config_id, resolved.tenant_id
        )


__all__ = ["LlmMetadataMigrator", "MetadataModelUnavailable"]

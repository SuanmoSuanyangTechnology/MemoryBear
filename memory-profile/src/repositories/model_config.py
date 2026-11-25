"""Read-only model-configuration lookups behind :class:`ModelConfigService`.

Read-only: this repository only selects, so it is listed with the other
read-only PostgreSQL repositories in the boundary script.

Resolution is one statement: the workspace row is already in memory, so the
config check, the effective model id and the ``model_configs`` row are fetched
together instead of in three sessions. Every model slot — ``llm``,
``embedding``, ``rerank``, ``vision``, ``audio``, ``video`` — rides the same
statement; the slot only selects which preset column is read.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import or_, select
from sqlalchemy.dialects import postgresql

from src.infrastructure.postgres import PostgresManager
from src.models.references import MemoryConfigRow, ModelConfig, WorkspaceDefaultModelPreset

#: api ``workspace_service.DEFAULT_PRESET_KEY``。
DEFAULT_PRESET_KEY = "default"

#: Preset column per workspace model slot (api ``_effective_workspace_models``).
_PRESET_COLUMNS = {
    "llm": WorkspaceDefaultModelPreset.llm_model_config_id,
    "embedding": WorkspaceDefaultModelPreset.embedding_model_config_id,
    "rerank": WorkspaceDefaultModelPreset.rerank_model_config_id,
    "vision": WorkspaceDefaultModelPreset.vision_model_config_id,
    "audio": WorkspaceDefaultModelPreset.audio_model_config_id,
    "video": WorkspaceDefaultModelPreset.video_model_config_id,
}


def _uuid_literal(value: UUID | None) -> sa.ColumnElement[Any]:
    """A bound UUID (or ``NULL``) safe to compare/join against ``model_configs.id``."""

    return sa.cast(sa.literal(value), postgresql.UUID(as_uuid=True))


class ModelConfigRepository:
    """Resolve one workspace model slot in a single round trip."""

    def __init__(self, postgres: PostgresManager) -> None:
        self.postgres = postgres

    async def get_models(
        self, model_ids: Iterable[UUID], tenant_id: UUID | None = None
    ) -> dict[UUID, dict[str, Any]]:
        """Load every requested ``model_configs`` row in one statement.

        Same visibility rule as the API repository: the tenant's own rows plus
        public ones. Ids are de-duplicated; ids that do not resolve are simply
        absent from the result. The probe path only needs identity, name and
        active state.
        """

        ids = list(dict.fromkeys(model_ids))
        if not ids:
            return {}
        statement = select(
            ModelConfig.id,
            ModelConfig.tenant_id,
            ModelConfig.name,
            ModelConfig.is_active,
        ).where(ModelConfig.id.in_(ids))
        if tenant_id:
            statement = statement.where(
                or_(
                    ModelConfig.tenant_id == tenant_id,
                    ModelConfig.is_public.is_(True),
                )
            )
        async with self.postgres.session() as session:
            rows = (await session.execute(statement)).mappings().all()
        return {row["id"]: dict(row) for row in rows}

    async def resolve_model(
        self,
        *,
        config_id: UUID,
        slot: str,
        is_default_config: bool,
        workspace_model_id: UUID | None,
    ) -> dict[str, Any] | None:
        """Fetch config existence, the effective model id and its ``model_configs`` row.

        ``slot`` chooses the preset column; callers pass a :class:`ModelSlot`
        value from the service, never user input. ``workspace_model_id`` is the
        workspace row's own column already parsed, used when the workspace is
        not default-config (or its preset column is empty).

        The returned mapping always has one row (the driver-side values are
        joined from a one-row subquery), so ``config_exists`` distinguishes a
        missing config from a missing model.
        """

        preset_model_id = (
            select(_PRESET_COLUMNS[slot])
            .where(WorkspaceDefaultModelPreset.singleton_key == DEFAULT_PRESET_KEY)
            .limit(1)
            .scalar_subquery()
        )
        effective_model_id = _uuid_literal(workspace_model_id)
        if is_default_config:
            effective_model_id = sa.func.coalesce(preset_model_id, effective_model_id)

        config_exists = sa.exists(
            select(1)
            .select_from(MemoryConfigRow)
            .where(
                MemoryConfigRow.config_id == config_id,
                MemoryConfigRow.workspace_id.is_not(None),
            )
        )
        effective = select(effective_model_id.label("model_id")).subquery()
        statement = (
            select(
                effective.c.model_id,
                config_exists.label("config_exists"),
                ModelConfig.id.label("model_row_id"),
                ModelConfig.tenant_id,
                ModelConfig.is_active,
                ModelConfig.is_public,
            )
            .select_from(effective)
            .outerjoin(ModelConfig, ModelConfig.id == effective.c.model_id)
        )
        async with self.postgres.session() as session:
            row = (await session.execute(statement)).mappings().one_or_none()
        return dict(row) if row is not None else None

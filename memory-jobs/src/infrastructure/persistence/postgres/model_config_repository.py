"""SQLAlchemy implementation of the model-configuration repository.

The statement is the one the service has always used: the effective model id is
the workspace's own column, falling back to the singleton preset for a
default-config workspace, and one outer join brings back the model row's
tenant/active/public flags.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from src.domain.model_config.model import ModelConfigRow
from src.domain.shared.ids import ModelConfigId
from src.infrastructure.persistence.postgres.mappers import to_model_config_row
from src.models import MemoryConfigRow, ModelConfig, WorkspaceDefaultModelPreset

#: api ``workspace_service.DEFAULT_PRESET_KEY``.
DEFAULT_PRESET_KEY = "default"


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


class SqlAlchemyModelConfigRepository:
    """Resolve a model slot inside the Unit of Work's current transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def resolve_model(
        self,
        *,
        config_id: ModelConfigId,
        slot: str,
        is_default_config: bool,
        workspace_model_id: UUID | None,
    ) -> ModelConfigRow | None:
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
                MemoryConfigRow.config_id == config_id.value,
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
        row = self._session.execute(statement).mappings().one_or_none()
        return to_model_config_row(row)

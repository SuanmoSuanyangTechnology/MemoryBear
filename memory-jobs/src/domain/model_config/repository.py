"""Repository interface for model-configuration resolution."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from src.domain.model_config.model import ModelConfigRow
from src.domain.shared.ids import ModelConfigId


class ModelConfigRepository(Protocol):
    """Fetch the row that decides which model a slot resolves to."""

    def resolve_model(
        self,
        *,
        config_id: ModelConfigId,
        slot: str,
        is_default_config: bool,
        workspace_model_id: UUID | None,
    ) -> ModelConfigRow | None: ...

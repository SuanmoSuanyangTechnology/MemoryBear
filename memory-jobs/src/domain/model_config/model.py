"""Model configuration value objects.

The rows belong to the API's model registry; this context only carries the
facts needed to resolve a workspace's LLM. Mapping the rows into these objects
is the persistence adapter's job (anti-corruption layer).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from src.domain.shared.ids import ModelConfigId, TenantId


@dataclass(frozen=True, slots=True)
class ModelConfigRow:
    """A resolved ``memory_config`` row plus the model row it points at."""

    model_id: ModelConfigId | None
    config_exists: bool
    model_row_id: UUID | None
    tenant_id: TenantId | None
    is_active: bool
    is_public: bool


@dataclass(frozen=True, slots=True)
class ResolvedChatModel:
    """The configuration id and tenant a blocking chat call is made for."""

    config_id: ModelConfigId
    tenant_id: TenantId

"""Repository ports implemented by each consuming service."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from .contracts import (
    ModelConfigSnapshot,
    ModelKeySnapshot,
    ModelProvider,
    PublicModelBindingSnapshot,
)


class ModelRegistryRepository(Protocol):
    def get_model_config(
        self,
        model_config_id: UUID,
        tenant_id: UUID,
    ) -> ModelConfigSnapshot | None: ...

    def list_active_keys(
        self,
        model_config_id: UUID,
    ) -> Sequence[ModelKeySnapshot]: ...

    def get_public_binding(
        self,
        tenant_id: UUID,
        provider: ModelProvider,
    ) -> PublicModelBindingSnapshot | None: ...

    def record_key_usage(self, key_id: UUID) -> None: ...


class AsyncModelRegistryRepository(Protocol):
    async def get_model_config(
        self,
        model_config_id: UUID,
        tenant_id: UUID,
    ) -> ModelConfigSnapshot | None: ...

    async def list_active_keys(
        self,
        model_config_id: UUID,
    ) -> Sequence[ModelKeySnapshot]: ...

    async def get_public_binding(
        self,
        tenant_id: UUID,
        provider: ModelProvider,
    ) -> PublicModelBindingSnapshot | None: ...

    async def record_key_usage(self, key_id: UUID) -> None: ...

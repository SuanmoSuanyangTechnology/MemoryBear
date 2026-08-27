"""Read-only SQL adapter for the shared RedBear model resolver."""

from __future__ import annotations

import uuid

from pydantic import SecretStr
from redbear_model import (
    LoadBalanceStrategy,
    ModelCapability,
    ModelConfigSnapshot,
    ModelKeySnapshot,
    ModelProvider,
    ModelRegistryRepository,
    ModelType,
    PublicModelBindingSnapshot,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.references import ModelApiKey, ModelConfig
from ..models.references.model_registry import model_config_api_key_association
from ..utils.datetime_utils import to_timestamp_ms


def _capabilities(values: list[str] | None) -> tuple[ModelCapability, ...]:
    result = []
    for value in values or []:
        try:
            result.append(ModelCapability(value))
        except ValueError:
            continue
    return tuple(result)


def _config_snapshot(config: ModelConfig) -> ModelConfigSnapshot:
    return ModelConfigSnapshot(
        model_config_id=config.id,
        tenant_id=config.tenant_id,
        provider=ModelProvider(config.provider),
        model_type=ModelType(config.type),
        display_name=config.name,
        is_active=config.is_active,
        is_public=config.is_public,
        load_balance_strategy=LoadBalanceStrategy(
            config.load_balance_strategy or LoadBalanceStrategy.NONE
        ),
        capabilities=_capabilities(config.capability),
        is_omni=config.is_omni,
        config=dict(config.config or {}),
    )


def _key_snapshot(key: ModelApiKey) -> ModelKeySnapshot:
    return ModelKeySnapshot(
        key_id=key.id,
        model_name=key.model_name,
        provider=ModelProvider(key.provider),
        api_key=SecretStr(key.api_key),
        base_url=key.api_base,
        is_active=key.is_active,
        priority=key.priority or "1",
        usage_count=int(key.usage_count or "0"),
        last_used_at_ms=to_timestamp_ms(key.last_used_at),
        capabilities=_capabilities(key.capability),
        is_omni=key.is_omni,
        config=dict(key.config or {}),
    )


def _active_keys_query(model_config_id: uuid.UUID):
    return (
        select(ModelApiKey)
        .join(
            model_config_api_key_association,
            model_config_api_key_association.c.api_key_id == ModelApiKey.id,
        )
        .where(
            model_config_api_key_association.c.model_config_id == model_config_id,
            ModelApiKey.is_active.is_(True),
        )
        .order_by(model_config_api_key_association.c.created_at.asc())
    )


class SyncSQLModelRegistry(ModelRegistryRepository):
    """Expose Platform model rows to synchronous worker task code."""

    def __init__(self, db: Session):
        self.db = db

    def get_model_config(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> ModelConfigSnapshot | None:
        del tenant_id
        result = self.db.execute(
            select(ModelConfig).where(ModelConfig.id == model_config_id)
        )
        config = result.scalars().first()
        return None if config is None else _config_snapshot(config)

    def list_active_keys(
        self,
        model_config_id: uuid.UUID,
    ) -> list[ModelKeySnapshot]:
        result = self.db.execute(_active_keys_query(model_config_id))
        return [_key_snapshot(key) for key in result.scalars().all()]

    def get_public_binding(
        self,
        tenant_id: uuid.UUID,
        provider: ModelProvider,
    ) -> PublicModelBindingSnapshot | None:
        del tenant_id, provider
        return None

    def record_key_usage(self, key_id: uuid.UUID) -> None:
        del key_id
        raise RuntimeError("Knowledge reference repositories are read-only")


class AsyncSQLModelRegistry:
    """Expose Platform model rows as immutable scalar snapshots."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_model_config(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> ModelConfigSnapshot | None:
        del tenant_id
        result = await self.db.execute(
            select(ModelConfig).where(ModelConfig.id == model_config_id)
        )
        config = result.scalars().first()
        if config is None:
            return None
        return _config_snapshot(config)

    async def list_active_keys(
        self,
        model_config_id: uuid.UUID,
    ) -> list[ModelKeySnapshot]:
        result = await self.db.execute(_active_keys_query(model_config_id))
        keys = list(result.scalars().all())
        return [_key_snapshot(key) for key in keys]

    async def get_public_binding(
        self,
        tenant_id: uuid.UUID,
        provider: ModelProvider,
    ) -> PublicModelBindingSnapshot | None:
        del tenant_id, provider
        return None

    async def record_key_usage(self, key_id: uuid.UUID) -> None:
        del key_id
        raise RuntimeError("Knowledge reference repositories are read-only")


__all__ = ["AsyncSQLModelRegistry", "SyncSQLModelRegistry"]

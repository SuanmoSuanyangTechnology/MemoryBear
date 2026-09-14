"""Read-only SQL adapter for the shared RedBear model resolver."""

from __future__ import annotations

import base64
import uuid

from pydantic import SecretStr
from redbear_model import (
    ChannelSnapshot,
    LoadBalanceStrategy,
    ModelCapability,
    ModelConfigSnapshot,
    ModelKeySnapshot,
    ModelProvider,
    ModelRegistryRepository,
    ModelType,
    PublicModelBindingSnapshot,
)
from redbear_model.crypto import AESGCMEnvCipher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..bootstrap import get_settings
from ..models.references import ModelApiKey, ModelChannel, ModelConfig
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
        name=config.name,
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


def _platform_channel_query(tenant_id: uuid.UUID):
    """平台代管 speedbear 渠道（仅 source=platform 且活跃行，最早登记优先）。"""
    return (
        select(ModelChannel)
        .where(
            ModelChannel.tenant_id == tenant_id,
            ModelChannel.provider == ModelProvider.SPEEDBEAR.value,
            ModelChannel.source == "platform",
            ModelChannel.is_active.is_(True),
        )
        .order_by(ModelChannel.created_at.asc(), ModelChannel.id.asc())
    )


def credential_cipher() -> AESGCMEnvCipher:
    """Use the existing host credential key for channel resolution."""
    raw_key = get_settings().model_credentials_key.get_secret_value().strip()
    if not raw_key:
        raise RuntimeError("MODEL_CREDENTIALS_KEY is not set (base64 32B master key)")
    return AESGCMEnvCipher(base64.b64decode(raw_key))


def _decrypt_credential(channel: ModelChannel) -> str:
    return credential_cipher().decrypt(
        channel.credential_encrypted, aad=f"{channel.provider}:{channel.tenant_id}"
    )


def _public_binding_snapshot(
    channel: ModelChannel | None,
    tenant_id: uuid.UUID,
    provider: ModelProvider,
    speedbear_base_url: str,
) -> PublicModelBindingSnapshot | None:
    if channel is None:
        return None
    return PublicModelBindingSnapshot(
        tenant_id=tenant_id,
        provider=provider,
        api_key=SecretStr(_decrypt_credential(channel)),
        base_url=f"{speedbear_base_url.rstrip('/')}/api/v1",
    )


def _channel_snapshot(channel: ModelChannel) -> ChannelSnapshot:
    return ChannelSnapshot(
        id=channel.id, tenant_id=channel.tenant_id, provider=channel.provider,
        model_names=tuple(channel.model_names or ()), api_base=channel.api_base,
        credential_encrypted=channel.credential_encrypted,
        credential_sha256=channel.credential_sha256, credential_masked=channel.credential_masked,
        priority=channel.priority, cooldown_until_ms=channel.cooldown_until_ms,
        extra=dict(channel.extra or {}), source=channel.source, is_active=channel.is_active,
        created_at_ms=to_timestamp_ms(channel.created_at) or 0,
        updated_at_ms=to_timestamp_ms(channel.updated_at) or 0,
    )


def _active_channel_query(tenant_id: uuid.UUID, provider: str):
    return select(ModelChannel).where(
        ModelChannel.tenant_id == tenant_id,
        ModelChannel.provider == provider,
        ModelChannel.is_active.is_(True),
    )


class SyncSQLModelRegistry(ModelRegistryRepository):
    """Expose Platform model rows to synchronous worker task code."""

    def __init__(self, db: Session, *, speedbear_base_url: str | None = None):
        self.db = db
        self.speedbear_base_url = speedbear_base_url or get_settings().speedbear_base_url

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
        if provider is not ModelProvider.SPEEDBEAR:
            return None
        result = self.db.execute(_platform_channel_query(tenant_id))
        return _public_binding_snapshot(
            result.scalars().first(),
            tenant_id,
            provider,
            self.speedbear_base_url,
        )

    def list_active_channels(self, tenant_id: uuid.UUID, provider: str) -> list[ChannelSnapshot]:
        result = self.db.execute(_active_channel_query(tenant_id, provider))
        return [_channel_snapshot(row) for row in result.scalars().all()]

    def record_key_usage(self, key_id: uuid.UUID) -> None:
        del key_id
        raise RuntimeError("Knowledge reference repositories are read-only")


class AsyncSQLModelRegistry:
    """Expose Platform model rows as immutable scalar snapshots."""

    def __init__(self, db: AsyncSession, *, speedbear_base_url: str | None = None):
        self.db = db
        self.speedbear_base_url = speedbear_base_url or get_settings().speedbear_base_url

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
        if provider is not ModelProvider.SPEEDBEAR:
            return None
        result = await self.db.execute(_platform_channel_query(tenant_id))
        return _public_binding_snapshot(
            result.scalars().first(),
            tenant_id,
            provider,
            self.speedbear_base_url,
        )

    async def list_active_channels(
        self, tenant_id: uuid.UUID, provider: str,
    ) -> list[ChannelSnapshot]:
        result = await self.db.execute(_active_channel_query(tenant_id, provider))
        return [_channel_snapshot(row) for row in result.scalars().all()]

    async def record_key_usage(self, key_id: uuid.UUID) -> None:
        del key_id
        raise RuntimeError("Knowledge reference repositories are read-only")


__all__ = ["AsyncSQLModelRegistry", "SyncSQLModelRegistry"]

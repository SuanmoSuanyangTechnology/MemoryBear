"""Read-only SQL adapter for the shared RedBear model resolver."""

from __future__ import annotations

import base64
import logging
import uuid

from pydantic import SecretStr
from redbear_model import (
    AsyncSQLChannelRegistry,
    ChannelSnapshot,
    ChannelSnapshotCache,
    LoadBalanceStrategy,
    ModelCapability,
    ModelConfigSnapshot,
    ModelKeySnapshot,
    ModelProvider,
    ModelRegistryRepository,
    ModelType,
    PublicModelBindingSnapshot,
    RegistrySQLSource,
    SyncSQLChannelRegistry,
    match_channel_candidates,
    order_channel_candidates,
)
from redbear_model.crypto import AESGCMEnvCipher, CredentialCipher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..bootstrap import get_settings
from ..models.references import ModelChannel, ModelConfig
from ..utils.datetime_utils import to_timestamp_ms

logger = logging.getLogger(__name__)

# 进程级共享渠道快照缓存（spec §11.1；只存密文快照）。mem-knowledge 无渠道写路径，
# 不主动失效，靠 TTL 过期；与 core 侧解析共用同一新鲜度上界。
_CHANNEL_CACHE = ChannelSnapshotCache(ttl_ms=60_000)


def _capabilities(values: list[str] | None) -> tuple[ModelCapability, ...]:
    result = []
    for value in values or []:
        try:
            result.append(ModelCapability(value))
        except ValueError:
            continue
    return tuple(result)


def _created_ms(value) -> int:
    return int(value.timestamp() * 1000) if value is not None else 0


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


def _channel_snapshot(channel: ModelChannel) -> ChannelSnapshot:
    """行→契约快照（与 core channel_registry 投影同口径）。"""
    return ChannelSnapshot(
        id=channel.id,
        tenant_id=channel.tenant_id,
        provider=channel.provider,
        model_names=tuple(channel.model_names or ()),
        api_base=channel.api_base,
        credential_encrypted=channel.credential_encrypted,
        credential_sha256=channel.credential_sha256,
        credential_masked=channel.credential_masked,
        is_active=channel.is_active,
        priority=channel.priority,
        cooldown_until_ms=channel.cooldown_until_ms,
        source=channel.source,
        extra=dict(channel.extra or {}),
        created_at_ms=_created_ms(channel.created_at),
        updated_at_ms=_created_ms(channel.updated_at),
    )


SOURCE = RegistrySQLSource(
    config_mapper=ModelConfig,
    channel_mapper=ModelChannel,
    config_snapshot=_config_snapshot,
    channel_snapshot=_channel_snapshot,
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


def _channel_cipher() -> CredentialCipher:
    raw_key = get_settings().model_credentials_key.get_secret_value().strip()
    if not raw_key:
        raise RuntimeError("MODEL_CREDENTIALS_KEY is not set (base64 32B master key)")
    return AESGCMEnvCipher(base64.b64decode(raw_key))


def _decrypt_credential(channel, cipher: CredentialCipher) -> str:
    """渠道密文解密（AAD=provider:tenant_id，与 ChannelService 写入侧一致）。"""
    return cipher.decrypt(
        channel.credential_encrypted, aad=f"{channel.provider}:{channel.tenant_id}"
    )


def _public_binding_snapshot(
    channel: ModelChannel | None,
    tenant_id: uuid.UUID,
    provider: ModelProvider,
    speedbear_base_url: str,
    cipher: CredentialCipher,
) -> PublicModelBindingSnapshot | None:
    if channel is None:
        return None
    return PublicModelBindingSnapshot(
        tenant_id=tenant_id,
        provider=provider,
        api_key=SecretStr(_decrypt_credential(channel, cipher)),
        base_url=f"{speedbear_base_url.rstrip('/')}/api/v1",
    )


def _ordered_candidates(
    config: ModelConfigSnapshot,
    channels: list[ChannelSnapshot],
) -> list[ChannelSnapshot]:
    """§10.1/§11.1 覆盖命中 + 选路排序（复用包内纯函数，与 core 语义零漂移）。"""
    candidates = match_channel_candidates(config, channels, model_name=config.name)
    return order_channel_candidates(candidates, model_name=config.name)


def _keys_from_channels(
    config: ModelConfigSnapshot,
    channels: list[ChannelSnapshot],
) -> list[ModelKeySnapshot]:
    """有序渠道链 → v1 key 快照链（非 speedbear 私有模型的渠道化实现）。

    单个渠道解密失败跳过（与宿主 prefer 模式降级一致）；全部失败返回空链，由
    `_select_key` 抛 ModelCredentialNotFoundError。主密钥缺失仍响亮抛 RuntimeError。
    """
    cipher = _channel_cipher()
    keys = []
    for channel in channels:
        try:
            api_key = _decrypt_credential(channel, cipher)
        except Exception as exc:
            logger.warning(
                "channel credential decrypt failed, channel=%s provider=%s: %s",
                channel.id,
                channel.provider,
                exc,
            )
            continue
        keys.append(
            ModelKeySnapshot(
                key_id=channel.id,
                model_name=config.name,
                provider=ModelProvider(channel.provider),
                api_key=SecretStr(api_key),
                base_url=channel.api_base,
                is_active=True,
                priority=str(channel.priority),
                usage_count=0,
                last_used_at_ms=None,
                capabilities=(),
                is_omni=False,
                config={},
            )
        )
    return keys


class SyncSQLModelRegistry(ModelRegistryRepository):
    """Expose Platform model rows to synchronous worker task code."""

    def __init__(self, db: Session, *, speedbear_base_url: str | None = None):
        self.db = db
        self.speedbear_base_url = speedbear_base_url or get_settings().speedbear_base_url
        self._channels = SyncSQLChannelRegistry(db, SOURCE, cache=_CHANNEL_CACHE)

    def get_model_config(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> ModelConfigSnapshot | None:
        # 租户可见性统一由 resolver `_validate_config_access` 校验（含 is_public 放行）
        del tenant_id
        return self._channels.get_config(model_config_id)

    def list_active_keys(
        self,
        model_config_id: uuid.UUID,
    ) -> list[ModelKeySnapshot]:
        config = self._channels.get_config(model_config_id)
        if config is None:
            return []
        channels = self._channels.get_active_channels(
            config.tenant_id, provider=config.provider
        )
        return _keys_from_channels(config, _ordered_candidates(config, channels))

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
            _channel_cipher(),
        )

    def record_key_usage(self, key_id: uuid.UUID) -> None:
        del key_id
        raise RuntimeError("Knowledge reference repositories are read-only")


class AsyncSQLModelRegistry:
    """Expose Platform model rows as immutable scalar snapshots."""

    def __init__(self, db: AsyncSession, *, speedbear_base_url: str | None = None):
        self.db = db
        self.speedbear_base_url = speedbear_base_url or get_settings().speedbear_base_url
        self._channels = AsyncSQLChannelRegistry(db, SOURCE, cache=_CHANNEL_CACHE)

    async def get_model_config(
        self,
        model_config_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> ModelConfigSnapshot | None:
        # 租户可见性统一由 resolver `_validate_config_access` 校验（含 is_public 放行）
        del tenant_id
        return await self._channels.get_config(model_config_id)

    async def list_active_keys(
        self,
        model_config_id: uuid.UUID,
    ) -> list[ModelKeySnapshot]:
        config = await self._channels.get_config(model_config_id)
        if config is None:
            return []
        channels = await self._channels.get_active_channels(
            config.tenant_id, provider=config.provider
        )
        return _keys_from_channels(config, _ordered_candidates(config, channels))

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
            _channel_cipher(),
        )

    async def record_key_usage(self, key_id: uuid.UUID) -> None:
        del key_id
        raise RuntimeError("Knowledge reference repositories are read-only")


__all__ = ["AsyncSQLModelRegistry", "SyncSQLModelRegistry"]

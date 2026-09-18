"""渠道登记服务（model_channels）：加密编排 + 幂等合并登记原语。

M1 阶段为纯登记原语层（无 wire 面），供数据迁移脚本（旧表 → model_channels）
与 M3 的 controller / 运行期 registry 复用。Wire 语义（spec §15.1 B/C 域）在
M3 于本服务之上实现；模型归属/影响面翻译属 M3/M6。

加密铁律（spec §8.1）：新写入凭据 100% 密文，信封 v{ver}:{iv}:{tag}:{ct}，
AAD = provider:tenant_id；任何日志/响应/异常禁止明文；对外只出 credential_masked。
事务边界在调用方，本服务只 flush。
"""
from __future__ import annotations

import base64
import uuid

from redbear_model.crypto import AESGCMEnvCipher, credential_sha256
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.models_model import ModelChannel
from app.repositories.model_channel_repository import ModelChannelRepository
from app.utils.redis_cache import invalidate_runtime_model_info_batch

_CIPHER: AESGCMEnvCipher | None = None


def cipher_from_env() -> AESGCMEnvCipher:
    """MODEL_CREDENTIALS_KEY（base64 32B）→ 信封 cipher；进程内单例。"""
    global _CIPHER
    if _CIPHER is None:
        raw = settings.MODEL_CREDENTIALS_KEY.strip()
        if not raw:
            raise RuntimeError("MODEL_CREDENTIALS_KEY is not set (base64 32B master key)")
        try:
            _CIPHER = AESGCMEnvCipher(base64.b64decode(raw))
        except ValueError as exc:
            raise RuntimeError(f"MODEL_CREDENTIALS_KEY invalid: {exc}") from exc
    return _CIPHER


def _masked(api_key: str) -> str:
    if len(api_key) < 8:
        return "****"
    return f"{api_key[:3]}****{api_key[-4:]}"


def _aad(provider: str, tenant_id: uuid.UUID) -> str:
    return f"{provider}:{tenant_id}"


def describe_channel(row: ModelChannel) -> dict:
    """渠道脱敏视图（时间毫秒）——详情/列表/删除影响面前置提示共用（纯函数，无查询）。"""
    created_ms = int(row.created_at.timestamp() * 1000) if row.created_at else None
    updated_ms = int(row.updated_at.timestamp() * 1000) if row.updated_at else None
    return {
        "id": str(row.id),
        "provider": row.provider,
        "tenant_id": str(row.tenant_id),
        "model_names": list(row.model_names or []),
        "is_provider_level": not row.model_names,
        "api_base": row.api_base,
        "credential_masked": row.credential_masked,
        "is_active": row.is_active,
        "priority": row.priority,
        "source": row.source,
        "remark": row.remark,
        "created_at_ms": created_ms,
        "updated_at_ms": updated_ms,
    }


class ChannelService:
    """model_channels 登记原语（同步 Session）。

    provider 恒为真实供应商（不允许 composite）；点名渠道 model_names 恒非空
    （清空即删行），[] 仅 Provider 域登记产生——同凭据点名行命中时原地升级；
    幂等合并键 (provider, tenant_id, api_base, credential_sha256) 由 DB 唯一约束
    兜底（api_base 空 = provider 默认端点）。
    """

    def __init__(self, db: Session):
        self.db = db
        self.repo = ModelChannelRepository(db)
        self.cipher = cipher_from_env()

    # ---- 加密 ----
    def _protect(self, provider: str, tenant_id: uuid.UUID, api_key: str) -> tuple[str, str, str]:
        return (
            self.cipher.encrypt(api_key, aad=_aad(provider, tenant_id)),
            credential_sha256(api_key),
            _masked(api_key),
        )

    def reveal(self, channel: ModelChannel) -> str:
        """解密凭据（唯一解密出口：运行时取 key 用，禁止进日志/响应）。"""
        return self.cipher.decrypt(
            channel.credential_encrypted, aad=_aad(channel.provider, channel.tenant_id)
        )

    # ---- 运行期缓存失效（写路径主动失效，不依赖 TTL 兜底）----
    def _invalidate_runtime(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: str,
        model_names: list[str] | None,
    ) -> None:
        """双层失效：进程内渠道快照缓存 + 受影响 config 的 Redis 运行时缓存。

        model_names=None → provider 级渠道（影响该 provider 全部 config）；
        非 None → 仅 name ∈ model_names 的 config。突变 flush 后即失效。
        调用方若最终回滚：其间未做候选探测则仅多一次缓存重查；若探测已按未提交态
        回填快照缓存，须自行再失效（见 model_channel_service 删除/解绑联动）。
        """
        from app.services.channel_registry import (  # 延迟导入：registry 反向依赖本模块 cipher
            affected_config_ids,
            invalidate_channel_cache,
        )

        invalidate_channel_cache(tenant_id, provider)
        config_ids = affected_config_ids(
            self.db, tenant_id=tenant_id, provider=provider, model_names=model_names
        )
        if config_ids:
            invalidate_runtime_model_info_batch(config_ids, tenant_id=tenant_id)

    # ---- 登记（双入口幂等合并）----
    def register_provider_channel(
        self,
        *,
        provider: str,
        tenant_id: uuid.UUID,
        api_key: str,
        api_base: str | None = None,
        remark: str | None = None,
        priority: int = 0,
        source: str = "manual",
        extra: dict | None = None,
        created_by: uuid.UUID | None = None,
    ) -> tuple[ModelChannel, str]:
        """登记 provider 公共 key（model_names=[]，覆盖该 provider 全部未点名模型）。

        api_base 恒 NULL（运行时使用 provider 公共基地址，本地提供商由上层拦截）；
        同幂等键已存在：点名渠道 → 原地升级为 provider 级（覆盖集扩展为全量，
        返回 "upgraded"）；已 provider 级 → merged（no-op）；否则新建。
        合并/升级均不覆盖既有行的其余属性（remark/priority/extra）。
        返回 (channel, "created" | "merged" | "upgraded")。
        """
        if provider == "composite":
            raise ValueError("composite is a config shape, not a channel provider")
        if isinstance(api_base, str) and not api_base.strip():
            api_base = None
        if api_base is not None:
            raise ValueError("provider-level channel must not set api_base")
        sha = credential_sha256(api_key)
        exist = self.repo.get_by_credential(
            provider=provider, tenant_id=tenant_id, api_base=api_base, credential_sha256=sha
        )
        if exist is not None:
            # 守卫返回 False = 并发下已被升级/删除；按现状返回，失效由写方负责
            if exist.model_names and self.repo.promote_to_provider_level(exist.id):
                # 同凭据点名行命中（唯一约束只允许单行）：Provider 域意图优先，原地升级
                self.db.refresh(exist)
                self._invalidate_runtime(
                    tenant_id=tenant_id, provider=provider, model_names=None
                )
                return exist, "upgraded"
            return exist, "merged"
        encrypted, _, masked = self._protect(provider, tenant_id, api_key)
        row = self.repo.create(
            tenant_id=tenant_id,
            provider=provider,
            model_names=[],
            api_base=api_base,
            credential_encrypted=encrypted,
            credential_sha256=sha,
            credential_masked=masked,
            source=source,
            remark=remark,
            created_by=created_by,
            priority=priority,
            extra=extra,
        )
        self._invalidate_runtime(tenant_id=tenant_id, provider=provider, model_names=None)
        return row, "created"

    def register_for_model(
        self,
        *,
        provider: str,
        tenant_id: uuid.UUID,
        model_name: str,
        api_key: str,
        api_base: str | None = None,
        remark: str | None = None,
        priority: int = 0,
        created_by: uuid.UUID | None = None,
    ) -> tuple[ModelChannel, str]:
        """给模型加专属 key：落点名渠道 model_names ∪ {model_name}。

        同凭据同端点已存在时 merged：provider 级 [] 渠道 → 模型已被默认覆盖，
        直接吸收（no-op）；点名渠道 → 已含则 no-op、未含则 union 并入。
        永不产生 provider 级渠道（专属 key 只点名自身）。
        """
        if provider == "composite":
            raise ValueError("composite is a config shape, not a channel provider")
        sha = credential_sha256(api_key)
        exist = self.repo.get_by_credential(
            provider=provider, tenant_id=tenant_id, api_base=api_base, credential_sha256=sha
        )
        if exist is not None:
            if exist.model_names and model_name not in exist.model_names:
                self.repo.union_model_name(exist.id, model_name)
                self.db.refresh(exist)
                self._invalidate_runtime(
                    tenant_id=tenant_id, provider=provider, model_names=[model_name]
                )
            return exist, "merged"
        encrypted, _, masked = self._protect(provider, tenant_id, api_key)
        row = self.repo.create(
            tenant_id=tenant_id,
            provider=provider,
            model_names=[model_name],
            api_base=api_base,
            credential_encrypted=encrypted,
            credential_sha256=sha,
            credential_masked=masked,
            remark=remark,
            created_by=created_by,
            priority=priority,
        )
        self._invalidate_runtime(
            tenant_id=tenant_id, provider=provider, model_names=[model_name]
        )
        return row, "created"

    # ---- 解绑/删除 ----
    def unbind_model(self, channel_id: uuid.UUID, model_name: str) -> tuple[bool, bool]:
        """点名渠道移除模型：清空后自动删行。

        返回 (unbound, deleted)：unbound=True 表示覆盖集确有移除；
        deleted=True 表示点名渠道随之清空并已删行。provider 级渠道无点名可解 →
        (False, False)；行不存在 → LookupError。
        """
        row = self.repo.get(channel_id)
        if row is None:
            raise LookupError(f"model channel {channel_id} not found")
        if not row.model_names:
            return False, False
        if not self.repo.unbind_model_name(channel_id, model_name):
            return False, False
        self._invalidate_runtime(
            tenant_id=row.tenant_id, provider=row.provider, model_names=[model_name]
        )
        fresh = self.repo.get(channel_id)
        if fresh is not None and not fresh.model_names:
            self.repo.delete_if_empty(channel_id)
            return True, True
        return True, False

    def delete(self, channel_id: uuid.UUID) -> bool:
        """删凭据本体（显式管理操作）；不存在返回 False。影响面提示见 describe。

        flush 让删除在同事务内立即可见（二次 delete → get 为空 → False）。
        """
        row = self.repo.get(channel_id)
        if row is None:
            return False
        self.repo.delete(channel_id)
        self.db.flush()
        self._invalidate_runtime(
            tenant_id=row.tenant_id,
            provider=row.provider,
            model_names=None if not row.model_names else list(row.model_names),
        )
        return True

    # ---- 更新 ----
    def update_attributes(
        self,
        channel_id: uuid.UUID,
        *,
        priority: int | None = ...,
        remark: str | None = ...,
        api_base: str | None = ...,
        is_active: bool | None = ...,
        extra: dict | None = ...,
    ) -> ModelChannel:
        """改渠道属性；省略 = 不改，None 语义（清空 api_base / extra）见仓库 _UNSET 文档。

        provider 级渠道 api_base 恒 NULL：空串归一为 None，非空拒改。
        extra 为整体替换（企业语义元数据），wire 层不暴露，仅内部调用方使用。
        """
        kwargs = {}
        for key, val in (
            ("priority", priority),
            ("remark", remark),
            ("api_base", api_base),
            ("is_active", is_active),
            ("extra", extra),
        ):
            if val is not ...:
                kwargs[key] = val
        if not kwargs:
            raise ValueError("no attribute to update")
        row = self.repo.get(channel_id)  # 失效上下文（覆盖集不随属性变更）
        if row is not None and not row.model_names and "api_base" in kwargs:
            value = kwargs["api_base"]
            if isinstance(value, str) and not value.strip():
                kwargs["api_base"] = None
            if kwargs["api_base"] is not None:
                raise ValueError("provider-level channel must not set api_base")
        updated = self.repo.update_attributes(channel_id, **kwargs)
        if row is not None:
            self._invalidate_runtime(
                tenant_id=row.tenant_id,
                provider=row.provider,
                model_names=None if not row.model_names else list(row.model_names),
            )
        return updated

    def replace_credential(self, channel_id: uuid.UUID, *, api_key: str) -> ModelChannel:
        """凭据重填：重加密 + 重算指纹/掩码（AAD 从行内自取，防错配）。

        键（api_base/指纹）变化撞唯一约束由 DB 兜底（IntegrityError 上抛给调用方）。
        """
        row = self.repo.get(channel_id)
        if row is None:
            raise LookupError(f"model channel {channel_id} not found")
        encrypted, sha, masked = self._protect(row.provider, row.tenant_id, api_key)
        updated = self.repo.update_credential(
            channel_id,
            credential_encrypted=encrypted,
            credential_sha256=sha,
            credential_masked=masked,
        )
        self._invalidate_runtime(
            tenant_id=row.tenant_id,
            provider=row.provider,
            model_names=None if not row.model_names else list(row.model_names),
        )
        return updated

    # ---- 读/影响面 ----
    def get(self, channel_id: uuid.UUID) -> ModelChannel | None:
        return self.repo.get(channel_id)

    def get_by_credential(
        self,
        *,
        provider: str,
        tenant_id: uuid.UUID,
        api_base: str | None,
        credential_sha256: str,
    ) -> ModelChannel | None:
        return self.repo.get_by_credential(
            provider=provider, tenant_id=tenant_id, api_base=api_base, credential_sha256=credential_sha256
        )

    def list_tenant(
        self,
        *,
        tenant_id: uuid.UUID,
        provider: str | None = None,
        is_active: bool | None = None,
        source: str | None = None,
        model_name: str | None = None,
    ) -> list[ModelChannel]:
        return self.repo.list_tenant(
            tenant_id=tenant_id,
            provider=provider,
            is_active=is_active,
            source=source,
            model_name=model_name,
        )

    def describe(self, channel_id: uuid.UUID) -> dict | None:
        """渠道详情（脱敏视图，时间毫秒）——删除/影响面前置提示与列表展示共用。"""
        row = self.repo.get(channel_id)
        if row is None:
            return None
        return describe_channel(row)


__all__ = ["ChannelService", "describe_channel"]

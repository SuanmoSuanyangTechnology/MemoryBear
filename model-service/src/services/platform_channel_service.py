"""平台代管渠道写路径：SpeedBear 网关 api-key 供给/轮换/清理（服务侧全编排）。

premium（SSO 新建租户绑定、控制台补绑、订单派额自愈）只传目标租户与 ``tenant_name``
+ ``quota_limit``；建 key 的上游协议在私有扩展的 ``provision_tenant_channel``（含默认
计价组解析），凭据明文全程留在本进程——加密落 ``model_channels``，对外只出掩码视图。

轮换不变量：已存在 platform 渠道时**复用最早行**（created_at, id 升序）换凭据，而不是
新建第二行——渠道选路按 least-used → created_at asc，旧凭据行更早会被反优先选中。

错误语汇：上游业务拒绝/缺字段 ``ValueError`` → ``INVALID_PARAMETER``；上游传输失败
``httpx.HTTPError`` → ``SERVICE_UNAVAILABLE``（premium 侧据此还原 4xx/503）。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..models.models_model import ModelProvider
from .channel_service import ChannelService, describe_channel
from .enterprise_loader import get_platform_semantics

logger = logging.getLogger(__name__)

PROVIDER = ModelProvider.SPEEDBEAR.value
PLATFORM_CHANNEL_SOURCE = "platform"


class PlatformChannelService:
    """租户平台渠道供给与清理（唯一写者：建 key → 加密 → 落行/轮换）。"""

    @staticmethod
    def provision(
        db: Session,
        *,
        tenant_id: uuid.UUID,
        tenant_name: str,
        quota_limit: float,
    ) -> dict[str, Any]:
        """供给租户平台渠道：上游建 key → 最早行轮换或新建 → 提交。

        返回 ``{"channel": 脱敏视图, "metadata": 上游记账元数据}``；凭据明文不进返回值。
        """
        credential = PlatformChannelService._provision_upstream(
            tenant_name=tenant_name, quota_limit=quota_limit
        )
        svc = ChannelService(db)
        existing = svc.list_tenant(
            tenant_id=tenant_id, provider=PROVIDER, source=PLATFORM_CHANNEL_SOURCE
        )
        if existing:
            channel_id = min(existing, key=lambda ch: (ch.created_at, str(ch.id))).id
            svc.update_attributes(channel_id, extra=credential.channel_extra, is_active=True)
            channel = svc.replace_credential(channel_id, api_key=credential.credential)
        else:
            channel, _status = svc.register_provider_channel(
                provider=PROVIDER,
                tenant_id=tenant_id,
                api_key=credential.credential,
                api_base=None,
                source=PLATFORM_CHANNEL_SOURCE,
                extra=credential.channel_extra,
            )
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(channel)
        return {"channel": describe_channel(channel), "metadata": credential.metadata}

    @staticmethod
    def delete_tenant_channels(db: Session, *, tenant_id: uuid.UUID) -> int:
        """删该租户全部渠道（租户物理删除的前置清理），返回删除行数。"""
        deleted = ChannelService(db).delete_tenant_channels(tenant_id)
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        return deleted

    @staticmethod
    def _provision_upstream(*, tenant_name: str, quota_limit: float) -> Any:
        semantics = get_platform_semantics()
        try:
            return semantics.provision_tenant_channel(
                tenant_name=tenant_name, quota_limit=quota_limit
            )
        except ValueError as exc:
            raise BusinessException(str(exc), BizCode.INVALID_PARAMETER) from exc
        except httpx.HTTPError as exc:
            raise BusinessException(
                f"SpeedBear 网关请求失败: {exc}", BizCode.SERVICE_UNAVAILABLE
            ) from exc


__all__ = ["PlatformChannelService"]

"""平台面渠道端点（``/internal/v1/platform/channels``）：SpeedBear 租户渠道供给/清理。

premium（SSO 新建租户绑定、控制台补绑/批量补绑、订单派额自愈、严格模式回滚）是唯一
调用方：身份经 ``X-Model-*`` 内部头，``tenant_id`` = 目标租户；网关建 key 与渠道落库
全在 ``PlatformChannelService``（凭据明文不出本进程）。
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...services.platform_channel_service import PlatformChannelService
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.platform import PlatformChannelProvisionRequest
from ..schemas.response_schema import ApiResponse

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]

router = APIRouter(
    prefix="/platform/channels",
    tags=["Platform Channels"],
)


@router.post("", response_model=ApiResponse)
def provision_platform_channel(
    body: PlatformChannelProvisionRequest,
    db: DbSession,
    principal: Caller,
):
    """供给租户平台渠道：上游建 key（默认计价组 + 配额）→ 落库或轮换最早行。"""
    result = PlatformChannelService.provision(
        db,
        tenant_id=principal.tenant_id,
        tenant_name=body.tenant_name,
        quota_limit=body.quota_limit,
    )
    return success(data=result, msg="租户渠道供给成功")


@router.delete("", response_model=ApiResponse)
def delete_platform_channels(db: DbSession, principal: Caller):
    """清理租户全部渠道（租户物理删除前置）；返回删除行数。"""
    deleted = PlatformChannelService.delete_tenant_channels(
        db, tenant_id=principal.tenant_id
    )
    return success(data={"deleted": deleted}, msg="租户渠道清理成功")

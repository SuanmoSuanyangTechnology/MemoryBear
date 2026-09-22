"""平台面内部端点（``/internal/v1/platform/models*``，M7-5）。

premium 控制台的 SpeedBear 系统模型写路径是唯一调用方：身份经 ``X-Model-*`` 内部头
（``tenant_id`` = 目标系统租户，``source=platform_admin``），编排全在
``PlatformModelService``；读路径仍留 premium 本地（D-M7-6）。
"""
from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...services.model_profile_view import wire_model_config
from ...services.platform_model_service import PlatformModelService
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.platform import (
    PlatformModelCreateRequest,
    PlatformModelStatusRequest,
    PlatformModelUpdateRequest,
)
from ..schemas.response_schema import ApiResponse

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]

router = APIRouter(
    prefix="/platform/models",
    tags=["Platform Models"],
)


def _wire(models) -> list[dict]:
    return [wire_model_config(model).model_dump(mode="json") for model in models]


@router.post("", response_model=ApiResponse)
def create_platform_models(
    body: PlatformModelCreateRequest,
    db: DbSession,
    principal: Caller,
):
    """按上游模型 ID 批量创建系统公共模型（整批校验，不半途落行）。"""
    models = PlatformModelService.create_system_models(
        db, tenant_id=principal.tenant_id, model_ids=body.model_ids
    )
    return success(data=_wire(models), msg="批量创建系统模型成功")


@router.put("/{model_id}", response_model=ApiResponse)
def update_platform_model(
    model_id: uuid.UUID,
    body: PlatformModelUpdateRequest,
    db: DbSession,
    principal: Caller,
):
    """更新系统模型类型与三列（未提交字段保持现值）。"""
    model = PlatformModelService.update_system_model(
        db,
        tenant_id=principal.tenant_id,
        model_id=model_id,
        update_data=body.model_dump(exclude_unset=True),
    )
    return success(data=_wire([model])[0], msg="更新系统模型成功")


@router.put("/{model_id}/status", response_model=ApiResponse)
def update_platform_model_status(
    model_id: uuid.UUID,
    body: PlatformModelStatusRequest,
    db: DbSession,
    principal: Caller,
):
    """启用/停用系统模型。"""
    model = PlatformModelService.update_system_model_status(
        db, tenant_id=principal.tenant_id, model_id=model_id, is_active=body.is_active
    )
    return success(data=_wire([model])[0], msg="更新系统模型状态成功")


@router.delete("/{model_id}", response_model=ApiResponse)
def delete_platform_model(
    model_id: uuid.UUID,
    db: DbSession,
    principal: Caller,
):
    """删除系统模型（仍被应用引用时拒绝并返回应用名清单）。"""
    PlatformModelService.delete_system_model(
        db, tenant_id=principal.tenant_id, model_id=model_id
    )
    return success(msg="删除系统模型成功")

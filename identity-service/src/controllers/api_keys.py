"""API-key management routes (host port, sync plane).

Declaration order and error wrapping mirror
``core/api/app/controllers/api_key_controller.py``: create/update/regenerate
pass business errors through, while list/get/delete/stats/logs wrap every
exception with the host's Chinese prefix.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from error_core.codes import BizCode
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.core.datetime_utils import parse_timestamp_to_utc_naive
from src.core.deps import cur_workspace_access_guard, get_current_user
from src.core.exceptions import BusinessException
from src.core.response_utils import success
from src.db import get_db
from src.models.api_key_model import ApiKeyType
from src.models.user_model import User
from src.repositories import end_user_repository
from src.schemas import api_key_schema
from src.schemas.response_schema import ApiResponse
from src.services import api_key_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apikeys", tags=["API Keys"])


@router.post("", response_model=ApiResponse)
@cur_workspace_access_guard()
def create_api_key(
    data: api_key_schema.ApiKeyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建 API Key"""
    try:
        workspace_id = current_user.current_workspace_id
        if data.type == ApiKeyType.SERVICE and not data.resource_id:
            data.resource_id = workspace_id

        api_key_obj = api_key_service.create_api_key(
            db, workspace_id=workspace_id, user_id=current_user.id, data=data
        )

        end_user_id, other_id = None, None
        if "memory" in data.scopes and data.user_id:
            end_user_id, other_id = end_user_repository.get_or_create_end_user_mcp(
                db, workspace_id, data.user_id
            )

        response_data = api_key_schema.ApiKeyResponse.model_validate(api_key_obj)
        response_data.end_user_id = end_user_id
        response_data.other_id = other_id

        return success(data=response_data.model_dump(), msg="API Key 创建成功")
    except BusinessException:
        raise
    except Exception as exc:
        logger.error("未知错误: %s", exc, exc_info=True)
        raise BusinessException(f"创建API Key失败：{exc}", code=BizCode.DB_ERROR) from exc


@router.get("", response_model=ApiResponse)
@cur_workspace_access_guard()
def list_api_keys(
    type: api_key_schema.ApiKeyType | None = Query(None, description="按类型过滤"),
    is_active: bool = Query(True, description="按状态过滤"),
    resource_id: uuid.UUID | None = Query(None, description="按资源过滤"),
    page: int = Query(1, ge=1, description="页码"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出 API Keys"""
    try:
        workspace_id = current_user.current_workspace_id
        query = api_key_schema.ApiKeyQuery(
            type=type,
            is_active=is_active,
            resource_id=resource_id,
            page=page,
            pagesize=pagesize,
        )

        result = api_key_service.list_api_keys(db, workspace_id, query)
        logger.info("API Keys 查询成功")
        return success(data=result.model_dump())
    except Exception as exc:
        logger.error("未知错误: %s", exc, exc_info=True)
        raise BusinessException(f"API Keys 查询失败：{exc}", code=BizCode.DB_ERROR) from exc


@router.get("/{api_key_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_api_key(
    api_key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取 API Key 详情"""
    try:
        workspace_id = current_user.current_workspace_id
        api_key = api_key_service.get_api_key(db, api_key_id, workspace_id)

        logger.info("获取API Key详情成功")
        return success(
            data=api_key_schema.ApiKey.model_validate(api_key).model_dump()
        )
    except Exception as exc:
        logger.error("未知错误: %s", exc, exc_info=True)
        raise BusinessException(f"获取API Key失败: {exc}", code=BizCode.DB_ERROR) from exc


@router.put("/{api_key_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def update_api_key(
    api_key_id: uuid.UUID,
    data: api_key_schema.ApiKeyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新 API Key配置"""
    try:
        workspace_id = current_user.current_workspace_id
        api_key = api_key_service.update_api_key(db, api_key_id, workspace_id, data)

        logger.info("API Key 更新配置成功")
        return success(
            data=api_key_schema.ApiKey.model_validate(api_key).model_dump(),
            msg="API Key 更新成功",
        )
    except BusinessException:
        raise
    except Exception as exc:
        logger.error("未知错误: %s", exc, exc_info=True)
        raise BusinessException(f"更新API Key失败: {exc}", code=BizCode.DB_ERROR) from exc


@router.delete("/{api_key_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def delete_api_key(
    api_key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除 API Key"""
    try:
        workspace_id = current_user.current_workspace_id
        api_key_service.delete_api_key(db, api_key_id, workspace_id)

        logger.info("API Key 删除成功")
        return success(msg="API Key 删除成功")
    except Exception as exc:
        logger.error("未知错误: %s", exc, exc_info=True)
        raise BusinessException(f"删除API Key失败: {exc}", code=BizCode.DB_ERROR) from exc


@router.post("/{api_key_id}/regenerate", response_model=ApiResponse)
@cur_workspace_access_guard()
def regenerate_api_key(
    api_key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """重新生成 API Key"""
    try:
        workspace_id = current_user.current_workspace_id
        api_key_obj = api_key_service.regenerate_api_key(
            db, api_key_id, workspace_id
        )

        response_data = api_key_schema.ApiKeyResponse.model_validate(api_key_obj)
        logger.info("API Key 重新生成成功")
        return success(data=response_data.model_dump(), msg="API Key 重新生成成功")
    except BusinessException:
        raise
    except Exception as exc:
        logger.error("未知错误: %s", exc, exc_info=True)
        raise BusinessException(
            f"重新生成API Key失败: {exc}", code=BizCode.DB_ERROR
        ) from exc


@router.get("/{api_key_id}/stats", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_api_key_stats(
    api_key_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取 API Key 使用统计"""
    try:
        workspace_id = current_user.current_workspace_id
        stats = api_key_service.get_stats(db, api_key_id, workspace_id)

        logger.info("API Key stats retrieved successfully")
        return success(data=stats.model_dump())
    except Exception as exc:
        logger.error("未知错误: %s", exc, exc_info=True)
        raise BusinessException(
            f"获取API Key统计失败: {exc}", code=BizCode.DB_ERROR
        ) from exc


@router.get("/{api_key_id}/logs", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_api_key_logs(
    api_key_id: uuid.UUID,
    start_date: int | None = Query(None, description="开始日期时间戳"),
    end_date: int | None = Query(None, description="结束日期时间戳"),
    status_code: int | None = Query(None, description="HTTP状态码过滤"),
    endpoint: str | None = Query(None, description="端点路径过滤"),
    page: int = Query(1, ge=1, description="页码"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取 API Key 使用日志"""
    try:
        workspace_id = current_user.current_workspace_id

        start_datetime = (
            parse_timestamp_to_utc_naive(start_date) if start_date else None
        )
        end_datetime = parse_timestamp_to_utc_naive(end_date) if end_date else None

        if start_datetime and end_datetime and start_datetime > end_datetime:
            logger.warning("开始日期晚于结束日期")
            raise BusinessException("开始日期不能晚于结束日期", BizCode.INVALID_PARAMETER)

        if status_code and (status_code < 100 or status_code > 599):
            logger.warning("查询无效的状态码")
            raise BusinessException("无效的HTTP状态码", BizCode.INVALID_PARAMETER)

        filters: dict[str, Any] = {
            "start_date": start_datetime,
            "end_date": end_datetime,
            "status_code": status_code,
            "endpoint": endpoint,
        }

        result = api_key_service.get_logs(
            db, api_key_id, workspace_id, filters, page, pagesize
        )

        logger.info("API Key 日志查询成功")
        return success(data=result.model_dump())
    except Exception as exc:
        logger.error("未知错误: %s", exc, exc_info=True)
        raise BusinessException(
            f"API Key 日志查询失败: {exc}", code=BizCode.DB_ERROR
        ) from exc

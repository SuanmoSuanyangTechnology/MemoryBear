"""API Key 管理接口 - 基于 JWT 认证"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.db import get_db
from app.dependencies import get_current_user, cur_workspace_access_guard
from app.models import ApiKeyType
from app.models.user_model import User
from app.core.response_utils import success
from app.schemas import api_key_schema
from app.schemas.response_schema import ApiResponse
from app.services.api_key_service import ApiKeyService
from app.core.api_key_utils import timestamp_to_datetime
from app.core.logging_config import get_api_logger
from app.core.exceptions import (
    BusinessException,
)

router = APIRouter(prefix="/apikeys", tags=["API Keys"])
logger = get_api_logger()


@router.post("", response_model=ApiResponse)
@cur_workspace_access_guard()
def create_api_key(
        data: api_key_schema.ApiKeyCreate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    创建 API Key

    - 支持三种类型：app/rag/memory
    - 创建后返回明文 API Key（仅此一次）
    - 支持设置权限范围、速率限制、配额等
    """
    try:
        workspace_id = current_user.current_workspace_id
        if data.type == ApiKeyType.SERVICE.value and not data.resource_id:
            data.resource_id = workspace_id

        # 创建 API Key
        api_key_obj = ApiKeyService.create_api_key(
            db,
            workspace_id=workspace_id,
            user_id=current_user.id,
            data=data
        )

        response_data = api_key_schema.ApiKeyResponse.model_validate(api_key_obj)

        return success(data=response_data, msg="API Key 创建成功")
    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"未知错误: {str(e)}", extra={
            "workspace_id": str(current_user.current_workspace_id),
            "user_id": str(current_user.id),
            "operation": "create_api_key"
        }, exc_info=True)
        raise Exception(f"创建API Key失败：{str(e)}")


@router.get("", response_model=ApiResponse)
@cur_workspace_access_guard()
def list_api_keys(
        type: api_key_schema.ApiKeyType = Query(None, description="按类型过滤"),
        is_active: bool = Query(True, description="按状态过滤"),
        resource_id: uuid.UUID = Query(None, description="按资源过滤"),
        page: int = Query(1, ge=1, description="页码"),
        pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    列出 API Keys

    - 支持多维度过滤
    - 支持分页
    - 自动按创建时间倒序
    """
    try:
        workspace_id = current_user.current_workspace_id

        query = api_key_schema.ApiKeyQuery(
            type=type,
            is_active=is_active,
            resource_id=resource_id,
            page=page,
            pagesize=pagesize
        )

        result = ApiKeyService.list_api_keys(db, workspace_id, query)

        logger.info("API Keys 查询成功", extra={
            "workspace_id": str(workspace_id),
            "user_id": str(current_user.id),
            "page": page,
            "pagesize": pagesize,
            "total_count": result.get("total", 0) if isinstance(result, dict) else 0
        })

        return success(data=result)

    except Exception as e:
        logger.error(f"未知错误: {str(e)}", extra={
            "workspace_id": str(current_user.current_workspace_id),
            "user_id": str(current_user.id),
            "operation": "list_api_keys"
        }, exc_info=True)
        raise Exception(f"API Keys 查询失败：{str(e)}")


@router.get("/{api_key_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_api_key(
        api_key_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """获取 API Key 详情"""
    try:
        workspace_id = current_user.current_workspace_id
        api_key = ApiKeyService.get_api_key(db, api_key_id, workspace_id)

        logger.info("获取API Key详情成功", extra={
            "api_key_id": str(api_key_id),
            "workspace_id": str(workspace_id),
            "user_id": str(current_user.id),
            "operation": "get_api_key"
        })

        return success(data=api_key_schema.ApiKey.model_validate(api_key))
    except Exception as e:
        logger.error(f"未知错误: {str(e)}", extra={
            "api_key_id": str(api_key_id),
            "workspace_id": str(current_user.current_workspace_id),
            "user_id": str(current_user.id),
            "operation": "get_api_key"
        }, exc_info=True)
        raise Exception(f"获取API Key失败: {str(e)}")


@router.put("/{api_key_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def update_api_key(
        api_key_id: uuid.UUID,
        data: api_key_schema.ApiKeyUpdate,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """更新 API Key配置"""
    try:
        workspace_id = current_user.current_workspace_id

        api_key = ApiKeyService.update_api_key(db, api_key_id, workspace_id, data)

        logger.info("API Key 更新配置成功", extra={
            "api_key_id": str(api_key_id),
            "workspace_id": str(workspace_id),
            "user_id": str(current_user.id)
        })

        return success(data=api_key_schema.ApiKey.model_validate(api_key), msg="API Key 更新成功")

    except Exception as e:
        logger.error(f"未知错误: {str(e)}", extra={
            "api_key_id": str(api_key_id),
            "workspace_id": str(current_user.current_workspace_id),
            "user_id": str(current_user.id),
            "operation": "update_api_key"
        }, exc_info=True)
        raise Exception(f"更新API Key失败: {str(e)}")


@router.delete("/{api_key_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
def delete_api_key(
        api_key_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """删除 API Key"""
    try:
        workspace_id = current_user.current_workspace_id
        ApiKeyService.delete_api_key(db, api_key_id, workspace_id)

        logger.info("API Key 删除成功", extra={
            "api_key_id": str(api_key_id),
            "workspace_id": str(workspace_id),
            "user_id": str(current_user.id)
        })

        return success(msg="API Key 删除成功")

    except Exception as e:
        logger.error(f"未知错误: {str(e)}", extra={
            "api_key_id": str(api_key_id),
            "workspace_id": str(current_user.current_workspace_id),
            "user_id": str(current_user.id),
            "operation": "delete_api_key"
        }, exc_info=True)
        raise Exception(f"删除API Key失败: {str(e)}")


@router.post("/{api_key_id}/regenerate", response_model=ApiResponse)
@cur_workspace_access_guard()
def regenerate_api_key(
        api_key_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    重新生成 API Key
    
    - 生成新的 API Key 并返回明文（仅此一次）
    - 旧的 API Key 立即失效
    """
    try:
        workspace_id = current_user.current_workspace_id
        api_key_obj = ApiKeyService.regenerate_api_key(db, api_key_id, workspace_id)

        response_data = api_key_schema.ApiKeyResponse.model_validate(api_key_obj)

        logger.info("API Key 重新生成成功", extra={
            "api_key_id": str(api_key_id),
            "workspace_id": str(workspace_id),
            "user_id": str(current_user.id)
        })

        return success(data=response_data, msg="API Key 重新生成成功")
    except BusinessException:
        raise
    except Exception as e:
        logger.error(f"未知错误: {str(e)}", extra={
            "api_key_id": str(api_key_id),
            "workspace_id": str(current_user.current_workspace_id),
            "user_id": str(current_user.id),
            "operation": "regenerate_api_key"
        }, exc_info=True)
        raise Exception(f"重新生成API Key失败: {str(e)}")


@router.get("/{api_key_id}/stats", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_api_key_stats(
        api_key_id: uuid.UUID,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """获取 API Key 使用统计"""
    try:
        workspace_id = current_user.current_workspace_id
        stats = ApiKeyService.get_stats(db, api_key_id, workspace_id)

        logger.info("API Key stats retrieved successfully", extra={
            "api_key_id": str(api_key_id),
            "workspace_id": str(workspace_id),
            "user_id": str(current_user.id)
        })

        return success(data=stats)
    except Exception as e:
        logger.error(f"未知错误: {str(e)}", extra={
            "api_key_id": str(api_key_id),
            "workspace_id": str(current_user.current_workspace_id),
            "user_id": str(current_user.id),
            "operation": "get_api_key_stats"
        }, exc_info=True)
        raise Exception(f"获取API Key统计失败: {str(e)}")


@router.get("/{api_key_id}/logs", response_model=ApiResponse)
@cur_workspace_access_guard()
def get_api_key_logs(
        api_key_id: uuid.UUID,
        start_date: Optional[int] = Query(None, description="开始日期时间戳"),
        end_date: Optional[int] = Query(None, description="结束日期时间戳"),
        status_code: Optional[int] = Query(None, description="HTTP状态码过滤"),
        endpoint: Optional[str] = Query(None, description="端点路径过滤"),
        page: int = Query(1, ge=1, description="页码"),
        pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """
    获取 API Key 使用日志

    - 支持时间范围过滤
    - 支持状态码和端点过滤
    - 按时间倒序返回
    """
    try:
        workspace_id = current_user.current_workspace_id

        start_datetime = timestamp_to_datetime(start_date) if start_date else None
        end_datetime = timestamp_to_datetime(end_date) if end_date else None

        # 验证日期范围
        if start_datetime and end_datetime and start_datetime > end_datetime:
            logger.warning("开始日期晚于结束日期", extra={
                "api_key_id": str(api_key_id),
                "workspace_id": str(workspace_id),
                "user_id": str(current_user.id),
                "start_date": start_datetime.isoformat(),
                "end_date": end_datetime.isoformat()
            })
            raise BusinessException("开始日期不能晚于结束日期", BizCode.INVALID_PARAMETER)

        # 验证状态码
        if status_code and (status_code < 100 or status_code > 599):
            logger.warning("查询无效的状态码", extra={
                "api_key_id": str(api_key_id),
                "workspace_id": str(workspace_id),
                "user_id": str(current_user.id),
                "status_code": status_code
            })
            raise BusinessException("无效的HTTP状态码", BizCode.INVALID_PARAMETER)

        # 构建过滤条件
        filters = {
            "start_date": start_datetime,
            "end_date": end_datetime,
            "status_code": status_code,
            "endpoint": endpoint
        }

        # 调用服务层获取日志
        result = ApiKeyService.get_logs(
            db, api_key_id, workspace_id, filters, page, pagesize
        )

        logger.info("API Key 日志查询成功", extra={
            "api_key_id": str(api_key_id),
            "workspace_id": str(workspace_id),
            "user_id": str(current_user.id),
            "page": page,
            "pagesize": pagesize,
            "filters": {k: str(v) if v else None for k, v in filters.items()}
        })

        return success(data=result)

    except Exception as e:
        logger.error(f"未知错误: {str(e)}", extra={
            "api_key_id": str(api_key_id),
            "workspace_id": str(current_user.current_workspace_id),
            "user_id": str(current_user.id),
            "operation": "get_api_key_logs"
        }, exc_info=True)
        raise Exception(f"API Key 日志查询失败: {str(e)}")

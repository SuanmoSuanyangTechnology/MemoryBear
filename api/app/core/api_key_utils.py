"""API Key 工具函数"""
import secrets
import uuid as _uuid
from typing import Optional, Union
from datetime import datetime

from sqlalchemy.orm import Session as _Session
from app.core.error_codes import BizCode as _BizCode
from app.core.exceptions import BusinessException as _BusinessException
from app.models.end_user_model import EndUser as _EndUser
from app.repositories.end_user_repository import EndUserRepository as _EndUserRepository

from app.models.api_key_model import ApiKeyType
from fastapi import Response
from fastapi.responses import JSONResponse


def generate_api_key(key_type: ApiKeyType) -> str:
    """
    生成 API Key
    
    Args:
        key_type: API Key 类型
        
    Returns:
        str: api_key
    """
    # 前缀映射
    prefix_map = {
        ApiKeyType.AGENT: "sk-agent-",
        ApiKeyType.CLUSTER: "sk-multi_agent-",
        ApiKeyType.WORKFLOW: "sk-workflow-",
        ApiKeyType.PURE_WORKFLOW: "sk-workflow-",
        ApiKeyType.SERVICE: "sk-service-"
    }

    prefix = prefix_map[key_type]
    random_string = secrets.token_urlsafe(32)[:32]  # 32 字符
    api_key = f"{prefix}{random_string}"

    return api_key


def add_rate_limit_headers(response, headers: dict):
    """统一添加限流响应头"""
    if isinstance(response, Response):
        for key, value in headers.items():
            response.headers[key] = value
    elif isinstance(response, JSONResponse):
        for key, value in headers.items():
            response.headers[key] = value
    elif hasattr(response, 'headers'):
        response.headers.update(headers)

    return response


def timestamp_to_datetime(timestamp: Optional[Union[int, float]]) -> Optional[datetime]:
    """将时间戳转换为datetime对象"""
    if timestamp is None:
        return None

    # 处理毫秒级时间戳
    if timestamp > 1e10:
        timestamp = timestamp / 1000

    return datetime.fromtimestamp(timestamp)


def datetime_to_timestamp(dt: Optional[datetime]) -> Optional[int]:
    """将datetime对象转换为时间戳（毫秒）"""
    if dt is None:
        return None

    return int(dt.timestamp() * 1000)


def get_current_user_from_api_key(db: _Session, api_key_auth):
    """通过 API Key 构造 current_user 对象。

    从 API Key 反查创建者（管理员用户），并设置其 workspace 上下文。
    与内部接口的 Depends(get_current_user) (JWT) 等价。

    Args:
        db: 数据库会话
        api_key_auth: API Key 认证信息（ApiKeyAuth）

    Returns:
        User ORM 对象，已设置 current_workspace_id
    """
    from app.services import api_key_service

    api_key = api_key_service.ApiKeyService.get_api_key(
        db, api_key_auth.api_key_id, api_key_auth.workspace_id
    )
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id
    return current_user


def validate_end_user_in_workspace(
    db: _Session,
    end_user_id: str,
    workspace_id,
) -> _EndUser:
    """校验 end_user 是否存在且属于指定 workspace。

    Args:
        db: 数据库会话
        end_user_id: 终端用户 ID
        workspace_id: 工作空间 ID（UUID 或字符串均可）

    Returns:
        EndUser ORM 对象（校验通过时）

    Raises:
        BusinessException(INVALID_PARAMETER): end_user_id 格式无效
        BusinessException(USER_NOT_FOUND): end_user 不存在
        BusinessException(PERMISSION_DENIED): end_user 不属于该 workspace
    """
    try:
        _uuid.UUID(end_user_id)
    except (ValueError, AttributeError):
        raise _BusinessException(
            f"Invalid end_user_id format: {end_user_id}",
            _BizCode.INVALID_PARAMETER,
        )

    end_user_repo = _EndUserRepository(db)
    end_user = end_user_repo.get_end_user_by_id(end_user_id)

    if end_user is None:
        raise _BusinessException(
            "End user not found",
            _BizCode.USER_NOT_FOUND,
        )

    if str(end_user.workspace_id) != str(workspace_id):
        raise _BusinessException(
            "End user does not belong to this workspace",
            _BizCode.PERMISSION_DENIED,
        )

    return end_user
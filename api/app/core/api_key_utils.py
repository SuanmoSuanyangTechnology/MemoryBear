"""API Key 工具函数"""
import secrets
import uuid as _uuid
from datetime import datetime
from typing import Optional, Union

from fastapi import Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import Session as _Session

from app.core.error_codes import BizCode as _BizCode
from app.core.exceptions import BusinessException as _BusinessException
from app.core.utils.datetime_utils import parse_timestamp_to_utc_naive, to_timestamp_ms
from app.models.api_key_model import ApiKeyType
from app.models.end_user_model import EndUser as _EndUser
from app.repositories.end_user_repository import EndUserRepository as _EndUserRepository


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
    return parse_timestamp_to_utc_naive(timestamp)


def datetime_to_timestamp(dt: Optional[datetime]) -> Optional[int]:
    """将datetime对象转换为时间戳（毫秒）"""
    return to_timestamp_ms(dt)


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


async def validate_end_user_in_workspace_async(
    db: AsyncSession,
    end_user_id: str,
    workspace_id,
) -> _EndUser:
    try:
        end_user_id = _uuid.UUID(end_user_id)
    except (ValueError, AttributeError):
        raise _BusinessException(
            f"Invalid end_user_id format: {end_user_id}",
            _BizCode.INVALID_PARAMETER,
        )

    end_user_repo = _EndUserRepository(db)
    end_user = await end_user_repo.get_end_user_by_id_async(end_user_id)

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


async def get_current_user_snapshot_from_api_key_async(
    db: AsyncSession,
    api_key_auth,
) -> "CurrentUserSnapshot":
    """通过 API Key 异步构造 CurrentUserSnapshot（detach-safe）。

    使用 AsyncSession 查询 api_key → creator，提取快照后 session 可关闭。
    替代同步版本 get_current_user_from_api_key + make_snapshot 组合。

    NOTE: 此方法替代了各 V1 controller 中原有的同步 _get_current_user() 辅助函数，
    原函数通过 ORM relationship lazy load 获取 User，在 async 端点中会阻塞事件循环。
    异步化改造后统一使用本方法，原 _get_current_user 已删除。

    Args:
        db: 异步数据库会话
        api_key_auth: API Key 认证信息（ApiKeyAuth schema）

    Returns:
        CurrentUserSnapshot 实例
    """
    from sqlalchemy import select
    from app.models.api_key_model import ApiKey
    from app.models.user_model import User
    from app.dependencies import CurrentUserSnapshot

    stmt = select(ApiKey).where(
        ApiKey.id == api_key_auth.api_key_id,
        ApiKey.workspace_id == api_key_auth.workspace_id,
    )
    result = await db.execute(stmt)
    api_key = result.scalars().first()
    if not api_key:
        raise _BusinessException("API Key not found", _BizCode.API_KEY_NOT_FOUND)

    user_stmt = select(User).where(User.id == api_key.created_by)
    user_result = await db.execute(user_stmt)
    user_orm = user_result.scalars().first()
    if not user_orm:
        raise _BusinessException("API Key creator not found", _BizCode.USER_NOT_FOUND)

    return CurrentUserSnapshot(
        id=user_orm.id,
        username=user_orm.username,
        email=user_orm.email,
        is_active=user_orm.is_active,
        is_superuser=user_orm.is_superuser,
        current_workspace_id=api_key_auth.workspace_id,
        tenant_id=user_orm.tenant_id,
    )

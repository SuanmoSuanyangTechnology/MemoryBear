import asyncio
import time
import uuid
from functools import wraps
from typing import Optional, List
from datetime import datetime

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.api_key_utils import add_rate_limit_headers
from app.core.exceptions import (
    BusinessException,
    RateLimitException,
)
from app.repositories.api_key_repository import ApiKeyLogRepository, ApiKeyRepository
from app.schemas.api_key_schema import ApiKeyAuth
from app.services.api_key_service import ApiKeyAuthService, RateLimiterService
from app.core.logging_config import get_api_logger
from app.core.error_codes import BizCode

logger = get_api_logger()


def require_api_key(
        scopes: Optional[List[str]] = None
):
    """
    API Key 鉴权装饰器

    Args:
        scopes: 所需的权限范围列表[“app”, "rag", "memory"]

    Usage:
        @router.get("/app/{resource_id}/chat")
        @require_api_key(scopes=["app"])
        def chat_with_app(
            resource_id: uuid.UUID,
            request: Request,
            api_key_auth: ApiKeyAuth = None,
            db: Session = Depends(get_db),
            message: str = Query(..., description="聊天消息内容")
        ):
            # api_key_auth 包含验证后的API Key 信息
            pass
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request: Request = kwargs.get("request")
            db: Session = kwargs.get("db")

            api_key = extract_api_key_from_request(request)
            if not api_key:
                logger.warning("API Key 缺失", extra={
                    "endpoint": str(request.url),
                    "method": request.method,
                    "ip_address": request.client.host if request.client else None
                })
                raise BusinessException("API Key 不存在", BizCode.API_KEY_NOT_FOUND)

            api_key_obj = ApiKeyAuthService.validate_api_key(db, api_key)
            if not api_key_obj:
                logger.warning("API Key 无效或已过期", extra={
                    "key_prefix": api_key[:10] + "..." if len(api_key) > 10 else api_key,
                    "endpoint": str(request.url),
                    "method": request.method,
                    "ip_address": request.client.host if request.client else None
                })
                raise BusinessException("API Key 无效或已过期", BizCode.API_KEY_INVALID)

            if scopes:
                missing_scopes = []
                for scope in scopes:
                    if not ApiKeyAuthService.check_scope(api_key_obj, scope):
                        missing_scopes.append(scope)
                if missing_scopes:
                    logger.warning("API Key 权限不足", extra={
                        "api_key_id": str(api_key_obj.id),
                        "missing_scopes": missing_scopes,
                        "available_scopes": api_key_obj.scopes,
                        "endpoint": str(request.url)
                    })
                    raise BusinessException(
                        f"缺少必须的权限范围：{','.join(missing_scopes)}",
                        BizCode.API_KEY_INVALID_SCOPE,
                        context={"required_scopes": scopes, "missing_scopes": missing_scopes}
                    )

            resource_id = kwargs.get("resource_id")
            if resource_id and not ApiKeyAuthService.check_resource(
                    api_key_obj,
                    resource_id
            ):
                logger.warning("API Key 资源访问被拒绝", extra={
                    "api_key_id": str(api_key_obj.id),
                    "required_resource_id": str(resource_id),
                    "bound_resource_id": str(api_key_obj.resource_id) if api_key_obj.resource_id else None,
                    "endpoint": str(request.url)
                })
                return BusinessException(
                    "API Key 未授权访问该资源",
                    BizCode.API_KEY_INVALID_RESOURCE,
                    context={
                        "required_resource_id": str(resource_id),
                        "bound_resource_id": str(api_key_obj.resource_id)
                    }
                )

            kwargs["api_key_auth"] = ApiKeyAuth(
                api_key_id=api_key_obj.id,
                workspace_id=api_key_obj.workspace_id,
                type=api_key_obj.type,
                scopes=api_key_obj.scopes,
                resource_id=api_key_obj.resource_id,
            )

            rate_limiter = RateLimiterService()
            is_allowed, error_msg, rate_headers = await rate_limiter.check_all_limits(api_key_obj)
            if not is_allowed:
                logger.warning("API Key 限流触发", extra={
                    "api_key_id": str(api_key_obj.id),
                    "endpoint": str(request.url),
                    "method": request.method,
                    "error_msg": error_msg
                })
                # 根据错误消息判断限流类型
                if "QPS" in error_msg:
                    code = BizCode.API_KEY_QPS_LIMIT_EXCEEDED
                elif "Daily" in error_msg:
                    code = BizCode.API_KEY_DAILY_LIMIT_EXCEEDED
                else:
                    code = BizCode.API_KEY_QUOTA_EXCEEDED

                raise RateLimitException(
                    error_msg,
                    code,
                    rate_headers=rate_headers
                )

            start_time = time.perf_counter()
            response = await func(*args, **kwargs)
            end_time = time.perf_counter()
            response_time = (end_time - start_time) * 1000
            if not isinstance(response, Response):
                response = JSONResponse(content=response)
            response = add_rate_limit_headers(response, rate_headers)

            asyncio.create_task(log_api_key_usage(
                db, api_key_obj.id, request, response, response_time
            ))
            return response

        return wrapper

    return decorator


def extract_api_key_from_request(request: Request) -> Optional[str]:
    """从请求中提取 API Key

    支持以下方式：
    1. Authorization: Bearer <api_key>
    2. X-API-Key: <api_key>
    """
    try:
        # 从 Authorization header
        auth_header = request.headers.get("Authorization")
        if auth_header:
            if " " not in auth_header:
                logger.warning("无效的 Authorization header 格式", extra={
                    "auth_header": auth_header[:20] + "..." if len(auth_header) > 20 else auth_header,
                    "endpoint": str(request.url)
                })
                return None
            auth_scheme, auth_token = auth_header.split(" ", 1)
            if auth_scheme.lower() != "bearer":
                logger.warning("无效的认证方案", extra={
                    "auth_scheme": auth_scheme,
                    "endpoint": str(request.url)
                })
                return None
            return auth_token

        # 从 X-API-Key header
        api_key_header = request.headers.get("X-API-Key")
        if api_key_header:
            return api_key_header

        return None
    except Exception as e:
        logger.error(f"提取 API Key 时发生错误: {str(e)}", extra={
            "endpoint": str(request.url)
        })
        return None


async def log_api_key_usage(
        db: Session,
        api_key_id: uuid.UUID,
        request: Request,
        response: Response,
        response_time: float
):
    """记录 API Key 使用日志"""
    try:
        log_data = {
            "id": uuid.uuid4(),
            "api_key_id": api_key_id,
            "endpoint": str(request.url.path),
            "method": request.method,
            "ip_address": request.client.host if request.client else None,
            "user_agent": request.headers.get("User-Agent"),
            "status_code": response.status_code if hasattr(response, "status_code") else None,
            "response_time": round(response_time),
            "tokens_used": None,
            "created_at": datetime.now()
        }

        ApiKeyLogRepository.create(db, log_data)
        ApiKeyRepository.update_usage(db, api_key_id)
        db.commit()
    except Exception as e:
        logger.error(f"未能记录API密钥的使用情况: {e}")

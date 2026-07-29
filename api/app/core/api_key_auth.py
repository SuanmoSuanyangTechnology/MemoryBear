import asyncio
import time
import uuid
from functools import wraps
from typing import Optional, List

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.api_key_utils import add_rate_limit_headers
from app.core.error_codes import BizCode
from app.core.exceptions import (
    BusinessException,
    RateLimitException,
)
from app.core.logging_config import get_api_logger
from app.core.utils.datetime_utils import utcnow_naive
from app.db import get_async_db_context
from app.models.workspace_model import Workspace
from app.repositories.api_key_repository import ApiKeyLogRepository, ApiKeyRepository
from app.schemas.api_key_schema import ApiKeyAuth
from app.services.api_key_service import ApiKeyAuthService, RateLimiterService

logger = get_api_logger()

# ── Async log-writer: batched single-consumer queue ──────────────────
# Replaces per-request ``asyncio.create_task(log_api_key_usage(...))``
# with an unbounded queue + single DB connection, batch-committed.
# Log writes never compete with business requests for connection pool slots.

BATCH_SIZE = 20       # max entries per commit
BATCH_TIMEOUT = 0.5   # seconds — flush partial batch if no new entries arrive

_log_queue: asyncio.Queue | None = None
_consumer_task: asyncio.Task | None = None


async def _consume_logs():
    """Batch consumer — drains up to BATCH_SIZE entries, single commit per batch."""
    while True:
        batch = []
        # Wait for the first entry (blocking)
        try:
            batch.append(await asyncio.wait_for(_log_queue.get(), timeout=BATCH_TIMEOUT))
        except asyncio.TimeoutError:
            continue  # nothing arrived, loop back

        # Drain remaining immediately-available entries
        for _ in range(BATCH_SIZE - 1):
            try:
                batch.append(_log_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        try:
            async with get_async_db_context() as db:
                for log_data in batch:
                    log_data.setdefault("id", uuid.uuid4())
                    await ApiKeyLogRepository.create_async(db, log_data)
                    await ApiKeyRepository.update_usage_async(db, log_data["api_key_id"])
                await db.commit()
        except Exception:
            logger.error("日志批量写入失败 (batch=%d)", len(batch), exc_info=True)
        finally:
            for _ in batch:
                _log_queue.task_done()


def _enqueue_usage_log(log_data: dict):
    """Non-blocking enqueue — unbounded queue, never blocks the request path."""
    if _log_queue is None:
        return
    _log_queue.put_nowait(log_data)


async def start_log_consumer():
    """Call once during app startup."""
    global _log_queue, _consumer_task
    if _consumer_task is not None:
        return
    _log_queue = asyncio.Queue()
    _consumer_task = asyncio.create_task(_consume_logs(), name="log-consumer")
    logger.info("log consumer started (unbounded queue)")


async def stop_log_consumer():
    """Gracefully drain and stop.  Call during app shutdown."""
    global _log_queue, _consumer_task
    if _consumer_task is None:
        return
    _consumer_task.cancel()
    try:
        await _consumer_task
    except asyncio.CancelledError:
        pass
    _consumer_task = None
    _log_queue = None
    logger.info("log consumer stopped")


# ── Decorators ───────────────────────────────────────────────────────


def require_api_key(
        scopes: Optional[List[str]] = None
):
    """
    API Key 鉴权装饰器

    Args:
        scopes: 所需的权限范围列表[“app”, "rag", "memory"]

    Usage:
        @router.get("/app/chat")
        @require_api_key(scopes=["app"])
        def chat_with_app(
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

            ApiKeyAuthService.check_app_published(db, api_key_obj)

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

            # Resolve tenant_id from workspace
            _ws = db.query(Workspace).filter(Workspace.id == api_key_obj.workspace_id).first()
            _tenant_id = _ws.tenant_id if _ws else None

            kwargs["api_key_auth"] = ApiKeyAuth(
                api_key_id=api_key_obj.id,
                workspace_id=api_key_obj.workspace_id,
                tenant_id=_tenant_id,
                type=api_key_obj.type,
                scopes=api_key_obj.scopes,
                resource_id=api_key_obj.resource_id,
            )

            rate_limiter = RateLimiterService()
            is_allowed, error_msg, rate_headers = await rate_limiter.check_all_limits(api_key_obj, db=db)
            if not is_allowed:
                logger.warning("API Key 限流触发", extra={
                    "api_key_id": str(api_key_obj.id),
                    "endpoint": str(request.url),
                    "method": request.method,
                    "error_msg": error_msg
                })
                # 根据错误消息判断限流类型
                if "Daily" in error_msg:
                    code = BizCode.API_KEY_DAILY_LIMIT_EXCEEDED
                elif "Tenant" in error_msg:
                    code = BizCode.API_KEY_QPS_LIMIT_EXCEEDED  # 租户套餐速率超限，同属 QPS 类
                elif "QPS" in error_msg:
                    code = BizCode.API_KEY_QPS_LIMIT_EXCEEDED
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

            _enqueue_usage_log({
                "api_key_id": api_key_obj.id,
                "endpoint": str(request.url.path),
                "method": request.method,
                "ip_address": request.client.host if request.client else None,
                "user_agent": request.headers.get("User-Agent"),
                "status_code": response.status_code if hasattr(response, "status_code") else None,
                "response_time": round(response_time),
                "created_at": utcnow_naive(),
            })
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


def require_api_key_self_db(
        scopes: Optional[List[str]] = None
):
    """
    API Key 鉴权装饰器（自动管理 DB 会话）

    与 require_api_key 功能相同，但自行管理 DB 会话生命周期，
    端点函数不再需要声明 db: Session = Depends(get_db) 参数。

    Args:
        scopes: 所需的权限范围列表["app", "rag", "memory"]

    Usage:
        @router.post("/read/sync")
        @require_api_key_self_db(scopes=["memory"])
        async def read_memory_sync(
            request: Request,
            api_key_auth: ApiKeyAuth = None,
        ):
            # 不需要 db 参数，装饰器内部自行管理
            pass
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request: Request = kwargs.get("request")

            api_key = extract_api_key_from_request(request)
            if not api_key:
                logger.warning("API Key 缺失", extra={
                    "endpoint": str(request.url),
                    "method": request.method,
                    "ip_address": request.client.host if request.client else None
                })
                raise BusinessException("API Key 不存在", BizCode.API_KEY_NOT_FOUND)

            from app.db import get_async_db_context

            async with get_async_db_context() as db:
                api_key_obj = await ApiKeyAuthService.validate_api_key_async(db, api_key)
                if not api_key_obj:
                    logger.warning("API Key 无效或已过期", extra={
                        "key_prefix": api_key[:10] + "..." if len(api_key) > 10 else api_key,
                        "endpoint": str(request.url),
                        "method": request.method,
                        "ip_address": request.client.host if request.client else None
                    })
                    raise BusinessException("API Key 无效或已过期", BizCode.API_KEY_INVALID)

                await ApiKeyAuthService.check_app_published_async(db, api_key_obj)

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

                rate_limiter = RateLimiterService()
                is_allowed, error_msg, rate_headers = await rate_limiter.check_all_limits(api_key_obj, db=db)
                if not is_allowed:
                    logger.warning("API Key 限流触发", extra={
                        "api_key_id": str(api_key_obj.id),
                        "endpoint": str(request.url),
                        "method": request.method,
                        "error_msg": error_msg
                    })
                    if "Daily" in error_msg:
                        code = BizCode.API_KEY_DAILY_LIMIT_EXCEEDED
                    elif "Tenant" in error_msg:
                        code = BizCode.API_KEY_QPS_LIMIT_EXCEEDED
                    elif "QPS" in error_msg:
                        code = BizCode.API_KEY_QPS_LIMIT_EXCEEDED
                    else:
                        code = BizCode.API_KEY_QUOTA_EXCEEDED
                    raise RateLimitException(
                        error_msg,
                        code,
                        rate_headers=rate_headers
                    )

                await db.commit()

                # Resolve tenant_id from workspace (needed by downstream loaders)
                from sqlalchemy import select as sa_select
                _ws_result = await db.execute(
                    sa_select(Workspace.tenant_id).where(Workspace.id == api_key_obj.workspace_id)
                )
                _tenant_id = _ws_result.scalar_one_or_none()

                _api_key_id = api_key_obj.id
                _api_key_auth = ApiKeyAuth(
                    api_key_id=api_key_obj.id,
                    workspace_id=api_key_obj.workspace_id,
                    tenant_id=_tenant_id,
                    type=api_key_obj.type,
                    scopes=api_key_obj.scopes,
                    resource_id=api_key_obj.resource_id,
                )

            kwargs["api_key_auth"] = _api_key_auth

            start_time = time.perf_counter()
            response = await func(*args, **kwargs)
            end_time = time.perf_counter()
            response_time = (end_time - start_time) * 1000

            if not isinstance(response, Response):
                response = JSONResponse(content=response)
            response = add_rate_limit_headers(response, rate_headers)

            _enqueue_usage_log({
                "api_key_id": _api_key_id,
                "endpoint": str(request.url.path),
                "method": request.method,
                "ip_address": request.client.host if request.client else None,
                "user_agent": request.headers.get("User-Agent"),
                "status_code": response.status_code if hasattr(response, "status_code") else None,
                "response_time": round(response_time),
                "created_at": utcnow_naive(),
            })
            return response

        return wrapper

    return decorator

"""基于 API Key 认证的 V1 单用户隐性记忆查询接口。"""

from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from app.core.api_key_auth import get_current_api_key_auth, require_api_key_self_db
from app.core.api_key_utils import validate_end_user_in_workspace_async
from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.core.trace import get_trace_id
from app.db import get_async_db_context, get_db_context
from app.integrations.profile.contracts import (
    MemoryProfileAuthSource,
    MemoryProfileRawResponse,
)
from app.integrations.profile.errors import MemoryProfileClientError
from app.integrations.profile.proxy import forward_memory_profile_request
from app.integrations.profile.runtime import is_memory_profile_enabled
from app.repositories.implicit_emotions_storage_repository import (
    ImplicitEmotionsStorageRepository,
)
from app.schemas.response_schema import ApiResponse

router = APIRouter(
    prefix="/memory/implicit-memory",
    tags=["V1 - Implicit Memory API"],
)
logger = get_api_logger()


@dataclass(frozen=True, slots=True)
class _ApiKeyProfilePrincipal:
    id: UUID
    current_workspace_id: UUID
    tenant_id: UUID | None


async def _forward_implicit_memory(
    *,
    request: Request,
    end_user_id: str,
    current_user: _ApiKeyProfilePrincipal,
) -> Response | dict | None:
    """Forward the API-Key face through the shared profile client helper."""
    if not is_memory_profile_enabled():
        return None

    trace_id = get_trace_id()
    language_type = request.headers.get("X-Language-Type") or request.headers.get(
        "Accept-Language"
    )
    try:
        result = await forward_memory_profile_request(
            method=request.method,
            path=request.scope["path"],
            current_user=current_user,
            source=MemoryProfileAuthSource.APIKEY,
            query={"end_user_id": end_user_id},
            language_type=language_type,
            raw=True,
        )
    except MemoryProfileClientError as exc:
        logger.error(
            "memory profile proxy failed: path=%s error=%s trace_id=%s",
            request.scope["path"],
            type(exc).__name__,
            trace_id,
        )
        return fail(
            BizCode.SERVICE_UNAVAILABLE,
            "记忆画像服务暂不可用",
            "Memory profile service unavailable",
        )
    if isinstance(result, MemoryProfileRawResponse):
        return Response(
            content=result.body,
            media_type=result.media_type,
            status_code=200,
        )
    return None


def _handle_implicit_memory_error(
    exc: Exception,
    operation: str,
    end_user_id: str,
) -> dict:
    """返回固定错误提示；异常详情记录到日志"""
    error_context = f"user_id={end_user_id}"

    if isinstance(exc, ValueError):
        error_text = str(exc).lower()
        if "user" in error_text and "not found" in error_text:
            logger.warning(
                "Invalid user ID for %s: %s", operation, error_context, exc_info=True
            )
            return fail(BizCode.INVALID_USER_ID, "无效的用户ID")
        if "insufficient" in error_text or "no data" in error_text:
            logger.warning(
                "Insufficient data for %s: %s", operation, error_context, exc_info=True
            )
            return fail(BizCode.INSUFFICIENT_DATA, "数据不足，无法进行分析")

        logger.warning(
            "Invalid parameters for %s: %s", operation, error_context, exc_info=True
        )
        return fail(BizCode.INVALID_FILTER_PARAMS, "无效的参数")

    if isinstance(exc, KeyError):
        logger.warning(
            "Missing required data for %s: %s", operation, error_context, exc_info=True
        )
        return fail(BizCode.INSUFFICIENT_DATA, "缺少必要的数据")

    if isinstance(exc, (ConnectionError, TimeoutError)):
        logger.error(
            "Service unavailable for %s: %s", operation, error_context, exc_info=True
        )
        return fail(BizCode.SERVICE_UNAVAILABLE, "服务暂时不可用")

    error_text = str(exc).lower()
    if "analysis" in error_text or "llm" in error_text:
        logger.error(
            "Analysis failed for %s: %s",
            operation,
            error_context,
            exc_info=True,
        )
        return fail(BizCode.ANALYSIS_FAILED, "分析处理失败")

    if "storage" in error_text or "database" in error_text:
        logger.error(
            "Storage error for %s: %s",
            operation,
            error_context,
            exc_info=True,
        )
        return fail(BizCode.PROFILE_STORAGE_ERROR, "数据存储失败")

    logger.error(
        "Unexpected error for %s: %s",
        operation,
        error_context,
        exc_info=True,
    )
    return fail(BizCode.INTERNAL_ERROR, f"{operation}失败")


@router.get("", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_implicit_memory(
    request: Request,
    end_user_id: str = Query(..., description="终端用户ID"),
) -> dict | Response:
    """返回单个终端用户已持久化的隐性记忆快照。"""
    # API Key 的授权边界是 workspace；先校验用户归属，再读取业务数据。
    api_key_auth = get_current_api_key_auth()
    async with get_async_db_context() as auth_db:
        end_user = await validate_end_user_in_workspace_async(
            auth_db,
            end_user_id,
            api_key_auth.workspace_id,
        )
        resolved_end_user_id = str(end_user.id)

    forwarded = await _forward_implicit_memory(
        request=request,
        end_user_id=resolved_end_user_id,
        current_user=_ApiKeyProfilePrincipal(
            id=api_key_auth.api_key_id,
            current_workspace_id=api_key_auth.workspace_id,
            tenant_id=api_key_auth.tenant_id,
        ),
    )
    if forwarded is not None:
        return forwarded

    try:
        # 对合并用户使用归一化后的 ID，直接读取快照，避免初始化分析和配置组件。
        with get_db_context() as db:
            storage = ImplicitEmotionsStorageRepository(db).get_by_end_user_id(
                resolved_end_user_id
            )
            cached_profile = storage.implicit_profile if storage else None

        if cached_profile is None:
            logger.info("用户 %s 的画像数据不存在", resolved_end_user_id)
            return fail(BizCode.NOT_FOUND, "隐性记忆快照尚未生成")

        # 四类数据保持落库原始结构，仅对偏好应用 API 接口的默认置信度阈值。
        habits = cached_profile.get("habits", [])
        preferences = [
            preference
            for preference in cached_profile.get("preferences", [])
            if preference.get("confidence_score", 0) >= 0.5
        ]
        portrait = cached_profile.get("portrait", {})
        interest_areas = cached_profile.get("interest_areas", {})

        return success(
            data={
                "end_user_id": resolved_end_user_id,
                "habits": habits,
                "preferences": preferences,
                "portrait": portrait,
                "interest_areas": interest_areas,
            },
            msg="查询成功",
        )
    except Exception as exc:
        return _handle_implicit_memory_error(
            exc,
            "隐性记忆查询",
            resolved_end_user_id,
        )

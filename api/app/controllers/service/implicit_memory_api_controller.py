"""基于 API Key 认证的 V1 单用户隐性记忆查询接口。"""

from fastapi import APIRouter, Query, Request

from app.core.api_key_auth import get_current_api_key_auth, require_api_key_self_db
from app.core.api_key_utils import validate_end_user_in_workspace_async
from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.db import get_async_db_context, get_db_context
from app.schemas.response_schema import ApiResponse
from app.services.implicit_memory_service import ImplicitMemoryService

router = APIRouter(
    prefix="/memory/implicit-memory",
    tags=["V1 - Implicit Memory API"],
)
logger = get_api_logger()


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
) -> dict:
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

    try:
        # 对合并用户使用归一化后的 ID，并沿用 API 接口的快照读取路径。
        with get_db_context() as db:
            service = ImplicitMemoryService(
                db=db,
                end_user_id=resolved_end_user_id,
            )
            cached_profile = await service.get_cached_profile(
                end_user_id=resolved_end_user_id,
                db=db,
            )

        if cached_profile is None:
            logger.info("用户 %s 的画像数据不存在", resolved_end_user_id)
            return fail(BizCode.NOT_FOUND, "", "")

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

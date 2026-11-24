"""Internal routes for reflection log reads."""

import logging
from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse, legacy_fail_response
from src.middleware.auth import SOURCE_INTERNAL, MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.schemas.reflection_logs import (
    LogStatus,
    ReflectionLogDetail,
    ReflectionLogPage,
    ReflectionLogStats,
    SubProblem,
    TriggerType,
)
from src.services.reflection_logs import ReflectionLogService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memory/reflection/logs", tags=["Memory Reflection"])


async def _require_user_context(
    context: Annotated[MemoryPrincipal, Depends(require_memory_context)],
) -> MemoryPrincipal:
    if context.source != SOURCE_INTERNAL or context.actor_kind != "actor":
        raise ProfileError(BizCode.FORBIDDEN, "User authentication required", status_code=403)
    return context


Context = Annotated[MemoryPrincipal, Depends(_require_user_context)]
EndUserId = Annotated[str, Query(description="终端用户ID")]


async def _respond[T](
    operation: Awaitable[T], *, success_msg: str, error_msg: str
) -> ApiResponse[T] | JSONResponse:
    try:
        return ApiResponse(data=await operation, msg=success_msg)
    except ProfileError as exc:
        return legacy_fail_response(exc.code, exc.message, exc.detail)
    except Exception as exc:
        logger.exception(error_msg)
        return legacy_fail_response(BizCode.INTERNAL_ERROR, error_msg, str(exc))


@router.get("/stats", response_model=ApiResponse[ReflectionLogStats])
async def get_reflection_log_stats(request: Request, context: Context, end_user_id: EndUserId):
    service = ReflectionLogService(request.app.state.runtime.postgres)
    return await _respond(
        service.stats(end_user_id, context.workspace_id),
        success_msg="反思日志统计获取成功",
        error_msg="查询统计失败",
    )


@router.get("/{log_id}", response_model=ApiResponse[ReflectionLogDetail])
async def get_reflection_log_detail(
    request: Request, context: Context, log_id: Annotated[str, Path(description="日志ID")]
):
    service = ReflectionLogService(request.app.state.runtime.postgres)
    return await _respond(
        service.detail(log_id, context.workspace_id),
        success_msg="反思日志详情获取成功",
        error_msg="查询详情失败",
    )


@router.get("", response_model=ApiResponse[ReflectionLogPage])
async def get_reflection_logs(
    request: Request,
    context: Context,
    end_user_id: EndUserId,
    sub_problem: Annotated[SubProblem | None, Query(description="子问题类型筛选")] = None,
    status: Annotated[LogStatus | None, Query(description="状态筛选")] = None,
    trigger_type: Annotated[TriggerType | None, Query(description="触发方式筛选")] = None,
    page: Annotated[int, Query(ge=1, description="页码，从1开始")] = 1,
    pagesize: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 10,
):
    service = ReflectionLogService(request.app.state.runtime.postgres)
    return await _respond(
        service.list_page(
            end_user_id,
            context.workspace_id,
            sub_problem=sub_problem,
            status=status,
            trigger_type=trigger_type,
            page=page,
            pagesize=pagesize,
        ),
        success_msg="反思日志列表获取成功",
        error_msg="查询日志列表失败",
    )

"""JWT management APIs for permanent memories in memory value ranking."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from app.core.error_codes import BizCode
from app.core.response_utils import fail
from app.dependencies import CurrentUserSnapshot, get_current_user_async
from app.integrations.profile.proxy import memory_profile_proxy
from app.schemas.memory_value_ranking_schema import (
    PermanentMemoryListApiResponse,
    PermanentMemoryQuotaApiResponse,
    PermanentMemoryUnmarkApiResponse,
    PermanentMemoryUnmarkRequest,
)

router = APIRouter(
    prefix="/memory/value-ranking",
    tags=["Memory Value Ranking"],
)


def _workspace_id(current_user: CurrentUserSnapshot) -> uuid.UUID | None:
    raw = current_user.current_workspace_id
    if raw is None:
        return None
    try:
        return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None


def _workspace_required() -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间"),
    )


def _proxy_response(result: dict | Response) -> Response:
    # Proxy failures carry empty data, outside the success response model.
    return result if isinstance(result, Response) else JSONResponse(content=result)


@memory_profile_proxy(query_fields=("end_user_id",), preserve_status=True)
async def _forward_quota(
    request: Request, current_user: CurrentUserSnapshot, end_user_id: str
):
    """Forward the validated quota request."""


@memory_profile_proxy(
    query_fields=("end_user_id", "page", "pagesize"),
    preserve_status=True,
)
async def _forward_list(
    request: Request,
    current_user: CurrentUserSnapshot,
    end_user_id: str,
    page: int,
    pagesize: int,
):
    """Forward the validated list request."""


@memory_profile_proxy(
    body_arg="payload",
    body_fields=("end_user_id",),
    preserve_status=True,
)
async def _forward_unmark(
    request: Request,
    current_user: CurrentUserSnapshot,
    payload: PermanentMemoryUnmarkRequest,
):
    """Forward the validated body and the path containing the element id."""


@router.get("/permanent-memories/quota", response_model=PermanentMemoryQuotaApiResponse)
async def get_permanent_memory_quota(
    request: Request,
    current_user: Annotated[CurrentUserSnapshot, Depends(get_current_user_async)],
    end_user_id: Annotated[str, Query()],
) -> Response:
    if _workspace_id(current_user) is None:
        return _workspace_required()
    result = await _forward_quota(
        request=request, current_user=current_user, end_user_id=end_user_id
    )
    return _proxy_response(result)


@router.get("/permanent-memories", response_model=PermanentMemoryListApiResponse)
async def list_permanent_memories(
    request: Request,
    current_user: Annotated[CurrentUserSnapshot, Depends(get_current_user_async)],
    end_user_id: Annotated[str, Query()],
    page: Annotated[int, Query(ge=1)] = 1,
    pagesize: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Response:
    if _workspace_id(current_user) is None:
        return _workspace_required()
    result = await _forward_list(
        request=request,
        current_user=current_user,
        end_user_id=end_user_id,
        page=page,
        pagesize=pagesize,
    )
    return _proxy_response(result)


@router.patch(
    "/permanent-memories/{node_id}",
    response_model=PermanentMemoryUnmarkApiResponse,
    description=(
        "仅将 Statement 从永久记忆集合中移除（is_permanent=false）；"
        "不会删除 Statement 节点、关联边，也不会记录手动遗忘日志。"
    ),
)
async def unmark_permanent_memory(
    request: Request,
    node_id: str,
    payload: PermanentMemoryUnmarkRequest,
    current_user: Annotated[CurrentUserSnapshot, Depends(get_current_user_async)],
) -> Response:
    if _workspace_id(current_user) is None:
        return _workspace_required()
    result = await _forward_unmark(
        request=request, current_user=current_user, payload=payload
    )
    return _proxy_response(result)

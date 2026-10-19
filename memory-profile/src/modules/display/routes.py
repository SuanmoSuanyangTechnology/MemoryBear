"""Internal memory display routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from src.common.external_api import external_api
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.display.handler import DisplayPageApiResponse, MemoryDisplayHandler

router = APIRouter(prefix="/memory-display", tags=["Memory Display"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> MemoryDisplayHandler:
    return MemoryDisplayHandler(request.app.state.runtime)


@router.get("/written", response_model=DisplayPageApiResponse)
@external_api
async def get_written(
    request: Request,
    context: Context,
    end_user_id: str | None = Query(None, description="终端用户 ID；不传则查询整个工作空间"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    start_time: int | None = Query(None, description="起始时间（毫秒 UTC，闭区间）"),
    end_time: int | None = Query(None, description="结束时间（毫秒 UTC，闭区间）"),
) -> DisplayPageApiResponse:
    return await _handler(request).get_written(
        context,
        page=page,
        pagesize=pagesize,
        end_user_id=end_user_id,
        start_time=start_time,
        end_time=end_time,
    )


@router.get("/retrieved", response_model=DisplayPageApiResponse)
@external_api
async def get_retrieved(
    request: Request,
    context: Context,
    end_user_id: str | None = Query(None, description="终端用户 ID；不传则查询整个工作空间"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    start_time: int | None = Query(None, description="起始时间（毫秒 UTC，闭区间）"),
    end_time: int | None = Query(None, description="结束时间（毫秒 UTC，闭区间）"),
) -> DisplayPageApiResponse:
    return await _handler(request).get_retrieved(
        context,
        page=page,
        pagesize=pagesize,
        end_user_id=end_user_id,
        start_time=start_time,
        end_time=end_time,
    )


@router.get("/engines", response_model=DisplayPageApiResponse)
@external_api
async def get_engines(
    request: Request,
    context: Context,
    timezone: str = Query(..., description="IANA 时区名称"),
    language_type: str | None = Header(None, alias="X-Language-Type"),
    end_user_id: str | None = Query(None, description="终端用户 ID；不传则查询整个工作空间"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    start_time: int | None = Query(None, description="起始时间（毫秒 UTC，闭区间）"),
    end_time: int | None = Query(None, description="结束时间（毫秒 UTC，闭区间）"),
) -> DisplayPageApiResponse:
    return await _handler(request).get_engines(
        context,
        timezone=timezone,
        language_type=language_type,
        page=page,
        pagesize=pagesize,
        end_user_id=end_user_id,
        start_time=start_time,
        end_time=end_time,
    )


@router.get("/all", response_model=DisplayPageApiResponse)
@external_api
async def get_all(
    request: Request,
    context: Context,
    timezone: str = Query(..., description="IANA 时区名称"),
    language_type: str | None = Header(None, alias="X-Language-Type"),
    end_user_id: str | None = Query(None, description="终端用户 ID；不传则查询整个工作空间"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    include_engines: bool = Query(True, description="是否包含引擎动态卡片"),
    start_time: int | None = Query(None, description="起始时间（毫秒 UTC，闭区间）"),
    end_time: int | None = Query(None, description="结束时间（毫秒 UTC，闭区间）"),
) -> DisplayPageApiResponse:
    return await _handler(request).get_all(
        context,
        timezone=timezone,
        language_type=language_type,
        page=page,
        pagesize=pagesize,
        end_user_id=end_user_id,
        include_engines=include_engines,
        start_time=start_time,
        end_time=end_time,
    )

"""Internal memory display routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from src.common.external_api import external_api
from src.common.responses import ApiResponse
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.display.handler import (
    AllPageApiResponse,
    EnginePageApiResponse,
    MemoryDisplayHandler,
    RetrievedPageApiResponse,
    WrittenPageApiResponse,
)
from src.schemas.display import ActivityCompositionData, ActivityStatisticsData

router = APIRouter(prefix="/memory-display", tags=["Memory Display"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> MemoryDisplayHandler:
    return MemoryDisplayHandler(request.app.state.runtime)


@router.get("/activity-statistics", response_model=ApiResponse[ActivityStatisticsData | dict])
async def get_yearly_activity_statistics(
    request: Request,
    context: Context,
    year: str | None = Query(None),
    timezone_name: str | None = Header(None, alias="X-Timezone"),
):
    return await _handler(request).get_yearly_activity_statistics(context, year, timezone_name)


@router.get("/activity-composition", response_model=ApiResponse[ActivityCompositionData | dict])
async def get_monthly_activity_composition(
    request: Request,
    context: Context,
    year: str | None = Query(None),
    month: str | None = Query(None),
    timezone_name: str | None = Header(None, alias="X-Timezone"),
):
    return await _handler(request).get_monthly_activity_composition(
        context, year, month, timezone_name
    )


@router.get("/written", response_model=WrittenPageApiResponse)
@external_api
async def get_written(
    request: Request,
    context: Context,
    end_user_id: str | None = Query(None, description="终端用户 ID；不传则查询整个工作空间"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    start_time: int | None = Query(None, description="起始时间（毫秒 UTC，闭区间）"),
    end_time: int | None = Query(None, description="结束时间（毫秒 UTC，闭区间）"),
) -> WrittenPageApiResponse:
    return await _handler(request).get_written(
        context,
        page=page,
        pagesize=pagesize,
        end_user_id=end_user_id,
        start_time=start_time,
        end_time=end_time,
    )


@router.get("/retrieved", response_model=RetrievedPageApiResponse)
@external_api
async def get_retrieved(
    request: Request,
    context: Context,
    end_user_id: str | None = Query(None, description="终端用户 ID；不传则查询整个工作空间"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    start_time: int | None = Query(None, description="起始时间（毫秒 UTC，闭区间）"),
    end_time: int | None = Query(None, description="结束时间（毫秒 UTC，闭区间）"),
) -> RetrievedPageApiResponse:
    return await _handler(request).get_retrieved(
        context,
        page=page,
        pagesize=pagesize,
        end_user_id=end_user_id,
        start_time=start_time,
        end_time=end_time,
    )


@router.get("/engines", response_model=EnginePageApiResponse)
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
) -> EnginePageApiResponse:
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


@router.get("/all", response_model=AllPageApiResponse)
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
) -> AllPageApiResponse:
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

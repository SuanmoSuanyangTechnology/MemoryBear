"""JWT internal workspace activity endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from src.common.responses import ApiResponse
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.memory_display.handler import MemoryDisplayHandler
from src.modules.memory_display.schemas import ActivityCompositionData, ActivityStatisticsData

router = APIRouter(prefix="/memory-display", tags=["Memory Display"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


@router.get("/activity-statistics", response_model=ApiResponse[ActivityStatisticsData | dict])
async def activity_statistics(
    request: Request,
    context: Context,
    year: str | None = Query(None),
    timezone_name: str | None = Header(None, alias="X-Timezone"),
):
    return await MemoryDisplayHandler(request.app.state.runtime).statistics(
        context, year, timezone_name
    )


@router.get("/activity-composition", response_model=ApiResponse[ActivityCompositionData | dict])
async def activity_composition(
    request: Request,
    context: Context,
    year: str | None = Query(None),
    month: str | None = Query(None),
    timezone_name: str | None = Header(None, alias="X-Timezone"),
):
    return await MemoryDisplayHandler(request.app.state.runtime).composition(
        context, year, month, timezone_name
    )

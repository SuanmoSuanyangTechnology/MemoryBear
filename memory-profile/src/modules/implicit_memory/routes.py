"""Internal implicit memory profile routes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from src.common.external_api import external_api
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.implicit_memory.handler import (
    ImplicitMemoryHandler,
    ListApiResponse,
    ObjectApiResponse,
)

router = APIRouter(prefix="/memory/implicit-memory", tags=["Implicit Memory"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> ImplicitMemoryHandler:
    return ImplicitMemoryHandler(request.app.state.runtime)


@router.get("", response_model=ObjectApiResponse)
@external_api
async def aggregate(
    request: Request,
    context: Context,
    end_user_id: str = Query(..., description="终端用户ID"),
) -> ObjectApiResponse:
    return await _handler(request).aggregate(context, end_user_id)


@router.get("/check-data/{end_user_id}", response_model=ObjectApiResponse)
async def check_data(
    request: Request,
    context: Context,
    end_user_id: str,
) -> ObjectApiResponse:
    return await _handler(request).check_data(context, end_user_id)


@router.get("/preferences/{end_user_id}", response_model=ListApiResponse)
async def preferences(
    request: Request,
    context: Context,
    end_user_id: str,
    confidence_threshold: Annotated[
        float, Query(ge=0.0, le=1.0, description="Minimum confidence threshold")
    ] = 0.5,
    tag_category: Annotated[
        str | None, Query(description="Filter by tag category")
    ] = None,
    start_date: Annotated[datetime | None, Query()] = None,
    end_date: Annotated[datetime | None, Query()] = None,
) -> ListApiResponse:
    return await _handler(request).preferences(
        context,
        end_user_id,
        confidence_threshold=confidence_threshold,
        tag_category=tag_category,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/portrait/{end_user_id}", response_model=ObjectApiResponse)
async def portrait(
    request: Request,
    context: Context,
    end_user_id: str,
    include_history: Annotated[bool, Query()] = False,
) -> ObjectApiResponse:
    return await _handler(request).portrait(context, end_user_id)


@router.get("/interest-areas/{end_user_id}", response_model=ObjectApiResponse)
async def interest_areas(
    request: Request,
    context: Context,
    end_user_id: str,
    include_trends: Annotated[bool, Query()] = False,
) -> ObjectApiResponse:
    return await _handler(request).interest_areas(context, end_user_id)


@router.get("/habits/{end_user_id}", response_model=ListApiResponse)
async def habits(
    request: Request,
    context: Context,
    end_user_id: str,
    confidence_level: Literal["high", "medium", "low"] | None = None,
    frequency_pattern: Literal[
        "daily", "weekly", "monthly", "seasonal", "occasional", "event_triggered"
    ]
    | None = None,
    time_period: Literal["current", "past"] | None = None,
) -> ListApiResponse:
    return await _handler(request).habits(
        context,
        end_user_id,
        confidence_level=confidence_level,
        frequency_pattern=frequency_pattern,
        time_period=time_period,
    )

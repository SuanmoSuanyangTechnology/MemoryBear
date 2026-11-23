from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from src.common.responses import ApiResponse
from src.i18n import get_request_language
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.forgetting.handler import ForgettingHandler
from src.modules.forgetting.schemas import ForgettingTriggerRequest
from src.schemas.forgetting import (
    ForgettingCandidatesData,
    ForgettingQuotaData,
    ForgettingTrendItem,
    ForgottenLogsData,
    RefreshCacheData,
)
from src.schemas.operations import ForgettingReport

router = APIRouter(prefix="/memory/forget-memory", tags=["Memory Forgetting Engine"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> ForgettingHandler:
    return ForgettingHandler(request.app.state.runtime)


@router.get("/{end_user_id}/memory_quota", response_model=ApiResponse[ForgettingQuotaData])
async def quota(request: Request, context: Context, end_user_id: str):
    return await _handler(request).dispatch("quota", context, end_user_id)


@router.get(
    "/{end_user_id}/forgetting_trend", response_model=ApiResponse[list[ForgettingTrendItem]]
)
async def trend(
    request: Request, context: Context, end_user_id: str, days: int = Query(7, ge=1, le=90)
):
    return await _handler(request).dispatch("trend", context, end_user_id, days)


@router.get(
    "/{end_user_id}/forgetting_candidates", response_model=ApiResponse[ForgettingCandidatesData]
)
async def candidates(
    request: Request,
    context: Context,
    end_user_id: str,
    page: int = Query(1, ge=1),
    pagesize: int = Query(50, ge=1, le=200),
):
    return await _handler(request).dispatch("candidates", context, end_user_id, page, pagesize)


@router.get("/{end_user_id}/forgotten_logs", response_model=ApiResponse[ForgottenLogsData])
async def forgotten_logs(
    request: Request,
    context: Context,
    end_user_id: str,
    page: int = Query(1, ge=1),
    pagesize: int = Query(10, ge=1, le=100),
):
    return await _handler(request).dispatch("logs", context, end_user_id, page, pagesize)


@router.post("/{end_user_id}/refresh_cache", response_model=ApiResponse[RefreshCacheData])
async def refresh_cache(request: Request, context: Context, end_user_id: str):
    return await _handler(request).dispatch("refresh", context, end_user_id)


@router.post("/trigger", response_model=ApiResponse[ForgettingReport])
async def trigger(request: Request, context: Context, body: ForgettingTriggerRequest):
    return await _handler(request).trigger(context, body, get_request_language(request))

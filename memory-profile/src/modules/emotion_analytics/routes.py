"""HTTP parameter bindings for emotion analytics."""

from __future__ import annotations

import logging
from datetime import UTC, date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request

from src.common.errors import BizCode, ProfileError
from src.common.external_api import external_api
from src.common.responses import ApiResponse
from src.i18n import get_request_language
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.emotion_analytics.handler import EmotionAnalyticsHandler
from src.modules.emotion_analytics.schemas import (
    EmotionHealthRequest,
    EmotionSyncRequest,
    EmotionSuggestionsRequest,
    EmotionTagsRequest,
    EmotionWordcloudRequest,
    GenerateSuggestionsRequest,
)
from src.schemas.emotion_analytics import (
    EmotionHealthData,
    EmotionOverviewData,
    EmotionTagsData,
    EmotionTimelineData,
    EmotionWordcloudData,
)
from src.schemas.operations import EmotionSuggestionsResult

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/memory/emotion-memory", tags=["Emotion Analysis"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]
ObjectResponse = ApiResponse[dict[str, Any]]


def _handler(request: Request) -> EmotionAnalyticsHandler:
    return EmotionAnalyticsHandler(request.app.state.runtime)


@router.post("/tags", response_model=ApiResponse[EmotionTagsData])
@external_api
async def tags(request: Request, context: Context, body: EmotionTagsRequest):
    return await _handler(request).read("tags", context, body, get_request_language(request))


@router.post("/wordcloud", response_model=ApiResponse[EmotionWordcloudData])
@external_api
async def wordcloud(request: Request, context: Context, body: EmotionWordcloudRequest):
    return await _handler(request).read("wordcloud", context, body, get_request_language(request))


@router.post("/health", response_model=ApiResponse[EmotionHealthData])
@external_api
async def health(request: Request, context: Context, body: EmotionHealthRequest):
    return await _handler(request).read("health", context, body, get_request_language(request))


@router.post("/suggestions", response_model=ObjectResponse)
async def suggestions(request: Request, context: Context, body: EmotionSuggestionsRequest):
    return await _handler(request).read("suggestions", context, body, get_request_language(request))


@router.get("/query_emotion_overview", response_model=ApiResponse[EmotionOverviewData])
@external_api
async def overview(
    request: Request,
    context: Context,
    end_user_id: str,
    timezone: str = Header(..., alias="X-Timezone"),
):
    return await _handler(request).daily(
        context, end_user_id, timezone, get_request_language(request), overview=True
    )


@router.get("/query_emotion_timeline", response_model=ApiResponse[EmotionTimelineData])
@external_api
async def timeline(
    request: Request,
    context: Context,
    end_user_id: str,
    timezone: str = Header(..., alias="X-Timezone"),
    start_date: date | None = None,
    end_date: date | None = None,
    sort: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    pagesize: int = Query(5, ge=1, le=10000),
):
    return await _handler(request).daily(
        context,
        end_user_id,
        timezone,
        get_request_language(request),
        overview=False,
        page=page,
        pagesize=pagesize,
        sort=sort,
        start=start_date,
        end=end_date,
    )


@router.post("/generate_suggestions", response_model=ApiResponse[EmotionSuggestionsResult])
async def generate_suggestions(
    request: Request, context: Context, body: GenerateSuggestionsRequest
):
    return await _handler(request).generate_suggestions(
        context, body, get_request_language(request)
    )

@router.post("/sync", response_model=ObjectResponse)
@external_api
async def sync(request: Request, context: Context, body: EmotionSyncRequest):
    if context.actor_kind != "apikey":
        raise ProfileError(BizCode.PERMISSION_DENIED, "此接口需要 API Key", status_code=403)
    service = _handler(request).service
    user = await service.users.resolve_in_workspace(body.end_user_id, context.workspace_id)
    if user is None:
        raise ProfileError(BizCode.USER_NOT_FOUND, "终端用户不存在或不可访问", status_code=404)
    try:
        count = await service.backfill.backfill(user.id, body.start_time, body.end_time)
    except Exception as exc:
        logger.exception("情绪同步失败 user=%s window=[%s, %s)", user.id, body.start_time, body.end_time)
        raise ProfileError(BizCode.INTERNAL_ERROR, "情绪明细同步失败", status_code=500) from exc
    return ApiResponse(data={
        "end_user_id": user.id,
        "total_dialogues": count,
        "start_time": body.start_time.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z"),
        "end_time": body.end_time.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z"),
    }, msg="情绪明细同步成功")

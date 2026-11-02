"""Internal profile analytics routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request

from src.common.external_api import external_api
from src.common.responses import ApiResponse
from src.i18n import get_request_language
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.analytics.handler import AnalyticsHandler
from src.modules.analytics.schemas import (
    CommunityGraphResponse,
    GenerateCacheBatchResult,
    GenerateCacheBody,
    GenerateCacheUserResult,
    GraphDataResponse,
    HotMemoryTag,
    InterestItem,
    MemoryInsightData,
    MemoryTypeStatistic,
    UserSummaryData,
)
from src.schemas.scene_community_graph import SCENE_GRAPH_ERROR_RESPONSES, GraphResponse
from src.schemas.workspace import WorkspaceStatistics

router = APIRouter(prefix="/memory/analytics", tags=["Profile Analytics"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]
#: The legacy end-user profile is JWT-only, so it is not part of the API-key
#: contract and keeps the loose envelope.
ObjectApiResponse = ApiResponse[dict[str, Any]]
GenerateCacheResponse = ApiResponse[GenerateCacheUserResult | GenerateCacheBatchResult]


def _handler(request: Request) -> AnalyticsHandler:
    return AnalyticsHandler(request.app.state.runtime)


@router.get("/memory_insight", response_model=ApiResponse[MemoryInsightData])
@external_api
async def memory_insight(
        request: Request,
        context: Context,
        end_user_id: str = Query(...),
) -> ApiResponse[MemoryInsightData]:
    return await _handler(request).memory_insight(
        context, end_user_id, get_request_language(request)
    )


@router.get("/user_summary", response_model=ApiResponse[UserSummaryData])
@external_api
async def user_summary(
        request: Request,
        context: Context,
        end_user_id: str = Query(...),
) -> ApiResponse[UserSummaryData]:
    return await _handler(request).user_summary(context, end_user_id, get_request_language(request))


@router.get("/node_statistics", response_model=ApiResponse[list[MemoryTypeStatistic]])
@external_api
async def node_statistics(
        request: Request,
        context: Context,
        end_user_id: str = Query(...),
) -> ApiResponse[list[MemoryTypeStatistic]]:
    return await _handler(request).node_statistics(
        context, end_user_id, get_request_language(request)
    )


@router.get("/workspace_statistics", response_model=ApiResponse[WorkspaceStatistics])
@external_api
async def workspace_statistics(
        request: Request, context: Context
) -> ApiResponse[WorkspaceStatistics]:
    return await _handler(request).workspace_statistics(context, get_request_language(request))


@router.get("/graph_data", response_model=ApiResponse[GraphDataResponse])
@external_api
async def graph_data(
        request: Request,
        context: Context,
        end_user_id: str = Query(...),
        node_types: str | None = None,
        limit: int = Query(100, ge=0, le=1000),
        depth: int = Query(1, ge=1, le=3),
        center_node_id: str | None = None,
        per_type_limits: str | None = None,
) -> ApiResponse[GraphDataResponse]:
    return await _handler(request).graph_data(
        context,
        end_user_id=end_user_id,
        node_types=node_types,
        limit=limit,
        depth=depth,
        center_node_id=center_node_id,
        per_type_limits=per_type_limits,
        language=get_request_language(request),
    )


@router.get("/community_graph", response_model=ApiResponse[CommunityGraphResponse])
@external_api
async def community_graph(
        request: Request,
        context: Context,
        end_user_id: str = Query(...),
) -> ApiResponse[CommunityGraphResponse]:
    return await _handler(request).community_graph(
        context, end_user_id, get_request_language(request)
    )


@router.get(
    "/scene_community_graph",
    response_model=GraphResponse,
    responses=SCENE_GRAPH_ERROR_RESPONSES,
    summary="Scene community graph",
)
@external_api
async def scene_community_graph(
    request: Request,
    context: Context,
    end_user_id: str = Query(
        ..., description="End user UUID", json_schema_extra={"format": "uuid"}
    ),
) -> GraphResponse:
    return await _handler(request).scene_community_graph(context, end_user_id)


@router.get("/hot_memory_tags", response_model=ApiResponse[list[HotMemoryTag]])
@external_api
async def hot_memory_tags(
        request: Request,
        context: Context,
        limit: int = 10,
) -> ApiResponse[list[HotMemoryTag]]:
    """工作空间热门记忆标签（api 侧参数无上下界；``limit <= 0`` 回落 10）。"""

    return await _handler(request).hot_memory_tags(
        context, limit, get_request_language(request)
    )


@router.get("/user_profile", response_model=ObjectApiResponse)
async def user_profile(
        request: Request,
        context: Context,
        end_user_id: str | None = None,
) -> ObjectApiResponse:
    """legacy 管理端用户画像；仅 JWT 面使用，不在 API-Key 契约内。"""

    return await _handler(request).user_profile(
        context, end_user_id, get_request_language(request)
    )


@router.get("/interest_distribution", response_model=ApiResponse[list[InterestItem]])
@external_api
async def interest_distribution(
        request: Request,
        context: Context,
        end_user_id: str = Query(...),
        limit: int = Query(5, ge=1, le=5),
) -> ApiResponse[list[InterestItem]]:
    return await _handler(request).interest_distribution(
        context, end_user_id, limit, get_request_language(request)
    )


@router.post("/generate_cache", response_model=GenerateCacheResponse)
@external_api
async def generate_cache(
        request: Request,
        context: Context,
        body: GenerateCacheBody = Body(...),  # noqa: B008
) -> GenerateCacheResponse:
    """手动触发缓存生成。

    body 必填（api JWT 面同样是 ``Body(...)``，缺 body 返回 422）；
    ``{}``（``end_user_id`` 为 null）表示整工作空间批量生成。
    """

    return await _handler(request).generate_cache(
        context, body.end_user_id, get_request_language(request)
    )

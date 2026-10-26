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
from src.modules.analytics.schemas import GenerateCacheBody

router = APIRouter(prefix="/memory/analytics", tags=["Profile Analytics"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]
ObjectApiResponse = ApiResponse[dict[str, Any]]
ListApiResponse = ApiResponse[list[dict[str, Any]]]


def _handler(request: Request) -> AnalyticsHandler:
    return AnalyticsHandler(request.app.state.runtime)


@router.get("/memory_insight", response_model=ObjectApiResponse)
@external_api
async def memory_insight(
    request: Request,
    context: Context,
    end_user_id: str = Query(...),
) -> ObjectApiResponse:
    return await _handler(request).memory_insight(
        context, end_user_id, get_request_language(request)
    )


@router.get("/user_summary", response_model=ObjectApiResponse)
@external_api
async def user_summary(
    request: Request,
    context: Context,
    end_user_id: str = Query(...),
) -> ObjectApiResponse:
    return await _handler(request).user_summary(context, end_user_id, get_request_language(request))


@router.get("/node_statistics", response_model=ListApiResponse)
@external_api
async def node_statistics(
    request: Request,
    context: Context,
    end_user_id: str = Query(...),
) -> ListApiResponse:
    return await _handler(request).node_statistics(
        context, end_user_id, get_request_language(request)
    )


@router.get("/workspace_statistics", response_model=ObjectApiResponse)
@external_api
async def workspace_statistics(request: Request, context: Context) -> ObjectApiResponse:
    return await _handler(request).workspace_statistics(context, get_request_language(request))


@router.get("/graph_data", response_model=ObjectApiResponse)
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
) -> ObjectApiResponse:
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


@router.get("/community_graph", response_model=ObjectApiResponse)
@external_api
async def community_graph(
    request: Request,
    context: Context,
    end_user_id: str = Query(...),
) -> ObjectApiResponse:
    return await _handler(request).community_graph(
        context, end_user_id, get_request_language(request)
    )


@router.get("/hot_memory_tags", response_model=ListApiResponse)
@external_api
async def hot_memory_tags(
    request: Request,
    context: Context,
    limit: int = 10,
) -> ListApiResponse:
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

@router.get("/interest_distribution", response_model=ListApiResponse)
@external_api
async def interest_distribution(
    request: Request,
    context: Context,
    end_user_id: str = Query(...),
    limit: int = Query(5, ge=1, le=5),
) -> ListApiResponse:
    return await _handler(request).interest_distribution(
        context, end_user_id, limit, get_request_language(request)
    )


@router.post("/generate_cache", response_model=ObjectApiResponse)
@external_api
async def generate_cache(
    request: Request,
    context: Context,
    body: GenerateCacheBody = Body(default_factory=GenerateCacheBody),  # noqa: B008
) -> ObjectApiResponse:
    return await _handler(request).generate_cache(
        context, body.end_user_id, get_request_language(request)
    )

"""Internal profile analytics routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request

from src.common.responses import ApiResponse
from src.modules.analytics.dependencies import MemoryRequestContext, require_memory_context
from src.modules.analytics.handler import AnalyticsHandler

router = APIRouter(prefix="/memory/analytics", tags=["Profile Analytics"])
Context = Annotated[MemoryRequestContext, Depends(require_memory_context)]
ObjectApiResponse = ApiResponse[dict[str, Any]]
ListApiResponse = ApiResponse[list[dict[str, Any]]]


def _handler(request: Request) -> AnalyticsHandler:
    return AnalyticsHandler(request.app.state.runtime)


@router.get("/memory_insight", response_model=ObjectApiResponse)
async def memory_insight(
    request: Request,
    context: Context,
    end_user_id: str = Query(...),
) -> ObjectApiResponse:
    return await _handler(request).memory_insight(context, end_user_id)


@router.get("/user_summary", response_model=ObjectApiResponse)
async def user_summary(
    request: Request,
    context: Context,
    end_user_id: str = Query(...),
    language_type: Annotated[str | None, Header(alias="X-Language-Type")] = None,
) -> ObjectApiResponse:
    return await _handler(request).user_summary(context, end_user_id, language_type)


@router.get("/node_statistics", response_model=ListApiResponse)
async def node_statistics(
    request: Request,
    context: Context,
    end_user_id: str = Query(...),
) -> ListApiResponse:
    return await _handler(request).node_statistics(context, end_user_id)


@router.get("/workspace_statistics", response_model=ObjectApiResponse)
async def workspace_statistics(request: Request, context: Context) -> ObjectApiResponse:
    return await _handler(request).workspace_statistics(context)


@router.get("/graph_data", response_model=ObjectApiResponse)
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
    )


@router.get("/community_graph", response_model=ObjectApiResponse)
async def community_graph(
    request: Request,
    context: Context,
    end_user_id: str = Query(...),
) -> ObjectApiResponse:
    return await _handler(request).community_graph(context, end_user_id)

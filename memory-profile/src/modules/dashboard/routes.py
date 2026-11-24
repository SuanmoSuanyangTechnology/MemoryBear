"""Dashboard HTTP bindings (deprecated endpoints are deliberately absent)."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from src.common.external_api import external_api
from src.common.responses import ApiResponse
from src.i18n import get_request_language
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.dashboard.handler import DashboardHandler
from src.modules.dashboard.schemas import GenerateRAGProfileRequest, MemoryCountsRequest
from src.schemas.dashboard import (
    ChunkInsightData,
    ChunkSummaryData,
    DashboardData,
    EndUsersData,
    MemoryCountsData,
    MemoryIncrementData,
    RAGContentData,
    TotalMemoryData,
)
from src.schemas.operations import RAGProfileResult

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> DashboardHandler:
    return DashboardHandler(request.app.state.runtime)


@router.get("/end_users", response_model=ApiResponse[EndUsersData])
@external_api
async def end_users(
    request: Request,
    context: Context,
    page: int = Query(1, ge=1),
    pagesize: int = Query(10, ge=1),
    keyword: str | None = None,
    label: str | None = None,
    workspace_id: UUID | None = None,
):
    return await _handler(request).dispatch(
        "end_users", context, page, pagesize, keyword, label, workspace_id
    )


@router.get("/total_memory_count", response_model=ApiResponse[TotalMemoryData])
@external_api
async def total_memory_count(request: Request, context: Context, end_user_id: str | None = None):
    return await _handler(request).dispatch("total_memory", context, end_user_id)


@router.post("/end_user_memory_counts", response_model=ApiResponse[MemoryCountsData])
@external_api
async def end_user_memory_counts(request: Request, context: Context, body: MemoryCountsRequest):
    return await _handler(request).dispatch("counts", context, body.end_user_ids)


@router.get("/memory_increment_daily", response_model=ApiResponse[MemoryIncrementData])
@external_api
async def memory_increment_daily(
    request: Request, context: Context, start_time: int, end_time: int
):
    return await _handler(request).dispatch("increments", context, start_time, end_time)


@router.get("/rag_content", response_model=ApiResponse[RAGContentData])
async def rag_content(
    request: Request,
    context: Context,
    end_user_id: str,
    page: int = Query(1, ge=1),
    pagesize: int = Query(15, ge=1, le=100),
    keyword: str | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    source: Annotated[list[str] | None, Query()] = None,
    order: Literal["asc", "desc"] = "asc",
):
    return await _handler(request).dispatch(
        "rag_content",
        context,
        end_user_id,
        page,
        pagesize,
        keyword,
        start_time,
        end_time,
        source,
        order,
    )


@router.get("/chunk_summary_tag", response_model=ApiResponse[ChunkSummaryData])
async def chunk_summary_tag(
    request: Request, context: Context, end_user_id: str, limit: int = 15, max_tags: int = 10
):
    return await _handler(request).dispatch("summary", context, end_user_id)


@router.get("/chunk_insight", response_model=ApiResponse[ChunkInsightData])
async def chunk_insight(request: Request, context: Context, end_user_id: str, limit: int = 15):
    return await _handler(request).dispatch("insight", context, end_user_id)


@router.get("/dashboard_data", response_model=ApiResponse[DashboardData])
@external_api
async def dashboard_data(
    request: Request,
    context: Context,
    end_user_id: str | None = None,
    start_date: int | None = None,
    end_date: int | None = None,
):
    return await _handler(request).dispatch(
        "dashboard_data", context, end_user_id, start_date, end_date
    )


@router.post("/generate_rag_profile", response_model=ApiResponse[RAGProfileResult])
async def generate_rag_profile(request: Request, context: Context, body: GenerateRAGProfileRequest):
    return await _handler(request).generate_rag_profile(
        context, body, get_request_language(request)
    )

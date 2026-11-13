"""Internal routes for explicit-memory read endpoints (T07–T10).

Same layout as the working-memory module: ``/internal/v1`` prefix comes from
``src.modules.router``, paths mirror the monolith's public paths so
``memory_profile_proxy`` can forward the caller path unchanged, and every
route is marked ``@external_api`` so the contract snapshot tracks it.

Query constraints reproduce the monolith's FastAPI layer exactly (T08: 422 on
``page/pagesize`` out of bounds), while business validation lives in the
service and answers with HTTP 200 + ``code=1003`` like the monolith's
``fail(...)`` returns.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from src.common.external_api import external_api
from src.common.responses import ApiResponse
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.explicit_memory.handler import ExplicitMemoryHandler
from src.schemas.explicit_memory import (
    DetailsRequest,
    EpisodicListData,
    ExplicitDetailsData,
    OverviewData,
    OverviewRequest,
    SemanticMemoryItem,
)

router = APIRouter(tags=["Explicit Memory"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> ExplicitMemoryHandler:
    return ExplicitMemoryHandler(request.app.state.runtime)


@router.post(
    "/memory/explicit-memory/overview",
    response_model=ApiResponse[OverviewData],
)
@external_api
async def get_explicit_memory_overview(
    request: Request,
    context: Context,
    body: OverviewRequest,
) -> ApiResponse[OverviewData] | JSONResponse:
    return await _handler(request).get_overview(context, body.end_user_id)


@router.get(
    "/memory/explicit-memory/episodics",
    response_model=ApiResponse[EpisodicListData],
)
@external_api
async def get_episodic_memory_list(
    request: Request,
    context: Context,
    end_user_id: str = Query(...),
    page: int = Query(default=1, gt=0),
    pagesize: int = Query(default=10, gt=0, le=100),
    start_date: int | None = Query(default=None),
    end_date: int | None = Query(default=None),
    episodic_type: str = Query(default="all"),
) -> ApiResponse[EpisodicListData] | JSONResponse:
    return await _handler(request).get_episodics(
        context,
        end_user_id,
        page,
        pagesize,
        start_date,
        end_date,
        episodic_type,
    )


@router.get(
    "/memory/explicit-memory/semantics",
    response_model=ApiResponse[list[SemanticMemoryItem]],
)
@external_api
async def get_semantic_memory_list(
    request: Request,
    context: Context,
    end_user_id: str = Query(...),
) -> ApiResponse[list[SemanticMemoryItem]] | JSONResponse:
    return await _handler(request).get_semantics(context, end_user_id)


@router.post(
    "/memory/explicit-memory/details",
    response_model=ApiResponse[ExplicitDetailsData],
)
@external_api
async def get_explicit_memory_details(
    request: Request,
    context: Context,
    body: DetailsRequest,
) -> ApiResponse[ExplicitDetailsData] | JSONResponse:
    return await _handler(request).get_details(context, body)

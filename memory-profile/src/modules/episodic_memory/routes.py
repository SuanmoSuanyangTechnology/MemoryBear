from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.common.responses import ApiResponse
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.episodic_memory.handler import EpisodicMemoryHandler
from src.modules.episodic_memory.schemas import DetailsRequest, OverviewRequest
from src.schemas.episodic_memory import DetailsData, OverviewData

router = APIRouter(tags=["Episodic Memory"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> EpisodicMemoryHandler:
    return EpisodicMemoryHandler(request.app.state.runtime)


@router.post("/memory/episodic-memory/overview", response_model=ApiResponse[OverviewData])
async def overview(
    request: Request, context: Context, body: OverviewRequest
) -> ApiResponse[OverviewData] | JSONResponse:
    return await _handler(request).overview(context, body)


@router.post("/memory/episodic-memory/details", response_model=ApiResponse[DetailsData])
async def details(
    request: Request, context: Context, body: DetailsRequest
) -> ApiResponse[DetailsData] | JSONResponse:
    return await _handler(request).details(context, body)

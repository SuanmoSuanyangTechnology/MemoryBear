"""Internal routes retaining the API short interface contracts."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.short.handler import ShortMemoryHandler
from src.schemas.short_memory import (
    MemoDateType,
    MemoScope,
    ShortTermMemoResponse,
    ShortTermResponse,
)

router = APIRouter(prefix="/memory/short", tags=["Memory"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> ShortMemoryHandler:
    return ShortMemoryHandler(request.app.state.runtime)


@router.get("/memos", response_model=ShortTermMemoResponse)
async def list_short_term_memos(
    request: Request,
    context: Context,
    end_user_id: UUID,
    scope: Annotated[MemoScope, Query()] = "current",
    date_type: Annotated[MemoDateType, Query()] = "all",
    page: int = Query(1, ge=1),
    pagesize: int = Query(20, ge=1, le=100),
) -> dict | JSONResponse:
    return await _handler(request).memos(
        context,
        end_user_id,
        scope,
        date_type,
        page,
        pagesize,
        request,
    )


@router.get("/short_term", response_model=ShortTermResponse)
async def short_term_configs(
    request: Request,
    context: Context,
    end_user_id: str,
    language_type: str | None = Header(default=None, alias="X-Language-Type"),
) -> dict | JSONResponse:
    return await _handler(request).short_term(context, end_user_id)

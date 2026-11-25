"""Internal JWT routes for permanent-memory management."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse
from src.middleware.auth import SOURCE_INTERNAL, MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.value_ranking.handler import ValueRankingHandler
from src.schemas.value_ranking import (
    PermanentMemoryList,
    PermanentMemoryQuota,
    PermanentMemoryUnmarkRequest,
    PermanentMemoryUnmarkResult,
)

router = APIRouter(prefix="/memory/value-ranking", tags=["Memory Value Ranking"])


async def _require_user_context(
    context: Annotated[MemoryPrincipal, Depends(require_memory_context)],
) -> MemoryPrincipal:
    if context.source != SOURCE_INTERNAL or context.actor_kind != "actor":
        raise ProfileError(BizCode.FORBIDDEN, "User authentication required", status_code=403)
    return context


Context = Annotated[MemoryPrincipal, Depends(_require_user_context)]


@router.get("/permanent-memories/quota", response_model=ApiResponse[PermanentMemoryQuota])
async def get_permanent_memory_quota(
    request: Request, context: Context, end_user_id: Annotated[str, Query()]
) -> ApiResponse[PermanentMemoryQuota]:
    handler = ValueRankingHandler(request.app.state.runtime)
    return await handler.get_quota(context, end_user_id)


@router.get("/permanent-memories", response_model=ApiResponse[PermanentMemoryList])
async def list_permanent_memories(
    request: Request,
    context: Context,
    end_user_id: Annotated[str, Query()],
    page: Annotated[int, Query(ge=1)] = 1,
    pagesize: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[PermanentMemoryList]:
    handler = ValueRankingHandler(request.app.state.runtime)
    return await handler.list_memories(context, end_user_id, page, pagesize)


@router.patch(
    "/permanent-memories/{node_id}",
    response_model=ApiResponse[PermanentMemoryUnmarkResult],
)
async def unmark_permanent_memory(
    request: Request, context: Context, node_id: str, payload: PermanentMemoryUnmarkRequest
) -> ApiResponse[PermanentMemoryUnmarkResult]:
    handler = ValueRankingHandler(request.app.state.runtime)
    return await handler.unmark(context, node_id, payload.end_user_id)

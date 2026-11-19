"""Internal memory-storage routes mirroring the manager API."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from src.common.responses import ApiResponse
from src.i18n import get_request_language
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.memory_storage.handler import MemoryStorageHandler
from src.schemas.memory_storage import DeleteNodeRequest, PilotRunInput, TrialRunChatInput

router = APIRouter(prefix="/memory-storage", tags=["Memory Storage"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> MemoryStorageHandler:
    return MemoryStorageHandler(request.app.state.runtime)


@router.get("/memory_space/timeline_memories", response_model=ApiResponse[Any])
async def timeline_memories(request: Request, context: Context, id: str, label: str):
    return await _handler(request).timeline_memories(context, id, label)


@router.get("/memory_space/entity_timeline", response_model=ApiResponse[Any])
async def entity_timeline(
    request: Request,
    context: Context,
    id: str | None = None,
    end_user_id: str | None = None,
    type: str = "all",
    sort: str = "time_desc",
    from_ts: int | None = None,
    to_ts: int | None = None,
    page: int = Query(1, ge=1),
    pagesize: int = Query(10, ge=1, le=100),
):
    return await _handler(request).entity_timeline(
        context,
        id=id,
        end_user_id=end_user_id,
        type=type,
        sort=sort,
        from_ts=from_ts,
        to_ts=to_ts,
        page=page,
        pagesize=pagesize,
    )


@router.get("/memory_space/relationship_evolution", response_model=ApiResponse[Any])
async def relationship_evolution(request: Request, context: Context, id: str, label: str):
    return await _handler(request).relationship_evolution(context, id, label)


@router.post("/pilot_run", response_model=None)
async def pilot_run(request: Request, context: Context, payload: PilotRunInput):
    return StreamingResponse(
        _handler(request).pilot_run(context, payload, get_request_language(request)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/chat", response_model=None)
async def chat(request: Request, context: Context, payload: TrialRunChatInput):
    return StreamingResponse(
        _handler(request).chat(context, payload, get_request_language(request)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/end-users/{end_user_id}", response_model=ApiResponse[Any])
async def delete_end_user(request: Request, context: Context, end_user_id: UUID):
    return await _handler(request).delete_end_user(context, end_user_id)


@router.post("/node/delete", response_model=ApiResponse[Any])
async def delete_node(request: Request, context: Context, payload: DeleteNodeRequest):
    return await _handler(request).delete_node(context, payload)

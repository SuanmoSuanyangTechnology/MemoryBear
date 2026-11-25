"""Internal memory-read routes.

Four interfaces, one core:

* ``POST /memory/read/sync`` — JSON, API-key reachable (``@external_api``);
* ``POST /memory/read/internal`` — JSON with retrieval options, API-key
  reachable (``@external_api``);
* ``POST /memory/read/stream`` — SSE, management face only.
* ``POST /memory/read/prediction-statements`` — JSON, prediction recall.

All call the same application use cases through ``src.core.composition``; the
transport layer never touches retrieval, storage or model code.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.common.external_api import external_api
from src.common.responses import ApiResponse
from src.middleware.auth import SOURCE_INTERNAL, MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.memory_read.handler import MemoryReadHandler
from src.modules.memory_read.schemas import (
    PredictionStatementReadBody,
    ReadInternalBody,
    ReadStreamBody,
    ReadSyncBody,
)

router = APIRouter(prefix="/memory/read", tags=["Memory Read"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]

#: The legacy V1 read returns a loose payload (single host or map of hosts).
SyncResponse = ApiResponse[dict[str, Any]]


def _handler(request: Request) -> MemoryReadHandler:
    return MemoryReadHandler(request.app.state.runtime)


@router.post(
    "/sync",
    response_model=SyncResponse,
    summary="Read memory synchronously (one or many end users)",
)
@external_api
async def read_memory_sync(
    request: Request,
    context: Context,
    body: ReadSyncBody = Body(...),  # noqa: B008
) -> SyncResponse:
    return await _handler(request).read_sync(request, context, body)


@router.post(
    "/internal",
    response_model=SyncResponse,
    summary="Read memory synchronously with retrieval options (internal)",
)
@external_api
async def read_memory_internal(
    request: Request,
    context: Context,
    body: ReadInternalBody = Body(...),  # noqa: B008
) -> SyncResponse:
    return await _handler(request).read_internal(request, context, body)


@router.post("/prediction-statements", response_model=SyncResponse)
async def read_prediction_statements(
    request: Request,
    context: Context,
    body: PredictionStatementReadBody,
) -> SyncResponse:
    if context.source != SOURCE_INTERNAL:
        raise HTTPException(status_code=403, detail="user context is required")
    return await _handler(request).read_prediction_statements(request, context, body)


@router.post(
    "/stream",
    response_model=None,
    responses={
        200: {"content": {"text/event-stream": {}}},
    },
    summary="Read memory with server-sent progress events",
)
async def read_memory_stream(
    request: Request,
    context: Context,
    body: ReadStreamBody = Body(...),  # noqa: B008
) -> StreamingResponse:
    return await _handler(request).read_stream(request, context, body)


__all__ = ["router"]

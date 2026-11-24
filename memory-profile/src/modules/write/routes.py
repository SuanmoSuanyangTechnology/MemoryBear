"""Internal routes for the ``/memory`` endpoints this service still serves.

``POST /memory/write`` and ``POST /memory/emotion-reply`` were removed: the
write pipeline belongs to memory-jobs and the API keeps its own local
emotion-reply implementation. Only merge and the placeholder info route remain.
The host maps its own ``/api`` (JWT face) and ``/v1`` (API-key face) paths onto
these internal paths; one route serves both faces because the principal carries
the face.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.write.handler import MemoryHandler
from src.schemas.write import MergeUserInput, WriteApiResponse

router = APIRouter(prefix="/memory", tags=["Memory"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> MemoryHandler:
    return MemoryHandler(request.app.state.runtime)


@router.post("/merge", response_model=None)
async def merge_memory(
        request: Request,
        context: Context,
        payload: MergeUserInput,
) -> WriteApiResponse | JSONResponse:
    return await _handler(request).merge(
        context, payload.model_dump(mode="json")
    )


@router.get("")
async def get_memory_info(request: Request, context: Context):
    del context
    return _handler(request).memory_info()
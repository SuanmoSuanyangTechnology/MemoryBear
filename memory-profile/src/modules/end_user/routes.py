"""End-user callbacks the application service makes over the internal network.

Two faces, one module: the domain-info projection read and the info update whose
memory-side effect lives here. Both authenticate like every other internal
endpoint (the internal token's principal), and the workspace always comes from
that principal, never from the body.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Request

from src.common.responses import ApiResponse
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.end_user.handler import EndUserHandler
from src.modules.end_user.schemas import DomainInfoBody, InfoUpdateBody

router = APIRouter(prefix="/end-user", tags=["End User"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]

ObjectResponse = ApiResponse[dict]


def _handler(request: Request) -> EndUserHandler:
    return EndUserHandler(request.app.state.runtime)


@router.post(
    "/domain-profile",
    response_model=ObjectResponse,
    summary="End user's assembled domain_specific_info",
)
async def end_user_domain_profile(
    request: Request,
    context: Context,
    body: DomainInfoBody = Body(...),  # noqa: B008
) -> ObjectResponse:
    return await _handler(request).domain_info(context, body)


@router.post(
    "/info-update",
    response_model=ObjectResponse,
    summary="Update an end user's info and sync the memory-side effect",
)
async def end_user_info_update(
    request: Request,
    context: Context,
    body: InfoUpdateBody = Body(...),  # noqa: B008
) -> ObjectResponse:
    return await _handler(request).info_update(context, body)


__all__ = ["router"]

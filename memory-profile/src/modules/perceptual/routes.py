"""Internal perceptual memory routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.perceptual.constants import PerceptualType
from src.modules.perceptual.handler import (
    LatestApiResponse,
    ObjectApiResponse,
    PerceptualMemoryHandler,
)

router = APIRouter(prefix="/memory/perceptual", tags=["Perceptual Memory"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> PerceptualMemoryHandler:
    return PerceptualMemoryHandler(request.app.state.runtime)


@router.get("/{end_user_id}/count", response_model=ObjectApiResponse)
async def count(
    request: Request,
    context: Context,
    end_user_id: str,
) -> ObjectApiResponse:
    return await _handler(request).count(context, end_user_id)


@router.get("/{end_user_id}/timeline", response_model=ObjectApiResponse)
async def timeline(
    request: Request,
    context: Context,
    end_user_id: str,
    perceptual_type: Annotated[
        PerceptualType | None, Query(description="感知类型过滤")
    ] = None,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页大小")] = 10,
) -> ObjectApiResponse:
    return await _handler(request).timeline(
        context,
        end_user_id,
        perceptual_type=perceptual_type,
        page=page,
        page_size=page_size,
    )


@router.get("/{end_user_id}/last_visual", response_model=LatestApiResponse)
async def last_visual(
    request: Request,
    context: Context,
    end_user_id: str,
) -> LatestApiResponse:
    return await _handler(request).latest(
        context, end_user_id, PerceptualType.VISION
    )


@router.get("/{end_user_id}/last_listen", response_model=LatestApiResponse)
async def last_listen(
    request: Request,
    context: Context,
    end_user_id: str,
) -> LatestApiResponse:
    return await _handler(request).latest(
        context, end_user_id, PerceptualType.AUDIO
    )


@router.get("/{end_user_id}/last_text", response_model=LatestApiResponse)
async def last_text(
    request: Request,
    context: Context,
    end_user_id: str,
) -> LatestApiResponse:
    return await _handler(request).latest(
        context, end_user_id, PerceptualType.TEXT
    )

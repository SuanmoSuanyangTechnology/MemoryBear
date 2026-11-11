"""Internal routes for working-memory read endpoints (T01–T04).

All routes live under ``/internal/v1`` and are registered with
``@external_api`` so the contract snapshot tracks them.  The monolith's
``memory_profile_proxy`` forwards to these paths when
``ENABLE_MEMORY_PROFILE`` is true at API startup.

Response ``msg`` strings stay hardcoded on purpose: the monolith returns the
same literals regardless of request language, so these endpoints do not
consume the i18n middleware.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from src.common.external_api import external_api
from src.common.responses import ApiResponse
from src.middleware.auth import MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.working_memory.handler import WorkingMemoryHandler
from src.schemas.working_memory import (
    ConversationsData,
    MessagesData,
    SourceMessagesData,
    SourceStatItem,
)

router = APIRouter(tags=["Working Memory"])
Context = Annotated[MemoryPrincipal, Depends(require_memory_context)]


def _handler(request: Request) -> WorkingMemoryHandler:
    return WorkingMemoryHandler(request.app.state.runtime)


@router.get(
    "/memory/work/{end_user_id}/conversations",
    response_model=ApiResponse[ConversationsData],
)
@external_api
async def get_conversations(
    request: Request,
    context: Context,
    end_user_id: uuid.UUID,
    page: int = 1,
    pagesize: int = 20,
) -> ApiResponse[ConversationsData]:
    return await _handler(request).get_conversations(context, str(end_user_id), page, pagesize)


@router.get(
    "/memory/work/{end_user_id}/messages",
    response_model=ApiResponse[MessagesData],
)
@external_api
async def get_messages(
    request: Request,
    context: Context,
    end_user_id: uuid.UUID,
    conversation_id: str = Query(...),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None),
    start_date: int | None = Query(default=None, ge=0),
    end_date: int | None = Query(default=None, ge=0),
) -> ApiResponse[MessagesData] | JSONResponse:
    return await _handler(request).get_messages(
        context,
        conversation_id,
        page,
        pagesize,
        keyword,
        start_date,
        end_date,
    )


@router.get(
    "/memory/work/{end_user_id}/sources",
    response_model=ApiResponse[list[SourceStatItem]],
)
@external_api
async def get_sources(
    request: Request,
    context: Context,
    end_user_id: uuid.UUID,
) -> ApiResponse[list[SourceStatItem]]:
    return await _handler(request).get_sources(context, str(end_user_id))


@router.get(
    "/memory/work/{end_user_id}/source_messages",
    response_model=ApiResponse[SourceMessagesData],
)
@external_api
async def get_source_messages(
    request: Request,
    context: Context,
    end_user_id: uuid.UUID,
    source: str = Query(...),
    page: int = Query(default=1, ge=1),
    pagesize: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None),
    start_date: int | None = Query(default=None, ge=0),
    end_date: int | None = Query(default=None, ge=0),
) -> ApiResponse[SourceMessagesData] | JSONResponse:
    return await _handler(request).get_source_messages(
        context,
        str(end_user_id),
        source,
        page,
        pagesize,
        keyword,
        start_date,
        end_date,
    )

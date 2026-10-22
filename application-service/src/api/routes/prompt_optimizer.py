"""Prompt-optimizer routes (management CRUD + streaming optimization).

Port of the monolith ``prompt_optimizer_controller``: five synchronous
management-plane endpoints plus one SSE optimization stream. The stream keeps
the monolith framing verbatim (``event:start`` / ``event:message`` /
``event:error`` / ``event:end``); its database work runs on a per-stream async
context (no sync session inside the event loop).
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...db import get_async_db_context
from ...schemas.prompt_optimizer_schema import (
    CreateSessionResponse,
    PromptOptMessage,
    PromptSaveRequest,
    SessionHistoryResponse,
    SessionMessage,
)
from ...services.prompt_optimizer_service import PromptOptimizerService
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse

router = APIRouter(prefix="/prompt", tags=["Prompts-Optimization"])

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]


@router.post(
    "/sessions",
    summary="Create a new prompt optimization session",
    response_model=ApiResponse,
)
def create_prompt_session(db: DbSession, principal: Caller):
    service = PromptOptimizerService(db)
    session = service.create_session(principal.tenant_id, principal.actor_id)
    result_schema = CreateSessionResponse.model_validate(session)
    return success(data=result_schema)


@router.get(
    "/sessions/{session_id}",
    summary="获取 prompt 优化历史对话",
    response_model=ApiResponse,
)
def get_prompt_session(session_id: uuid.UUID, db: DbSession, principal: Caller):
    service = PromptOptimizerService(db)

    history = service.get_session_message_history(
        session_id=session_id,
        user_id=principal.actor_id,
    )

    messages = [
        SessionMessage(role=role, content=content) for role, content in history
    ]

    result = SessionHistoryResponse(session_id=session_id, messages=messages)

    return success(data=result)


@router.post(
    "/sessions/{session_id}/messages",
    summary="Get prompt optimization",
)
async def get_prompt_opt(
    session_id: uuid.UUID,
    data: PromptOptMessage,
    principal: Caller,
):
    async def event_generator():
        yield "event:start\ndata: {}\n\n"
        try:
            async with get_async_db_context() as db:
                service = PromptOptimizerService(db)
                async for chunk in service.optimize_prompt(
                    tenant_id=principal.tenant_id,
                    model_id=data.model_id,
                    session_id=session_id,
                    user_id=principal.actor_id,
                    current_prompt=data.current_prompt,
                    user_require=data.message,
                    skill=data.skill,
                ):
                    yield f"event:message\ndata: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"event:error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        yield "event:end\ndata: {}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/releases",
    summary="Get prompt optimization",
    response_model=ApiResponse,
)
def save_prompt(data: PromptSaveRequest, db: DbSession, principal: Caller):
    service = PromptOptimizerService(db)
    prompt_info = service.save_prompt(
        tenant_id=principal.tenant_id,
        session_id=data.session_id,
        title=data.title,
        prompt=data.prompt,
    )
    return success(data=prompt_info)


@router.delete(
    "/releases/{prompt_id}",
    summary="Delete prompt (soft delete)",
    response_model=ApiResponse,
)
def delete_prompt(prompt_id: uuid.UUID, db: DbSession, principal: Caller):
    service = PromptOptimizerService(db)
    service.delete_prompt(tenant_id=principal.tenant_id, prompt_id=prompt_id)
    return success(msg="Prompt deleted successfully")


@router.get(
    "/releases/list",
    summary="Get paginated list of released prompts with optional filter",
    response_model=ApiResponse,
)
def get_release_list(
    db: DbSession,
    principal: Caller,
    page: int = 1,
    pagesize: int = 20,
    keyword: str | None = None,
):
    service = PromptOptimizerService(db)
    result = service.get_release_list(
        tenant_id=principal.tenant_id,
        page=max(1, page),
        page_size=min(max(1, pagesize), 100),
        filter_keyword=keyword,
    )
    return success(data=result)


__all__ = ["router"]

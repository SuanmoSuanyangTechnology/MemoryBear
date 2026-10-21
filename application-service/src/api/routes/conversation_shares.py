"""Conversation-share routes (host ``message_interaction_controller`` parity).

Management face: create / revoke / list a conversation's share links. The
shared-view read is anonymous (no user credentials) — the gateway forwards it
without terminating a user credential, and the shape stays read-only.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...guards.workspace_access import require_workspace_access
from ...schemas import conversation_share_schema
from ...services.conversation_share_service import ConversationShareService
from ..dependencies import Principal, get_principal, get_sync_db
from ..schemas.common import success
from ..schemas.response_schema import ApiResponse

router = APIRouter(prefix="/apps", tags=["Conversation Share"])

logger = logging.getLogger(__name__)

DbSession = Annotated[Session, Depends(get_sync_db)]
Caller = Annotated[Principal, Depends(get_principal)]
WorkspaceId = Annotated[UUID, Depends(require_workspace_access)]


@router.post(
    "/{app_id}/conversations/{conversation_id}/share",
    summary="分享会话",
    response_model=ApiResponse,
)
def share_conversation(
    app_id: UUID,
    conversation_id: UUID,
    payload: conversation_share_schema.ShareConversationRequest,
    workspace_id: WorkspaceId,
    principal: Caller,
    db: DbSession,
):
    """Create a public read-only link for one conversation."""

    service = ConversationShareService(db)
    result = service.create_share(
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        user_id=principal.actor_id,
        password=payload.password,
        expire_hours=payload.expire_hours,
        allow_copy=payload.allow_copy,
    )

    return success(data=conversation_share_schema.ShareConversationResponse(**result))


@router.get("/share/{share_uuid}", summary="访问分享的会话", response_model=ApiResponse)
def get_shared_conversation(
    share_uuid: str,
    db: DbSession,
    password: str | None = None,
):
    """Read a shared conversation (read-only view, no login required)."""

    service = ConversationShareService(db)
    result = service.get_shared_conversation(share_uuid, password)

    return success(data=result)


@router.delete(
    "/{app_id}/conversations/{conversation_id}/share/{share_uuid}",
    summary="撤销分享链接",
    response_model=ApiResponse,
)
def revoke_share(
    app_id: UUID,
    conversation_id: UUID,
    share_uuid: str,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    """Deactivate one share link of the acting workspace."""

    service = ConversationShareService(db)
    service.revoke_share(share_uuid, workspace_id)

    return success(msg="分享链接已撤销")


@router.get(
    "/{app_id}/conversations/{conversation_id}/shares",
    summary="列出会话的分享链接",
    response_model=ApiResponse,
)
def list_conversation_shares(
    app_id: UUID,
    conversation_id: UUID,
    workspace_id: WorkspaceId,
    db: DbSession,
):
    """List all active share links of one conversation."""

    service = ConversationShareService(db)
    result = service.list_shares(conversation_id, workspace_id)

    return success(data=result)

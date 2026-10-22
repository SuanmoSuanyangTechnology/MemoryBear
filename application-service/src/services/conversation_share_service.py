"""Conversation-share service (frozen port of the host ``ConversationShareService``).

Direct data access kept verbatim (the host has no repository layer on this
path). Share creation and management are the management face; the shared-view
read is the anonymous leg the gateway forwards without user credentials.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from ..config import current_settings
from ..errors import BizCode, BusinessException
from ..models.conversation_model import Conversation, Message
from ..models.conversation_share_model import ConversationShare
from ..utils.datetime_utils import to_timestamp_ms, utcnow_naive

logger = logging.getLogger(__name__)


class ConversationShareService:
    """Conversation share service."""

    def __init__(self, db: Session):
        self.db = db

    def create_share(
        self,
        conversation_id: UUID,
        workspace_id: UUID,
        user_id: UUID,
        password: str | None = None,
        expire_hours: int | None = None,
        allow_copy: bool = True,
    ) -> dict[str, Any]:
        """Create a read-only share link for one conversation."""

        conv = self.db.get(Conversation, conversation_id)
        if not conv:
            raise BusinessException("会话不存在", BizCode.NOT_FOUND)

        share_uuid = str(uuid4())[:8]

        expire_at = None
        if expire_hours:
            expire_at = utcnow_naive() + timedelta(hours=expire_hours)

        share = ConversationShare(
            conversation_id=conversation_id,
            share_uuid=share_uuid,
            workspace_id=workspace_id,
            created_by=user_id,
            password=password,
            expire_at=expire_at,
            allow_copy=allow_copy,
        )
        self.db.add(share)
        self.db.commit()
        self.db.refresh(share)

        share_url = f"{current_settings().file_local_server_url}/apps/share/{share_uuid}"

        logger.info(
            "Conversation share created: share_uuid=%s, conversation_id=%s, user_id=%s",
            share_uuid,
            conversation_id,
            user_id,
        )

        return {
            "share_id": str(share.id),
            "share_uuid": share_uuid,
            "share_url": share_url,
            "expire_at": to_timestamp_ms(expire_at),
            "has_password": bool(password),
        }

    def get_shared_conversation(
        self,
        share_uuid: str,
        password: str | None = None,
    ) -> dict[str, Any]:
        """Read a shared conversation in read-only mode (anonymous access)."""

        share = self.db.query(ConversationShare).filter(
            ConversationShare.share_uuid == share_uuid,
            ConversationShare.is_active.is_(True),
        ).first()

        if not share:
            raise BusinessException("分享链接不存在或已失效", BizCode.NOT_FOUND)

        if share.expire_at and share.expire_at < utcnow_naive():
            raise BusinessException("分享链接已过期", BizCode.FORBIDDEN)

        if share.password and share.password != password:
            raise BusinessException("访问密码错误", BizCode.FORBIDDEN)

        share.view_count += 1
        self.db.commit()

        messages = self.db.query(Message).filter(
            Message.conversation_id == share.conversation_id,
            Message.is_deleted.is_not(True),
            Message.is_current.is_not(False),
        ).order_by(Message.created_at).all()

        logger.info(
            "Conversation share accessed: share_uuid=%s, view_count=%s",
            share_uuid,
            share.view_count,
        )

        return {
            "conversation_id": str(share.conversation_id),
            "messages": [
                {
                    "id": str(msg.id),
                    "role": msg.role,
                    "content": msg.content,
                    "created_at": to_timestamp_ms(msg.created_at),
                    "meta_data": msg.meta_data,
                    "feedback_type": None,
                    "feedback_content": None,
                }
                for msg in messages
            ],
            "allow_copy": share.allow_copy,
            "is_readonly": True,
        }

    def revoke_share(self, share_uuid: str, workspace_id: UUID) -> None:
        """Deactivate a share link of the acting workspace."""

        share = self.db.query(ConversationShare).filter(
            ConversationShare.share_uuid == share_uuid,
            ConversationShare.workspace_id == workspace_id,
        ).first()

        if not share:
            raise BusinessException("分享链接不存在", BizCode.NOT_FOUND)

        share.is_active = False
        self.db.commit()

        logger.info(
            "Conversation share revoked: share_uuid=%s, workspace_id=%s",
            share_uuid,
            workspace_id,
        )

    def list_shares(self, conversation_id: UUID, workspace_id: UUID) -> list[dict[str, Any]]:
        """List the active share links of one conversation, newest first."""

        shares = self.db.query(ConversationShare).filter(
            ConversationShare.conversation_id == conversation_id,
            ConversationShare.workspace_id == workspace_id,
            ConversationShare.is_active.is_(True),
        ).order_by(ConversationShare.created_at.desc()).all()

        return [
            {
                "share_id": str(s.id),
                "share_uuid": s.share_uuid,
                "share_url": f"{current_settings().file_local_server_url}/share/{s.share_uuid}",
                "view_count": s.view_count,
                "expire_at": to_timestamp_ms(s.expire_at),
                "has_password": bool(s.password),
                "allow_copy": s.allow_copy,
                "created_at": to_timestamp_ms(s.created_at),
            }
            for s in shares
        ]


__all__ = ["ConversationShareService"]

"""Share-link chat support (frozen port of the host ``SharedChatService``).

Only the current (non-deprecated) public face is ported: share resolution with
password check and view counting, plus the conversation create/get/list
helpers behind the public share view. The deprecated chat entry points were
superseded by ``AppChatService`` on the host and are not carried over.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException, ResourceNotFoundException
from ..models.app_release_model import AppRelease
from ..models.conversation_model import Conversation
from ..models.release_share_model import ReleaseShare
from .conversation_service import ConversationService
from .release_share_service import ReleaseShareService

logger = logging.getLogger(__name__)


class SharedChatService:
    """Share-link chat support: release resolution and conversation helpers."""

    def __init__(self, db: Session | AsyncSession):
        self.db = db
        self.conversation_service = ConversationService(db)
        self.share_service = ReleaseShareService(db)

    def get_release_by_share_token(
        self,
        share_token: str,
        password: str | None = None,
    ) -> tuple[ReleaseShare, AppRelease]:
        """Resolve the shared release behind a token (password-gated shares
        require the password to be supplied)."""

        share = self.share_service.repo.get_by_share_token(share_token)
        if not share:
            raise ResourceNotFoundException("分享链接", share_token)

        if not share.is_enabled:
            raise BusinessException("该分享链接已被禁用", BizCode.SHARE_DISABLED)

        if share.require_password:
            if not password:
                raise BusinessException("需要提供访问密码", BizCode.PASSWORD_REQUIRED)

            if not self.share_service.verify_password(share_token, password):
                raise BusinessException("访问密码错误", BizCode.INVALID_PASSWORD)

        release = self.db.get(AppRelease, share.release_id)
        if not release:
            raise ResourceNotFoundException("发布版本", str(share.release_id))

        # View counting is best-effort and never blocks the response.
        try:
            self.share_service.repo.increment_view_count(share.id)
        except Exception as e:
            logger.warning(f"更新访问统计失败: {str(e)}")

        return share, release

    async def get_release_by_share_token_async(
        self,
        share_token: str,
        password: str | None = None,
    ) -> tuple[ReleaseShare, AppRelease]:
        """Async twin of ``get_release_by_share_token`` for the runtime plane."""

        share = await self.share_service.repo.get_by_share_token_async(share_token)
        if not share:
            raise ResourceNotFoundException("分享链接", share_token)

        if not share.is_enabled:
            raise BusinessException("该分享链接已被禁用", BizCode.SHARE_DISABLED)

        if share.require_password:
            if not password:
                raise BusinessException("需要提供访问密码", BizCode.PASSWORD_REQUIRED)
            if not self.share_service.verify_password(share_token, password):
                raise BusinessException("访问密码错误", BizCode.INVALID_PASSWORD)

        release = await self.db.get(AppRelease, share.release_id)
        if not release:
            raise ResourceNotFoundException("发布版本", str(share.release_id))

        try:
            await self.share_service.repo.increment_view_count_async(share.id)
        except Exception as e:
            logger.warning(f"更新访问统计失败: {str(e)}")

        return share, release

    def create_or_get_conversation(
        self,
        share_token: str,
        conversation_id: uuid.UUID | None = None,
        user_id: str | None = None,
        password: str | None = None,
    ) -> Conversation:
        """Return an existing conversation of the shared app or create one."""

        share, release = self.get_release_by_share_token(share_token, password)

        if conversation_id:
            try:
                conversation = self.conversation_service.get_conversation(
                    conversation_id=conversation_id,
                    workspace_id=release.app.workspace_id,
                )

                if conversation.app_id != release.app_id:
                    raise BusinessException("会话不属于该应用", BizCode.INVALID_CONVERSATION)

                return conversation
            except ResourceNotFoundException:
                logger.warning(
                    "会话不存在，将创建新会话",
                    extra={"conversation_id": str(conversation_id)},
                )

        conversation = self.conversation_service.create_conversation(
            app_id=release.app_id,
            workspace_id=release.app.workspace_id,
            user_id=user_id,
            is_draft=False,  # the share view always runs the released version
            config_snapshot=release.config,
        )

        logger.info(
            "为分享链接创建新会话",
            extra={
                "conversation_id": str(conversation.id),
                "share_token": share_token,
                "release_id": str(release.id),
            },
        )

        return conversation

    async def create_or_get_conversation_async(
        self,
        share_token: str,
        conversation_id: uuid.UUID | None = None,
        user_id: str | None = None,
        password: str | None = None,
    ) -> Conversation:
        """Async twin of ``create_or_get_conversation``."""

        share, release = await self.get_release_by_share_token_async(share_token, password)

        if conversation_id:
            try:
                conversation = await self.conversation_service.create_or_get_conversation_async(
                    app_id=release.app_id,
                    workspace_id=release.app.workspace_id if release.app else None,
                    is_draft=False,
                    conversation_id=conversation_id,
                    user_id=user_id,
                )
                return conversation
            except ResourceNotFoundException:
                logger.warning(
                    "会话不存在，将创建新会话",
                    extra={"conversation_id": str(conversation_id)},
                )

        conversation = await self.conversation_service.create_conversation_async(
            app_id=release.app_id,
            workspace_id=release.app.workspace_id,
            user_id=user_id,
            is_draft=False,
            config_snapshot=release.config,
        )
        return conversation

    def get_conversation_messages(
        self,
        share_token: str,
        conversation_id: uuid.UUID,
        password: str | None = None,
    ) -> Conversation:
        """Fetch one conversation of the shared app (ownership-checked)."""

        share, release = self.get_release_by_share_token(share_token, password)

        conversation = self.conversation_service.get_conversation(
            conversation_id=conversation_id,
            workspace_id=release.app.workspace_id,
        )

        if conversation.app_id != release.app_id:
            raise BusinessException("会话不属于该应用", BizCode.INVALID_CONVERSATION)

        return conversation

    def list_conversations(
        self,
        share_token: str,
        user_id: str | None = None,
        password: str | None = None,
        page: int = 1,
        pagesize: int = 20,
    ) -> tuple[list[Conversation], int]:
        """List conversations of the shared app (released version only)."""

        share, release = self.get_release_by_share_token(share_token, password)

        conversations, total = self.conversation_service.list_conversations(
            app_id=release.app_id,
            workspace_id=release.app.workspace_id,
            user_id=user_id,
            is_draft=False,  # only released-version conversations are shown
            page=page,
            pagesize=pagesize,
        )

        return conversations, total


__all__ = ["SharedChatService"]

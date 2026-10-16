"""Conversation service (frozen port of the monolith ``ConversationService``
subset consumed by the message-interaction face and the chat kernel).

Two faces ride one class, matching the host shape:

- Sync (management / v1 / share handlers): the v1 API-key conversation/message
  reads and feedback list, the message version list/switch, the cascade
  soft-delete of one assistant message, and the share-face conversation
  create/get/list.
- Async (chat kernel): conversation create/get-or-create, the message write
  path and history assembly, and the memory-pair dispatch fired after a
  completed turn.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException, ResourceNotFoundException
from ..models.conversation_model import Conversation, Message
from ..models.message_feedback_model import MessageFeedback
from ..repositories.conversation_repository import ConversationRepository, MessageRepository
from ..repositories.end_user_repository import EndUserRepository
from ..utils.datetime_utils import to_timestamp_ms, utcnow
from .memory_config_service import MemoryConfigService
from .message_feedback_service import FeedbackService

logger = logging.getLogger(__name__)


class ConversationService:
    """Conversation service (v1 reads, message versions, soft delete, chat kernel)."""

    def __init__(self, db: Session):
        self.db = db
        self.conversation_repo = ConversationRepository(db)
        self.message_repo = MessageRepository(db)
        self.end_user_repo = EndUserRepository(db)

    _V1_MESSAGE_META_DATA_WHITELIST = {
        "usage",
        "citations",
        "audio_url",
        "audio_status",
        "files",
    }

    def _resolve_v1_internal_user_id(
        self,
        *,
        workspace_id: uuid.UUID,
        external_user_id: str,
    ) -> str | None:
        """Map the external user id to the internal end-user id; None when the
        external id resolves to no end user."""

        end_user = self.end_user_repo.get_end_user_by_other_id(
            workspace_id=workspace_id,
            other_id=external_user_id,
        )
        if not end_user:
            return None
        return str(end_user.id)

    @staticmethod
    def _normalize_required_external_user_id(user_id: str) -> str:
        """Strip the required external user id; blank input is invalid."""

        normalized = user_id.strip()
        if not normalized:
            raise BusinessException("user_id 不能为空或全为空白", BizCode.INVALID_PARAMETER)
        return normalized

    @classmethod
    def _sanitize_v1_message_meta_data(cls, meta_data: dict | None) -> dict:
        """Keep only whitelisted fields when exposing message metadata."""

        if not isinstance(meta_data, dict):
            return {}
        return {
            key: value
            for key, value in meta_data.items()
            if key in cls._V1_MESSAGE_META_DATA_WHITELIST
        }

    def list_v1_conversations(
        self,
        *,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        external_user_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """List conversations for one external end-user of the v1 app face."""

        if not external_user_id:
            raise BusinessException("user_id 不能为空", BizCode.INVALID_PARAMETER)
        if page < 1:
            raise BusinessException("page 必须大于等于 1", BizCode.INVALID_PARAMETER)
        if page_size < 1:
            raise BusinessException("page_size 必须大于等于 1", BizCode.INVALID_PARAMETER)
        if page_size > 100:
            raise BusinessException("page_size 超过最大限制", BizCode.INVALID_PARAMETER)

        internal_user_id = self._resolve_v1_internal_user_id(
            workspace_id=workspace_id,
            external_user_id=external_user_id,
        )
        if internal_user_id is None:
            return {
                "items": [],
                "page": page,
                "page_size": page_size,
                "total": 0,
                "hasnext": False,
            }

        try:
            conversations, total = self.conversation_repo.list_v1_conversations(
                app_id=app_id,
                workspace_id=workspace_id,
                internal_user_id=internal_user_id,
                page=page,
                page_size=page_size,
            )
        except Exception as e:
            logger.exception(
                "Failed to list v1 conversations",
                extra={
                    "app_id": str(app_id),
                    "workspace_id": str(workspace_id),
                    "external_user_id": external_user_id,
                },
            )
            raise BusinessException("查询会话失败", BizCode.DB_ERROR, cause=e) from e

        items = [
            {
                "conversation_id": conversation.id,
                "title": conversation.title,
                "summary": conversation.summary,
                "message_count": conversation.message_count,
                "created_at": conversation.created_at,
                "updated_at": conversation.updated_at,
            }
            for conversation in conversations
        ]

        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "hasnext": (page * page_size) < total,
        }

    def list_v1_conversation_messages(
        self,
        *,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        external_user_id: str,
        conversation_id: uuid.UUID,
        limit: int = 50,
    ) -> dict:
        """List history messages of one v1 conversation."""

        if not external_user_id:
            raise BusinessException("user_id 不能为空", BizCode.INVALID_PARAMETER)
        if limit < 1:
            raise BusinessException("limit 必须大于等于 1", BizCode.INVALID_PARAMETER)
        if limit > 200:
            raise BusinessException("limit 超过最大限制", BizCode.INVALID_PARAMETER)

        internal_user_id = self._resolve_v1_internal_user_id(
            workspace_id=workspace_id,
            external_user_id=external_user_id,
        )

        try:
            conversation = self.conversation_repo.get_conversation_by_conversation_id(
                conversation_id
            )
        except ResourceNotFoundException as e:
            raise BusinessException("会话不存在", BizCode.NOT_FOUND, cause=e) from e
        except Exception as e:
            logger.exception(
                "Failed to fetch v1 conversation",
                extra={"conversation_id": str(conversation_id)},
            )
            raise BusinessException("查询会话失败", BizCode.DB_ERROR, cause=e) from e

        if internal_user_id is None:
            raise BusinessException("无权访问该会话", BizCode.FORBIDDEN)

        if (
            conversation.app_id != app_id
            or conversation.workspace_id != workspace_id
            or conversation.user_id != internal_user_id
            or conversation.is_active is not True
            or conversation.is_draft is not False
        ):
            raise BusinessException("会话不存在", BizCode.NOT_FOUND)

        try:
            messages = self.message_repo.get_message_by_conversation_id(
                conversation_id,
                limit=limit,
                current_only=True,
            )
        except Exception as e:
            logger.exception(
                "Failed to fetch v1 conversation messages",
                extra={"conversation_id": str(conversation_id)},
            )
            raise BusinessException("查询会话失败", BizCode.DB_ERROR, cause=e) from e

        items = []
        for message in messages:
            if message.role == "system":
                continue
            items.append(
                {
                    "message_id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "status": message.status,
                    "meta_data": self._sanitize_v1_message_meta_data(message.meta_data),
                    "created_at": message.created_at,
                    "version": message.version or 1,
                    "is_current": False if message.is_current is False else True,
                    "parent_message_id": message.parent_message_id,
                }
            )

        return {
            "conversation_id": conversation.id,
            "items": items,
            "limit": limit,
        }

    def list_v1_conversation_feedback(
        self,
        *,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        external_user_id: str,
        conversation_id: uuid.UUID,
        limit: int = 50,
    ) -> dict:
        """Feedback states of one v1 conversation for the current end-user.

        Controller-exact semantics (the host v1 route executes its controller
        inline): blank external id rejected with the strip-aware message, the
        conversation is loaded by raw id, the not-owned branch answers 403, and
        only rows with ``is_current`` true participate.
        """

        external_user_id = self._normalize_required_external_user_id(external_user_id)

        internal_user_id = self._resolve_v1_internal_user_id(
            workspace_id=workspace_id,
            external_user_id=external_user_id,
        )
        if internal_user_id is None:
            raise BusinessException("无权访问该会话", BizCode.FORBIDDEN)

        conversation = self.db.get(Conversation, conversation_id)
        if not conversation:
            raise BusinessException("会话不存在", BizCode.NOT_FOUND)

        if (
            conversation.app_id != app_id
            or conversation.workspace_id != workspace_id
            or conversation.user_id != internal_user_id
            or conversation.is_active is not True
            or conversation.is_draft is not False
        ):
            raise BusinessException("无权访问该会话", BizCode.FORBIDDEN)

        message_result = self.db.execute(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.is_deleted.is_not(True),
                Message.is_current.is_(True),
            )
            .order_by(Message.created_at)
            .limit(limit)
        )
        visible = [
            message
            for message in message_result.scalars().all()
            if message.role != "system"
        ]

        feedback_type_map = {}
        feedback_content_map = {}
        if visible:
            feedback_result = self.db.execute(
                select(
                    MessageFeedback.message_id,
                    MessageFeedback.feedback_type,
                    MessageFeedback.feedback_content,
                ).where(
                    MessageFeedback.message_id.in_([message.id for message in visible]),
                    MessageFeedback.user_id == internal_user_id,
                )
            )
            rows = feedback_result.all()
            feedback_type_map = {row[0]: row[1] for row in rows}
            feedback_content_map = {row[0]: row[2] for row in rows}

        return {
            "conversation_id": conversation.id,
            "items": [
                {
                    "message_id": message.id,
                    "role": message.role,
                    "feedback_type": feedback_type_map.get(message.id),
                    "feedback_content": feedback_content_map.get(message.id),
                    "created_at": message.created_at,
                }
                for message in visible
            ],
            "limit": limit,
        }

    def submit_v1_message_feedback(
        self,
        *,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        external_user_id: str,
        message_id: uuid.UUID,
        feedback_type: str,
        feedback_content: str | None = None,
    ) -> dict:
        """Submit like/dislike feedback for one v1 message (controller-exact).

        After the ownership guards the upsert itself is identical to the
        management face, so it delegates to ``FeedbackService``.
        """

        external_user_id = self._normalize_required_external_user_id(external_user_id)

        internal_user_id = self._resolve_v1_internal_user_id(
            workspace_id=workspace_id,
            external_user_id=external_user_id,
        )
        if internal_user_id is None:
            raise BusinessException("无权访问该会话", BizCode.FORBIDDEN)

        message = self.db.get(Message, message_id)
        if not message or message.is_deleted:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        conversation = self.db.get(Conversation, message.conversation_id)
        if not conversation:
            raise BusinessException("会话不存在", BizCode.NOT_FOUND)

        if (
            conversation.app_id != app_id
            or conversation.workspace_id != workspace_id
            or conversation.user_id != internal_user_id
            or conversation.is_active is not True
            or conversation.is_draft is not False
        ):
            raise BusinessException("无权访问该会话", BizCode.FORBIDDEN)

        return FeedbackService(self.db).submit_feedback(
            message_id=message_id,
            conversation_id=message.conversation_id,
            workspace_id=workspace_id,
            user_id=internal_user_id,
            feedback_type=feedback_type,
            feedback_content=feedback_content,
        )

    def get_v1_message_suggested_questions(
        self,
        *,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        message_id: uuid.UUID,
    ) -> list[str]:
        """Preset questions of one assistant message (meta_data.suggested_questions)."""

        message = self.db.get(Message, message_id)
        if not message or message.is_deleted:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        try:
            conversation = self.conversation_repo.get_conversation_by_conversation_id(
                message.conversation_id,
                workspace_id,
            )
        except ResourceNotFoundException as e:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND, cause=e) from e

        if (
            conversation.app_id != app_id
            or conversation.workspace_id != workspace_id
            or conversation.is_active is not True
            or conversation.is_draft is not False
        ):
            # Same NOT_FOUND as above so callers cannot probe conversation
            # existence through the error code.
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        if message.role != "assistant":
            raise BusinessException("仅支持 assistant 消息", BizCode.BAD_REQUEST)

        meta_data = message.meta_data
        if not isinstance(meta_data, dict):
            return []
        raw = meta_data.get("suggested_questions")
        if not isinstance(raw, list):
            return []
        return [str(q) for q in raw if q]

    def delete_message(
        self,
        message_id: uuid.UUID,
        workspace_id: uuid.UUID,
    ) -> None:
        """Soft-delete one message.

        An assistant reply drags down every version of its turn (regenerated
        siblings included). The version group is anchored on the parent user
        message — ``parent_message_id`` when back-filled, else the nearest
        earlier user message regardless of deletion state — and bounded by the
        next user message's timestamp, so legacy rows without
        ``parent_message_id`` are covered too.
        """

        message = self.db.get(Message, message_id)
        if not message:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        conv = self.db.get(Conversation, message.conversation_id)
        if conv.workspace_id != workspace_id:
            raise BusinessException("无权删除此消息", BizCode.PERMISSION_DENIED)

        message.is_deleted = True

        if message.role == "assistant":
            parent_user_msg = None
            if message.parent_message_id:
                parent_user_msg = self.db.get(Message, message.parent_message_id)
                # A parent_message_id pointing at a non-user row is dirty data:
                # fall back to the time-based lookup.
                if parent_user_msg and parent_user_msg.role != "user":
                    parent_user_msg = None
            if not parent_user_msg:
                # Nearest earlier user message, deleted ones included: a
                # deleted user row still anchors its own reply group.
                parent_user_msg = (
                    self.db.query(Message)
                    .filter(
                        Message.conversation_id == message.conversation_id,
                        Message.role == "user",
                        Message.created_at < message.created_at,
                    )
                    .order_by(Message.created_at.desc())
                    .first()
                )

            if parent_user_msg:
                # The next user message bounds the reply-group window (end of
                # conversation when absent). Deleted rows still mark the start
                # of the next group, so they are not filtered out either.
                next_user_msg = (
                    self.db.query(Message)
                    .filter(
                        Message.conversation_id == message.conversation_id,
                        Message.role == "user",
                        Message.created_at > parent_user_msg.created_at,
                    )
                    .order_by(Message.created_at.asc())
                    .first()
                )

                siblings_query = (
                    self.db.query(Message)
                    .filter(
                        Message.conversation_id == message.conversation_id,
                        Message.role == "assistant",
                        Message.created_at > parent_user_msg.created_at,
                        Message.is_deleted.is_not(True),
                    )
                )
                if next_user_msg:
                    siblings_query = siblings_query.filter(
                        Message.created_at < next_user_msg.created_at
                    )

                for sibling in siblings_query.all():
                    sibling.is_deleted = True

        self.db.commit()

        logger.info(
            "Message deleted",
            extra={
                "message_id": str(message_id),
                "workspace_id": str(workspace_id),
            },
        )

    def get_message_versions(
        self,
        message_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        """All regenerated versions of one message."""

        message = self.db.get(Message, message_id)
        if not message:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        if message.parent_message_id:
            versions = self.db.query(Message).filter(
                Message.parent_message_id == message.parent_message_id,
                Message.role == "assistant",
                Message.is_deleted.is_not(True),
            ).order_by(Message.version).all()
        else:
            # No parent link: the message is its own only version.
            versions = [message]

        return [
            {
                "message_id": str(v.id),
                "version": v.version,
                "is_current": v.is_current,
                "content": v.content,
                "created_at": to_timestamp_ms(v.created_at),
            }
            for v in versions
        ]

    def switch_message_version(
        self,
        message_id: uuid.UUID,
        version: int,
        workspace_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Switch the displayed version of one message."""

        current_msg = self.db.get(Message, message_id)
        if not current_msg:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        conv = self.db.get(Conversation, current_msg.conversation_id)
        if conv.workspace_id != workspace_id:
            raise BusinessException("无权操作此消息", BizCode.PERMISSION_DENIED)

        if current_msg.parent_message_id:
            target_msg = self.db.query(Message).filter(
                Message.parent_message_id == current_msg.parent_message_id,
                Message.role == "assistant",
                Message.version == version,
                Message.is_deleted.is_not(True),
            ).first()
        else:
            target_msg = current_msg if current_msg.version == version else None

        if not target_msg:
            raise BusinessException("版本不存在", BizCode.NOT_FOUND)

        if current_msg.parent_message_id:
            # Deleted siblings included: their is_current flag is still reset
            # so a later restore cannot resurrect a stale current version.
            all_versions = self.db.query(Message).filter(
                Message.parent_message_id == current_msg.parent_message_id,
                Message.role == "assistant",
            ).all()
            for v in all_versions:
                v.is_current = False

            target_msg.is_current = True
            self.db.commit()

        return {
            "message_id": str(target_msg.id),
            "version": target_msg.version,
            "content": target_msg.content,
        }

    # ------------------------------------------------------------------
    # Share face (sync session)
    # ------------------------------------------------------------------

    def create_conversation(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: str | None = None,
        title: str | None = None,
        is_draft: bool = False,
        config_snapshot: dict | None = None,
    ) -> Conversation:
        """Create a conversation on the sync (share/management) session."""

        try:
            conversation = self.conversation_repo.create_conversation(
                app_id=app_id,
                workspace_id=workspace_id,
                user_id=user_id,
                title=title,
                is_draft=is_draft,
                config_snapshot=config_snapshot,
            )
            self.db.commit()
            self.db.refresh(conversation)

            logger.info(
                "Create Conversation Success",
                extra={
                    "conversation_id": str(conversation.id),
                    "app_id": str(app_id),
                    "workspace_id": str(workspace_id),
                    "is_draft": is_draft,
                },
            )
        except Exception as e:
            logger.error("Create Conversation Failed - %s", e)
            self.db.rollback()
            raise BusinessException(
                "Error create Convsersation", code=BizCode.DB_ERROR, cause=e
            ) from e

        return conversation

    def get_conversation(
        self,
        conversation_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
    ) -> Conversation:
        """Fetch one conversation (optionally workspace-scoped); not-found raises."""

        return self.conversation_repo.get_conversation_by_conversation_id(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )

    def list_conversations(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: str | None = None,
        is_draft: bool | None = None,
        page: int = 1,
        pagesize: int = 20,
    ) -> tuple[list[Conversation], int]:
        """List conversations of one app (+workspace) with optional filters."""

        return self.conversation_repo.list_conversations(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            is_draft=is_draft,
            page=page,
            pagesize=pagesize,
        )

    def get_conversation_with_messages(
        self,
        conversation_id: uuid.UUID,
    ) -> list[Message | list[Message]]:
        """Messages of one conversation, multi-version aware.

        Current-version messages come back in time order; only the last user
        message's assistant replies keep their version shape — a single version
        stays flat, several versions collapse into one nested list at the tail.
        """

        current_messages = self.message_repo.get_message_by_conversation_id(
            conversation_id,
            current_only=True,
        )

        last_user_msg = None
        for msg in reversed(current_messages):
            if msg.role == "user":
                last_user_msg = msg
                break

        last_user_all_versions: list[Message] = []
        if last_user_msg:
            last_user_all_versions = list(
                self.db.scalars(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.role == "assistant",
                        Message.parent_message_id == last_user_msg.id,
                        Message.is_deleted.is_not(True),
                    )
                    .order_by(Message.version)
                ).all()
            )

        result_messages: list[Message | list[Message]] = []
        for msg in current_messages:
            if msg.role == "user":
                result_messages.append(msg)
            elif msg.role == "assistant":
                # Replies to the last user message are appended as the version
                # group below instead of in their current-version position.
                if last_user_msg and msg.parent_message_id == last_user_msg.id:
                    continue
                result_messages.append(msg)

        if last_user_all_versions:
            if len(last_user_all_versions) == 1:
                result_messages.append(last_user_all_versions[0])
            else:
                result_messages.append(last_user_all_versions)

        return result_messages

    # ------------------------------------------------------------------
    # Chat kernel (async session)
    # ------------------------------------------------------------------

    async def create_conversation_async(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: str | None = None,
        title: str | None = None,
        is_draft: bool = False,
        config_snapshot: dict | None = None,
    ) -> Conversation:
        """Create a conversation on the async chat-kernel session."""

        try:
            conversation = self.conversation_repo.create_conversation(
                app_id=app_id,
                workspace_id=workspace_id,
                user_id=user_id,
                title=title,
                is_draft=is_draft,
                config_snapshot=config_snapshot,
            )
            if isinstance(self.db, AsyncSession):
                await self.db.flush()
            await self.db.commit()
            return conversation
        except Exception as e:
            logger.error("Create Conversation Failed - %s", e)
            await self.db.rollback()
            raise BusinessException(
                "Error create Convsersation", code=BizCode.DB_ERROR, cause=e
            ) from e

    async def add_message_async(
        self,
        conversation_id: uuid.UUID,
        role: str,
        content: str,
        *,
        meta_data: dict | None = None,
        message_id: uuid.UUID | None = None,
        status: str = "completed",
        parent_message_id: uuid.UUID | None = None,
    ) -> Message | None:
        """Append one message and bump the conversation counters.

        Returns None while ``_suppress_message_save`` is set (regenerate
        replays must not persist a second copy of the same turn).
        """

        if getattr(self, "_suppress_message_save", False):
            return None
        try:
            if isinstance(self.db, AsyncSession):
                conversation = (
                    await self.conversation_repo.get_conversation_by_conversation_id_async(
                        conversation_id
                    )
                )
            else:
                conversation = self.conversation_repo.get_conversation_by_conversation_id(
                    conversation_id
                )

            message = Message(
                id=message_id if message_id else uuid.uuid4(),
                conversation_id=conversation_id,
                role=role,
                content=content,
                meta_data=meta_data,
                status=status,
                parent_message_id=parent_message_id,
            )

            self.message_repo.add_message(message)
            conversation.message_count += 1

            # The opening user turn names the conversation (truncated to 50).
            if conversation.message_count <= 2 and role == "user":
                conversation.title = content[:50] + ("..." if len(content) > 50 else "")

            if isinstance(self.db, AsyncSession):
                await self.db.commit()
                await self.db.refresh(message)
            else:
                self.db.commit()
                self.db.refresh(message)

            return message
        except Exception as e:
            logger.error(
                "Message added error, db roll back - %s",
                e,
                extra={
                    "conversation_id": str(conversation_id),
                    "role": role,
                    "content_length": len(content),
                },
            )
            if isinstance(self.db, AsyncSession):
                await self.db.rollback()
            else:
                self.db.rollback()
            raise BusinessException(
                f"Error adding message, conversation_id={conversation_id}",
                code=BizCode.DB_ERROR,
                cause=e,
            ) from e

    async def dispatch_memory_pair(
        self,
        conversation_id: uuid.UUID,
        user_message: dict[str, Any],
        assistant_message: dict[str, Any],
        storage_type: str = "neo4j",
        user_rag_memory_id: str = "",
    ) -> None:
        """Resolve the memory config and fire-and-forget the paired ingest.

        The pair is submitted as one batch, so the memory pipeline assigns
        consecutive sequence numbers with the user message strictly before
        the assistant one. Message dicts accept: ``id``, ``content``,
        ``meta_data`` and ``should_memorize`` (default True). Best-effort:
        failures are logged and never fail the chat turn.
        """

        conversation = await self.conversation_repo.get_conversation_by_conversation_id_async(
            conversation_id
        )
        if not conversation:
            return

        from ..memory.memory_service import MemoryService

        try:
            config_id = await MemoryConfigService().get_workspace_active_config_id_async(
                conversation.workspace_id
            )
            now = utcnow()
            messages = [
                SimpleNamespace(
                    id=m["id"],
                    conversation_id=conversation_id,
                    role=role,
                    content=m["content"],
                    meta_data=m.get("meta_data"),
                    created_at=now,
                    should_memorize=m.get("should_memorize", True),
                )
                for role, m in (("user", user_message), ("assistant", assistant_message))
            ]
            asyncio.create_task(
                MemoryService.ingest_agent_messages(
                    conversation_id=str(conversation.id),
                    messages=messages,
                    app_id=str(conversation.app_id),
                    config_id=str(config_id),
                    workspace_id=str(conversation.workspace_id),
                    end_user_id=str(conversation.user_id) if conversation.user_id else "",
                    storage_type=storage_type or "neo4j",
                    user_rag_memory_id=user_rag_memory_id,
                )
            )
        except Exception as exc:
            logger.warning(
                "dispatch_memory_pair failed: conv=%s, err=%s",
                conversation.id,
                exc,
                exc_info=True,
            )

    async def get_conversation_history(
        self,
        conversation_id: uuid.UUID,
        max_history: int | None = None,
        current_provider: str | None = None,
    ) -> list[dict]:
        """History formatted for model prompts.

        Messages carrying ``history_files`` only join when their stored
        provider matches the current one; mismatched rows are skipped so a
        provider switch does not replay foreign file blocks.
        """

        messages, _ = await self.message_repo.get_message_by_conversation_id_async(
            conversation_id,
            limit=max_history,
        )

        history = []
        for msg in messages:
            history_files = msg.meta_data.get("history_files", {}) if msg.meta_data else {}

            has_files = bool(history_files and current_provider)
            if has_files:
                stored_provider = history_files.get("provider")

                if stored_provider != current_provider:
                    continue

                content = [{"type": "text", "text": msg.content}]
                content.extend(history_files.get("content", []))
            else:
                content = msg.content

            history.append({
                "role": msg.role,
                "content": content,
            })

        return history

    async def create_or_get_conversation_async(
        self,
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        is_draft: bool = False,
        conversation_id: uuid.UUID | None = None,
        user_id: str | None = None,
    ) -> Conversation:
        """Return the existing conversation or create one.

        Public-face guards treat every ownership mismatch as "conversation
        does not exist" so an id belonging to another app / end user / draft
        conversation cannot be probed. An id that exists outside the given
        workspace is never recreated.
        """

        if conversation_id:
            try:
                conversation = (
                    await self.conversation_repo.get_conversation_by_conversation_id_async(
                        conversation_id=conversation_id,
                        workspace_id=workspace_id,
                    )
                )
                if conversation.app_id != app_id:
                    raise BusinessException("会话不存在", BizCode.NOT_FOUND)
                if user_id is not None and str(conversation.user_id) != str(user_id):
                    logger.warning(
                        "Conversation does not belong to the requesting end user.",
                        extra={
                            "conversation_id": str(conversation_id),
                            "app_id": str(app_id),
                        },
                    )
                    raise BusinessException("会话不存在", BizCode.NOT_FOUND)
                if conversation.is_draft is not False or conversation.is_active is not True:
                    raise BusinessException("会话不存在", BizCode.NOT_FOUND)
                return conversation
            except ResourceNotFoundException:
                # The id is missing from this workspace; it may still exist in
                # another one, in which case it must never be recreated here.
                try:
                    await self.conversation_repo.get_conversation_by_conversation_id_async(
                        conversation_id=conversation_id,
                    )
                except ResourceNotFoundException:
                    logger.warning(
                        "Conversation not found. A new conversation will be created.",
                        extra={"conversation_id": str(conversation_id)},
                    )
                else:
                    logger.warning(
                        "Conversation belongs to a different workspace.",
                        extra={
                            "conversation_id": str(conversation_id),
                            "workspace_id": str(workspace_id),
                        },
                    )
                    raise BusinessException("会话不存在", BizCode.NOT_FOUND)

        return await self.create_conversation_async(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            is_draft=is_draft,
        )


__all__ = ["ConversationService"]

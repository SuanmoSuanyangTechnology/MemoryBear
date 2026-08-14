"""会话服务"""
import asyncio
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Annotated
from typing import Optional, List, Tuple, Dict, Any

import json_repair
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.exceptions import ResourceNotFoundException
from app.core.logging_config import get_business_logger
from app.core.models import RedBearLLM, RedBearModelConfig
from app.core.utils.datetime_utils import to_timestamp_ms, utcnow, utcnow_naive
from app.db import get_db
from app.models import Conversation, Message, MessageFeedback, User, ModelType
from app.models.conversation_model import ConversationDetail
from app.models.prompt_optimizer_model import RoleType
from app.repositories.conversation_repository import ConversationRepository, MessageRepository
from app.repositories.end_user_repository import EndUserRepository
from app.repositories.tool_repository import ToolRepository
from app.schemas.conversation_schema import ConversationOut
from app.services import workspace_service
from app.services.memory_config_service import MemoryConfigService
from app.services.model_service import ModelConfigService, ModelApiKeyService
from app.services.prompt import prompt_manager

logger = get_business_logger()


class ConversationService:
    """
    Service layer for managing conversations and messages.
    Provides methods to create, retrieve, list, and manipulate conversations and messages.
    Delegates database operations to repositories.
    """

    def __init__(self, db: Session | AsyncSession):
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

    def create_conversation(
            self,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            user_id: Optional[str] = None,
            title: Optional[str] = None,
            is_draft: bool = False,
            config_snapshot: Optional[dict] = None
    ) -> Conversation:
        """
        Create a new conversation in the system.

        Args:
            app_id (uuid.UUID): The application ID the conversation belongs to.
            workspace_id (uuid.UUID): Workspace ID for context.
            user_id (Optional[str]): Optional user ID for the conversation owner.
            title (Optional[str]): Conversation title. Defaults to 'New Conversation' if not provided.
            is_draft (bool): Whether the conversation is a draft.
            config_snapshot (Optional[dict]): Optional configuration snapshot.

        Returns:
            Conversation: Newly created Conversation instance.
        """
        try:
            conversation = self.conversation_repo.create_conversation(
                app_id=app_id,
                workspace_id=workspace_id,
                user_id=user_id,
                title=title or "New Conversation",
                is_draft=is_draft,
                config_snapshot=config_snapshot
            )
            self.db.commit()
            self.db.refresh(conversation)

            logger.info(
                "Create Conversation Success",
                extra={
                    "conversation_id": str(conversation.id),
                    "app_id": str(app_id),
                    "workspace_id": str(workspace_id),
                    "is_draft": is_draft
                }
            )
        except Exception as e:
            logger.error(
                f"Create Conversation Failed - {str(e)}"
            )
            self.db.rollback()
            raise BusinessException(f"Error create Convsersation", code=BizCode.DB_ERROR)

        return conversation

    async def create_conversation_async(
            self,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            user_id: Optional[str] = None,
            title: Optional[str] = None,
            is_draft: bool = False,
            config_snapshot: Optional[dict] = None
    ) -> Conversation:
        try:
            conversation = self.conversation_repo.create_conversation(
                app_id=app_id,
                workspace_id=workspace_id,
                user_id=user_id,
                title=title or "New Conversation",
                is_draft=is_draft,
                config_snapshot=config_snapshot
            )
            if isinstance(self.db, AsyncSession):
                await self.db.flush()
            await self.db.commit()
            return conversation
        except Exception as e:
            logger.error(f"Create Conversation Failed - {str(e)}")
            await self.db.rollback()
            raise BusinessException("Error create Convsersation", code=BizCode.DB_ERROR)

    def get_conversation(
            self,
            conversation_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None
    ) -> Conversation:
        """
        Retrieve a conversation by its ID.

        Args:
            conversation_id (uuid.UUID): The conversation UUID.
            workspace_id (Optional[uuid.UUID]): Optional workspace UUID to restrict the query.

        Raises:
            ResourceNotFoundException: If the conversation does not exist.

        Returns:
            Conversation: The requested Conversation instance.
        """
        conversation = self.conversation_repo.get_conversation_by_conversation_id(
            conversation_id=conversation_id,
            workspace_id=workspace_id
        )

        return conversation

    async def get_conversation_async(
            self,
            conversation_id: uuid.UUID,
            workspace_id: Optional[uuid.UUID] = None,
    ) -> Conversation:
        """
        Async version of get_conversation.

        Args:
            conversation_id (uuid.UUID): The conversation UUID.
            workspace_id (Optional[uuid.UUID]): Optional workspace UUID to restrict the query.

        Raises:
            ResourceNotFoundException: If the conversation does not exist.

        Returns:
            Conversation: The requested Conversation instance.
        """
        return await self.conversation_repo.get_conversation_by_conversation_id_async(
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )

    async def get_user_conversations_async(
            self,
            user_id: uuid.UUID,
            page: int = 1,
            page_size: int = 20
    ) -> tuple[list[Conversation], int]:
        """异步版本：分页查询用户的会话列表。"""
        conversations, total = await self.conversation_repo.get_conversation_by_user_id_async(
            user_id,
            page=page,
            page_size=page_size
        )
        return conversations, total

    def list_conversations(
            self,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            user_id: Optional[str] = None,
            is_draft: Optional[bool] = None,
            page: int = 1,
            pagesize: int = 20
    ) -> Tuple[List[Conversation], int]:
        """
        List conversations with optional filters and pagination.

        Args:
            app_id (uuid.UUID): Application ID filter.
            workspace_id (uuid.UUID): Workspace ID filter.
            user_id (Optional[str]): Optional user ID filter.
            is_draft (Optional[bool]): Optional draft status filter.
            page (int): Page number, 1-based.
            pagesize (int): Number of items per page.

        Returns:
            Tuple[List[Conversation], int]: A list of Conversation instances and the total count.
        """
        conversations, total = self.conversation_repo.list_conversations(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            is_draft=is_draft,
            page=page,
            pagesize=pagesize
        )

        return conversations, total

    def add_message(
            self,
            conversation_id: uuid.UUID,
            role: str,
            content: str,
            meta_data: Optional[dict] = None,
            message_id: Optional[uuid.UUID] = None,
            status: str = "completed",
            parent_message_id: Optional[uuid.UUID] = None,
    ) -> Message:
        """
        Add a message to a conversation using UnitOfWork.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            role (str): Role of the message sender ('user' or 'assistant').
            content (str): Message content.
            meta_data (Optional[dict]): Optional metadata.
            message_id (Optional[uuid.UUID]): Optional custom message UUID.
            status (str): Message status, default "completed".

        Returns:
            Message: Newly created Message instance.
        """
        # 重新生成场景：由 WorkflowService.regenerate 统一保存版本化消息，
        # 跳过 run/run_stream 内部对 user/assistant/开场白/失败消息的重复保存。
        if getattr(self, "_suppress_message_save", False):
            return None
        try:
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

            if conversation.message_count <= 2 and role == "user":
                conversation.title = (
                        content[:50] + ("..." if len(content) > 50 else "")
                )

            self.db.commit()
            self.db.refresh(message)

            # 由业务层成对调用点显式调 dispatch_memory_pair

            logger.info(
                "Message added successfully",
                extra={
                    "conversation_id": str(conversation_id),
                    "message_id": str(message.id),
                    "role": role,
                    "content_length": len(content),
                },
            )

            return message
        except Exception as e:
            logger.error(
                f"Message added error, db roll back - {str(e)}",
                extra={
                    "conversation_id": str(conversation_id),
                    "role": role,
                    "content_length": len(content),
                },
            )
            self.db.rollback()
            raise BusinessException(
                f"Error adding message, conversation_id={conversation_id}",
                code=BizCode.DB_ERROR
            )

    async def add_message_async(
            self,
            conversation_id: uuid.UUID,
            role: str,
            content: str,
            meta_data: Optional[dict] = None,
            message_id: Optional[uuid.UUID] = None,
            status: str = "completed",
            parent_message_id: Optional[uuid.UUID] = None,
    ) -> Message:
        """AsyncSession 版本的消息写入。"""
        if getattr(self, "_suppress_message_save", False):
            return None
        try:
            if isinstance(self.db, AsyncSession):
                conversation = await self.conversation_repo.get_conversation_by_conversation_id_async(
                    conversation_id
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

            if conversation.message_count <= 2 and role == "user":
                conversation.title = content[:50] + ("..." if len(content) > 50 else "")

            if isinstance(self.db, AsyncSession):
                await self.db.commit()
                await self.db.refresh(message)
            else:
                self.db.commit()
                self.db.refresh(message)

            # 由业务层成对调用点显式调 dispatch_memory_pair

            return message
        except Exception as e:
            logger.error(
                f"Message added error, db roll back - {str(e)}",
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
                code=BizCode.DB_ERROR
            )

    def update_message(
            self,
            message_id: uuid.UUID,
            content: str | None = None,
            meta_data: dict | None = None,
    ) -> Message | None:
        """Update an existing message's content and/or meta_data by ID.

        Used for HITL resume: the same assistant message that carried
        waiting_human=True is updated in-place with the final output
        content and cleared waiting_human flag, instead of creating a
        new message.

        Args:
            message_id: The message ID to update.
            content: New content (None = keep existing).
            meta_data: New meta_data dict (None = keep existing).

        Returns:
            The updated Message, or None if not found.
        """
        message = self.db.get(Message, message_id)
        if not message:
            logger.warning(f"update_message: message_id={message_id} not found")
            return None
        if content is not None:
            message.content = content
        if meta_data is not None:
            message.meta_data = meta_data
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(message, "meta_data")
        self.db.flush()
        return message

    def resolve_intervention_message(
            self,
            conversation_id: uuid.UUID,
            execution_id: str,
            content: str,
            extra_meta: dict | None = None,
    ) -> Message | None:
        """Find the waiting_human assistant message and update it with
        the final workflow output, clearing the waiting_human flag.

        Instead of creating a NEW assistant message on resume, this
        method finds the existing waiting_human message (created when
        the workflow paused) and updates it in-place: content becomes
        the final output, waiting_human becomes False.

        Args:
            conversation_id: The conversation to search.
            execution_id: Execution ID in meta_data for precise targeting.
            content: The final workflow output to set as message content.
            extra_meta: Additional meta_data keys to merge (usage, citations).

        Returns:
            The updated Message, or None if no matching message found.
        """
        from sqlalchemy import cast
        from sqlalchemy.dialects.postgresql import JSONB

        msg = (
            self.db.query(Message)
            .filter(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
                Message.is_deleted == False,
                cast(Message.meta_data, JSONB).contains(
                    {"waiting_human": True, "execution_id": execution_id}
                ),
            )
            .first()
        )
        if not msg:
            logger.warning(
                f"resolve_intervention_message: no waiting_human message found "
                f"for conversation_id={conversation_id}, execution_id={execution_id}"
            )
            return None

        msg.content = content
        new_meta = dict(msg.meta_data or {})
        new_meta["waiting_human"] = False
        if extra_meta:
            new_meta.update(extra_meta)
        msg.meta_data = new_meta

        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(msg, "meta_data")
        self.db.flush()

        logger.debug(
            f"resolve_intervention_message: updated message_id={msg.id}, "
            f"conversation_id={conversation_id}, content_length={len(content)}"
        )
        return msg

    async def dispatch_memory_pair(
        self,
        conversation_id: uuid.UUID,
        user_message: dict[str, Any],
        assistant_message: dict[str, Any],
    ) -> None:
        """查会话 + 解析记忆配置 + 组装成对消息 + fire-and-forget 批量派发。

        一次 write_batch（一次 pg_advisory_xact_lock + 一次事务）分配连续 seq，
        批内严格 user < assistant。

        Args:
            conversation_id: 会话 ID。
            user_message / assistant_message: 消息字段字典，支持键：
                - id: uuid.UUID（消息 ID）
                - content: str
                - meta_data: dict | None
                - should_memorize: bool（默认 True）
        """
        conversation = await self.conversation_repo.get_conversation_by_conversation_id_async(conversation_id)
        if not conversation:
            return

        from app.db import get_async_db_context
        from app.core.memory.memory_service import MemoryService

        try:
            async with get_async_db_context() as db:
                config_id = await MemoryConfigService(db).get_workspace_active_config_id_async(
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
                )
            )
        except Exception as exc:
            logger.warning(
                f"[ConversationService] dispatch_memory_pair 执行失败: "
                f"conv={conversation.id}, err={exc}",
                exc_info=True,
            )

    async def dispatch_memory_batch(
        self,
        messages: List[Any],
        conversation: Conversation,
    ) -> None:
        """批量派发同一回合的多条消息到记忆系统（async 线性实现）。

        与 dispatch_memory_sync 的区别：一次 write_batch（一次 pg_advisory_xact_lock
        + 一次事务）分配连续 seq，一次滑动窗口派发。批内 seq 严格 user < assistant。

        本方法**本身不 fire-and-forget**。调用方决定：
            - fire-and-forget：`asyncio.create_task(svc.dispatch_memory_batch(...))`
            - 阻塞等待：`await svc.dispatch_memory_batch(...)`
        主 chat 流程默认走 fire-and-forget，避免拖累流式响应。

        Args:
            messages: 消息列表，元素需带 .id / .conversation_id / .role /
                .content / .created_at / .meta_data / .should_memorize 属性。
                所有消息应属于同一对话，seq 分配顺序 = 列表顺序。
            conversation: 所属 Conversation 实例（提供 workspace_id / app_id / user_id）。
        """
        # 数据形状规范化（None → {}, 缺失字段默认等）由下游 dispatcher.ingest_agent_messages
        # 统一处理；空/异常输入由下游 if not messages / if not written 双重拦截；
        # 本方法只做纯派发（薄派发层），不重复防御。
        from app.db import get_async_db_context
        from app.core.memory.memory_service import MemoryService

        try:
            async with get_async_db_context() as db:
                config_id = await MemoryConfigService(db).get_workspace_active_config_id_async(
                    conversation.workspace_id
                )
            await MemoryService.ingest_agent_messages(
                conversation_id=str(messages[0].conversation_id) if messages else "",
                messages=messages,
                app_id=str(conversation.app_id),
                config_id=str(config_id),
                workspace_id=str(conversation.workspace_id),
                end_user_id=str(conversation.user_id) if conversation.user_id else "",
            )
        except Exception as exc:
            logger.warning(
                f"[ConversationService] dispatch_memory_batch 执行失败: "
                f"conv={conversation.id}, batch={len(messages)}, err={exc}",
                exc_info=True,
            )

    def get_messages(
            self,
            conversation_id: uuid.UUID,
            limit: Optional[int] = None,
            current_only: bool = True
    ) -> List[Message]:
        """
        Retrieve messages for a conversation.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            limit (Optional[int]): Optional maximum number of messages.
            current_only (bool): If True, only return current version messages.
                                If False, return all versions.

        Returns:
            List[Message]: List of messages ordered by creation time.
        """
        return self.message_repo.get_message_by_conversation_id(
            conversation_id,
            limit,
            current_only=current_only
        )

    async def get_messages_async(
            self,
            conversation_id: uuid.UUID,
            limit: Optional[int] = None,
            current_only: bool = True
    ) -> List[Message]:
        return await self.message_repo.get_message_by_conversation_id_async(
            conversation_id,
            limit,
            current_only=current_only
        )

    def _resolve_v1_internal_user_id(
            self,
            *,
            workspace_id: uuid.UUID,
            external_user_id: str,
    ) -> str | None:
        """将外部 user_id 解析为内部终端用户 ID，不存在时返回 None。"""
        end_user = self.end_user_repo.get_end_user_by_other_id(
            workspace_id=workspace_id,
            other_id=external_user_id,
        )
        if not end_user:
            return None
        return str(end_user.id)

    @classmethod
    def _sanitize_v1_message_meta_data(cls, meta_data: Optional[dict]) -> dict:
        """对外返回消息元数据时仅保留白名单字段。"""
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
        """获取 v1 应用对外服务的会话列表。"""
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
                "查询 v1 会话列表失败",
                extra={
                    "app_id": str(app_id),
                    "workspace_id": str(workspace_id),
                    "external_user_id": external_user_id,
                }
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
        """获取 v1 应用对外服务的会话历史消息。"""
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
            conversation = self.conversation_repo.get_conversation_by_conversation_id(conversation_id)
        except ResourceNotFoundException as e:
            raise BusinessException("会话不存在", BizCode.NOT_FOUND, cause=e) from e
        except Exception as e:
            logger.exception(
                "查询 v1 会话详情失败",
                extra={"conversation_id": str(conversation_id)}
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
                "查询 v1 会话消息失败",
                extra={"conversation_id": str(conversation_id)}
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

    async def list_v1_conversation_messages_async(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            external_user_id: str,
            conversation_id: uuid.UUID,
            limit: int = 50,
    ) -> dict:
        """获取 v1 应用对外服务的会话历史消息（异步版本）。"""
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
            conversation = await self.conversation_repo.get_conversation_by_conversation_id_async(conversation_id)
        except ResourceNotFoundException as e:
            raise BusinessException("会话不存在", BizCode.NOT_FOUND, cause=e) from e
        except Exception as e:
            logger.exception(
                "查询 v1 会话详情失败",
                extra={"conversation_id": str(conversation_id)}
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
            messages = await self.message_repo.get_message_by_conversation_id_async(
                conversation_id,
                limit=limit,
                current_only=True,
            )
        except Exception as e:
            logger.exception(
                "查询 v1 会话消息失败",
                extra={"conversation_id": str(conversation_id)}
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
        """获取 v1 应用对外服务的会话消息反馈（仅当前用户 feedback_type）。

        供前端渲染消息列表的反馈状态；不含 is_favorite（收藏功能不在范围）。
        校验/取消息逻辑与 list_v1_conversation_messages 完全一致。
        """
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
            conversation = self.conversation_repo.get_conversation_by_conversation_id(conversation_id)
        except ResourceNotFoundException as e:
            raise BusinessException("会话不存在", BizCode.NOT_FOUND, cause=e) from e
        except Exception as e:
            logger.exception(
                "查询 v1 会话详情失败",
                extra={"conversation_id": str(conversation_id)}
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
            raise BusinessException("无权访问该会话", BizCode.FORBIDDEN)

        try:
            messages = self.message_repo.get_message_by_conversation_id(
                conversation_id,
                limit=limit,
                current_only=True,
            )
        except Exception as e:
            logger.exception(
                "查询 v1 会话消息失败",
                extra={"conversation_id": str(conversation_id)}
            )
            raise BusinessException("查询会话失败", BizCode.DB_ERROR, cause=e) from e

        visible = [m for m in messages if m.role != "system"]

        # 单条 IN 查询取当前用户的 feedback_type / feedback_content（防 N+1；不查 is_favorite，无收藏泄漏）
        feedback_type_map: dict = {}
        feedback_content_map: dict = {}
        if visible:
            rows = (
                self.db.query(
                    MessageFeedback.message_id,
                    MessageFeedback.feedback_type,
                    MessageFeedback.feedback_content,
                )
                .filter(
                    MessageFeedback.message_id.in_([m.id for m in visible]),
                    MessageFeedback.user_id == internal_user_id,
                )
                .all()
            )
            feedback_type_map = {row[0]: row[1] for row in rows}
            feedback_content_map = {row[0]: row[2] for row in rows}

        items = [
            {
                "message_id": m.id,
                "role": m.role,
                "feedback_type": feedback_type_map.get(m.id),
                "feedback_content": feedback_content_map.get(m.id),
                "created_at": m.created_at,
            }
            for m in visible
        ]

        return {
            "conversation_id": conversation.id,
            "items": items,
            "limit": limit,
        }

    def get_v1_message_suggested_questions(
            self,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            message_id: uuid.UUID,
    ) -> list[str]:
        """获取指定 assistant 消息的预制问题（meta_data.suggested_questions）。"""
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
        ):
            # 为避免根据错误码推断会话/消息是否存在，这里与上方保持同样的 NOT_FOUND 返回
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

    def get_conversation_with_messages(
            self,
            conversation_id: uuid.UUID
    ) -> List[Message]:
        """获取会话及其所有消息（包含多版本），按 parent_message_id 分组

        Args:
            conversation_id: 会话ID

        Returns:
            List: 扁平化的消息列表
                  只有最后一条 user 消息的 assistant 回复可能展示多版本
        """
        # 获取 is_current=True 的消息
        current_messages = self.message_repo.get_message_by_conversation_id(
            conversation_id,
            current_only=True
        )
        
        # 找到最后一条 user 消息
        last_user_msg = None
        for msg in reversed(current_messages):
            if msg.role == "user":
                last_user_msg = msg
                break
        
        # 查询最后一条 user 消息的所有 assistant 版本
        last_user_all_versions = []
        if last_user_msg:
            from sqlalchemy import select
            all_versions = self.db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.role == "assistant",
                    Message.parent_message_id == last_user_msg.id,
                    Message.is_deleted.is_not(True),
                )
                .order_by(Message.version)
            ).all()
            last_user_all_versions = list(all_versions)
        
        # 构建结果
        result_messages = []
        for msg in current_messages:
            if msg.role == "user":
                result_messages.append(msg)
            elif msg.role == "assistant":
                # 检查是否是最后一条 user 的回复
                if last_user_msg and msg.parent_message_id == last_user_msg.id:
                    # 已经处理过了，跳过
                    continue
                else:
                    result_messages.append(msg)
        
        # 在最后添加最后一条 user 的所有 assistant 版本
        if last_user_all_versions:
            if len(last_user_all_versions) == 1:
                result_messages.append(last_user_all_versions[0])
            else:
                result_messages.append(last_user_all_versions)
        
        return result_messages

    async def get_conversation_history(
            self,
            conversation_id: uuid.UUID,
            max_history: Optional[int] = None,
            current_provider: Optional[str] = None,
            current_is_omni: Optional[bool] = None
    ) -> List[dict]:
        """
        Retrieve historical conversation messages formatted as dictionaries.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            max_history (Optional[int]): Maximum number of messages to retrieve.
            current_provider (Optional[str]): Current provider for file handling.
            current_is_omni (Optional[bool]): Current omni flag for file handling.

        Returns:
            List[dict]: List of message dictionaries with keys 'role' and 'content'.
        """
        messages = await self.message_repo.get_message_by_conversation_id_async(
            conversation_id,
            limit=max_history
        )

        history = []
        for msg in messages:
            history_files = msg.meta_data.get("history_files", {}) if msg.meta_data else {}

            has_files = bool(history_files and current_provider and current_is_omni is not None)
            if has_files:
                stored_provider = history_files.get("provider")
                stored_is_omni = history_files.get("is_omni")

                if stored_provider != current_provider or stored_is_omni != current_is_omni:
                    continue

                content = [{"type": "text", "text": msg.content}]
                content.extend(history_files.get("content", []))
            else:
                content = msg.content

            msg_dict = {
                "role": msg.role,
                "content": content
            }

            history.append(msg_dict)

        return history

    def save_conversation_messages(
            self,
            conversation_id: uuid.UUID,
            user_message: str,
            assistant_message: str,
            meta_data: Optional[dict] = None
    ):
        """
        Save a pair of user and assistant messages to the conversation.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            user_message (str): User's message content.
            assistant_message (str): Assistant's response content.
            meta_data (Optional[dict]): Optional metadata for the messages.
        """
        self.add_message(
            conversation_id=conversation_id,
            role="user",
            content=user_message
        )

        ai_message = self.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_message,
            meta_data=meta_data
        )

        logger.debug(
            "Saved conversation messages successfully",
            extra={
                "conversation_id": str(conversation_id),
                "user_message_length": len(user_message),
                "assistant_message_length": len(assistant_message)
            }
        )
        return ai_message.id

    def delete_conversation(
            self,
            conversation_id: uuid.UUID,
            workspace_id: uuid.UUID
    ):
        """
        Soft delete a conversation.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            workspace_id (uuid.UUID): Workspace UUID for validation.
        """
        try:
            self.conversation_repo.soft_delete_conversation_by_conversation_id(
                conversation_id,
                workspace_id
            )
            self.db.commit()

            logger.info(
                "Soft deleted conversation successfully",
                extra={
                    "conversation_id": str(conversation_id),
                    "workspace_id": str(workspace_id)
                }
            )
        except Exception as e:
            self.db.rollback()
            logger.error(
                f"Error deleting conversation, conversation_id={conversation_id} - {str(e)}",
            )
            raise BusinessException("Error deleting conversation", code=BizCode.DB_ERROR)

    def create_or_get_conversation(
            self,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            is_draft: bool = False,
            conversation_id: Optional[uuid.UUID] = None,
            user_id: Optional[str] = None,
    ) -> Conversation:
        """
        Retrieve an existing conversation by ID or create a new one.

        Args:
            app_id (uuid.UUID): Application ID.
            workspace_id (uuid.UUID): Workspace ID.
            is_draft (bool): Whether the conversation should be a draft.
            conversation_id (Optional[uuid.UUID]): Optional conversation ID to retrieve.
            user_id (Optional[str]): Optional user ID.

        Returns:
            Conversation: Existing or newly created conversation.
        """
        if conversation_id:
            try:
                conversation = self.get_conversation(
                    conversation_id=conversation_id,
                    workspace_id=workspace_id
                )

                # 验证会话是否属于该应用
                if conversation.app_id != app_id:
                    raise BusinessException(
                        "Conversation does not belong to this app",
                        BizCode.INVALID_CONVERSATION
                    )
                return conversation
            except ResourceNotFoundException:
                logger.warning(
                    "Conversation not found. A new conversation will be created.",
                    extra={"conversation_id": str(conversation_id)}
                )

        # 创建新会话（使用发布版本的配置）
        conversation = self.create_conversation(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            is_draft=is_draft
        )

        logger.info(
            "Created a new conversation for shared link usage",
            extra={
                "conversation_id": str(conversation_id),
            }
        )

        return conversation

    async def create_or_get_conversation_async(
            self,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            is_draft: bool = False,
            conversation_id: Optional[uuid.UUID] = None,
            user_id: Optional[str] = None,
    ) -> Conversation:
        if conversation_id:
            try:
                conversation = await self.conversation_repo.get_conversation_by_conversation_id_async(
                    conversation_id=conversation_id,
                    workspace_id=workspace_id
                )
                if conversation.app_id != app_id:
                    raise BusinessException(
                        "Conversation does not belong to this app",
                        BizCode.INVALID_CONVERSATION
                    )
                return conversation
            except ResourceNotFoundException:
                logger.warning(
                    "Conversation not found. A new conversation will be created.",
                    extra={"conversation_id": str(conversation_id)}
                )

        return await self.create_conversation_async(
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            is_draft=is_draft
        )

    async def delete_message(
            self,
            message_id: uuid.UUID,
            workspace_id: uuid.UUID,
    ) -> None:
        """删除单条消息（逻辑删除）

        若被删消息为 AI 回复，则一并删除同一轮的全部版本（含重新生成的兄弟版本），
        即每个版本的 is_deleted 均置为 True。

        版本分组以"父用户消息"为锚点：取该 AI 回复所响应的 user 消息（优先用
        parent_message_id，未回填则向前取同会话内最近一条 user 消息，不论是否已删除），
        再用其下一条 user 消息界定回复组时间窗，窗内所有 assistant 消息即为同轮全部版本。
        此方式不依赖 parent_message_id 是否回填，兼容原始版本未回填 parent_message_id
        的历史数据（试运行 / 普通对话流程的首版 AI 回复未回填，重新生成后会成为孤儿，
        原 parent_message_id 维度的查询会漏删 v1）。

        Args:
            message_id: 消息ID
            workspace_id: 工作空间ID
        """
        message = self.db.get(Message, message_id)
        if not message:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        # 权限校验：验证会话属于当前工作空间
        conv = self.db.get(Conversation, message.conversation_id)
        if conv.workspace_id != workspace_id:
            raise BusinessException("无权删除此消息", BizCode.PERMISSION_DENIED)

        # 删除当前消息
        message.is_deleted = True

        # AI 回复：删除同一轮的全部版本
        if message.role == "assistant":
            # 定位该回复所响应的父用户消息（版本分组锚点）
            parent_user_msg = None
            if message.parent_message_id:
                parent_user_msg = self.db.get(Message, message.parent_message_id)
                # 防御：parent_message_id 指向非 user 消息（脏数据）时回退到按时间查找
                if parent_user_msg and parent_user_msg.role != "user":
                    parent_user_msg = None
            if not parent_user_msg:
                # 向前取最近一条 user 消息作为父消息（不按 is_deleted 过滤：被删的 user
                # 消息仍是该 AI 回复实际响应的父消息，跳过它会错锚到更早的 user 消息）
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
                # 下一条 user 消息界定回复组时间窗上界（不存在则到会话末尾）。
                # 不按 is_deleted 过滤：被删的 user 消息仍标志下一回复组的起点，
                # 跳过它会让时间窗越过边界、误删下一回复组的版本。
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

                # 回复组时间窗内的全部 assistant 消息 = 同一轮的全部版本
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
            "消息已删除",
            extra={
                "message_id": str(message_id),
                "workspace_id": str(workspace_id),
            }
        )

    def get_message_versions(
            self,
            message_id: uuid.UUID,
    ) -> List[Dict[str, Any]]:
        """获取消息的所有版本

        Args:
            message_id: 消息ID

        Returns:
            List[Dict]: 版本列表
        """
        message = self.db.get(Message, message_id)
        if not message:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        # 查询同一 parent_message_id 下的所有版本
        if message.parent_message_id:
            versions = self.db.query(Message).filter(
                Message.parent_message_id == message.parent_message_id,
                Message.role == "assistant",
                Message.is_deleted.is_not(True),
            ).order_by(Message.version).all()
        else:
            # 如果没有 parent_message_id，则只返回自己
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
    ) -> Dict[str, Any]:
        """切换消息版本

        Args:
            message_id: 当前消息ID
            version: 目标版本号
            workspace_id: 工作空间ID

        Returns:
            Dict: 切换后的消息信息
        """
        current_msg = self.db.get(Message, message_id)
        if not current_msg:
            raise BusinessException("消息不存在", BizCode.NOT_FOUND)

        # 验证权限
        conv = self.db.get(Conversation, current_msg.conversation_id)
        if conv.workspace_id != workspace_id:
            raise BusinessException("无权操作此消息", BizCode.PERMISSION_DENIED)

        # 查找目标版本
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

        # 切换 is_current
        if current_msg.parent_message_id:
            # 先将所有版本设为非当前
            all_versions = self.db.query(Message).filter(
                Message.parent_message_id == current_msg.parent_message_id,
                Message.role == "assistant",
            ).all()
            for v in all_versions:
                v.is_current = False

            # 设置目标版本为当前
            target_msg.is_current = True
            self.db.commit()

        return {
            "message_id": str(target_msg.id),
            "version": target_msg.version,
            "content": target_msg.content,
        }

    async def get_conversation_detail(
            self,
            user: User,
            conversation_id: uuid.UUID,
            workspace_id: uuid.UUID,
            language: str = "zh"
    ) -> ConversationOut:
        """
        Retrieve or generate the summary and theme of a conversation.

        This method first attempts to fetch the conversation detail from the repository.
        If no detail exists or the conversation is outdated (>1 day), it generates a new
        summary using the configured LLM model, stores it, and returns it.

        Args:
            user (User): The user requesting the conversation summary.
            conversation_id (UUID): Unique identifier of the conversation.
            workspace_id (UUID): Identifier of the workspace where the conversation belongs.
            language (str, optional): Language for the summary generation. Defaults to "zh".

        Returns:
            ConversationOut: An object containing the conversation's theme, summary,
                             takeaways, and information score.

        Raises:
            BusinessException: If the workspace model is not configured, the model does
                               not exist, API keys are missing, or the LLM output is invalid.

        Notes:
            - If conversation details exist and are recent, they are returned directly.
            - LLM generation uses system and user prompt templates from the filesystem.
            - JSON repair is applied to ensure model outputs can be safely parsed.
            - Commits the new conversation detail only if it is generated or outdated.
        """
        logger.info(f"Fetching conversation detail for conversation_id={conversation_id}, workspace_id={workspace_id}")

        conversation_detail = await self.conversation_repo.get_conversation_detail_async(
            conversation_id=conversation_id,
        )
        conversation = await self.get_conversation_async(
            conversation_id=conversation_id,
        )
        if not conversation:
            raise BusinessException("Conversation not found", BizCode.INVALID_CONVERSATION)
        is_stable = (
                conversation.updated_at
                and utcnow_naive() - conversation.updated_at > timedelta(days=1)
        )
        if conversation_detail and is_stable:
            logger.info(f"Conversation detail found in repository for conversation_id={conversation_id}")
            return ConversationOut(
                theme=conversation_detail.theme,
                question=conversation_detail.question if conversation_detail.question else [],
                summary=conversation_detail.summary,
                takeaways=conversation_detail.takeaways,
                info_score=conversation_detail.info_score,
            )
        logger.info("Conversation detail not found, generating new summary using LLM")
        configs = await workspace_service.get_workspace_models_configs_async(
            db=self.db,
            workspace_id=workspace_id,
            user=user
        )
        model_id = configs.get('llm')
        if not model_id:
            logger.error(f"Workspace model configuration not found for workspace_id={workspace_id}")
            raise BusinessException("Workspace model configuration not found. Please configure a model first.", code=BizCode.MODEL_NOT_FOUND)
        config = await ModelConfigService.get_model_by_id_async(db=self.db, model_id=model_id)

        if not config:
            logger.error("Configured model not found for model_id={model_id}")
            raise BusinessException("Configured model does not exist.", BizCode.NOT_FOUND)

        tenant_id = await ToolRepository.get_tenant_id_by_workspace_id_async(self.db, str(workspace_id))
        api_config = await ModelApiKeyService.get_available_api_key_async(
            self.db,
            model_id,
            tenant_id=tenant_id,
        )
        if not api_config:
            logger.error(f"Model API keys missing for model_id={model_id}")
            raise BusinessException("Model configuration missing API keys.", BizCode.INVALID_PARAMETER)

        model_name = api_config.model_name
        provider = api_config.provider
        api_key = api_config.api_key
        api_base = api_config.api_base
        is_omni = api_config.is_omni
        capability = api_config.capability
        model_type = config.type

        llm = RedBearLLM(
            RedBearModelConfig(
                model_name=model_name,
                provider=provider,
                api_key=api_key,
                base_url=api_base,
                is_omni=is_omni,
                capability=capability,
            ),
            type=ModelType(model_type)
        )

        conversation_messages = await self.get_conversation_history(
            conversation_id=conversation_id,
            max_history=20,
            current_provider=provider,
            current_is_omni=is_omni
        )
        if len(conversation_messages) == 0:
            return ConversationOut(
                theme="",
                question=[],
                summary="",
                takeaways=[],
                info_score=0,
            )
        rendered_system_message = prompt_manager.render('conversation_summary_system')
        rendered_user_message = prompt_manager.render(
            'conversation_summary_user',
            language=language,
            conversation=str(conversation_messages)
        )

        messages = [
            (RoleType.SYSTEM, rendered_system_message),
            (RoleType.USER, rendered_user_message),
        ]
        logger.info(f"Invoking LLM for conversation_id={conversation_id}")
        model_resp = await llm.ainvoke(messages)

        try:
            if isinstance(model_resp.content, str):
                result = json_repair.repair_json(model_resp.content, return_objects=True)
            elif isinstance(model_resp.content, list):
                result = json_repair.repair_json(model_resp.content[0].get("text"), return_objects=True)
            elif isinstance(model_resp.content, dict):
                result = model_resp.content
            else:
                raise BusinessException("Unexpect model output", code=BizCode.LLM_ERROR)
        except Exception as e:
            logger.exception(f"Failed to parse LLM response for conversation_id={conversation_id}")
            raise BusinessException("Failed to parse LLM response", code=BizCode.LLM_ERROR) from e

        summary = result.get('summary', "")
        theme = result.get('theme', "")
        question = result.get("question") or []
        takeaways = result.get("takeaways") or []
        info_score = result.get("info_score", 50)

        if not is_stable:
            if not conversation_detail:
                logger.info(f"Creating conversation detail in DB for conversation_id={conversation_id}")
                conversation_detail = ConversationDetail(
                    conversation_id=conversation.id,
                    summary=summary,
                    theme=theme,
                    question=question,
                    takeaways=takeaways,
                    info_score=info_score
                )
                self.conversation_repo.add_conversation_detail(conversation_detail)
            else:
                logger.info(f"Updating conversation detail in DB for conversation_id={conversation_id}")
                conversation_detail.summary = summary
                conversation_detail.theme = theme
                conversation_detail.question = question
                conversation_detail.takeaways = takeaways
                conversation_detail.info_score = info_score

            await self.db.commit()
            await self.db.refresh(conversation_detail)

        logger.info(f"Returning conversation summary for conversation_id={conversation_id}")
        conversation_out = ConversationOut(
            theme=theme,
            question=question,
            summary=summary,
            takeaways=takeaways,
            info_score=info_score
        )
        return conversation_out


# ==================== Dependency Injection ====================

def get_conversation_service(
        db: Annotated[Session, Depends(get_db)]
) -> ConversationService:
    """
    Dependency injection function to provide ConversationService instance.

    Args:
        db (Session): Database session provided by FastAPI dependency.

    Returns:
        ConversationService: Service instance.
    """
    return ConversationService(db)

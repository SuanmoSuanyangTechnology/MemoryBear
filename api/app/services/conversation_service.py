"""会话服务"""
import uuid
from datetime import datetime, timedelta
from typing import Annotated
from typing import Optional, List, Tuple, Dict, Any

import json_repair
from fastapi import Depends
from jinja2 import Template
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.utils.datetime_utils import to_timestamp_ms, utcnow_naive
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.exceptions import ResourceNotFoundException
from app.core.logging_config import get_business_logger
from app.core.models import RedBearLLM, RedBearModelConfig
from app.db import get_db
from app.models import Conversation, Message, User, ModelType
from app.models.conversation_model import ConversationDetail
from app.models.prompt_optimizer_model import RoleType
from app.repositories.conversation_repository import ConversationRepository, MessageRepository
from app.schemas.conversation_schema import ConversationOut
from app.schemas.model_schema import ModelInfo
from app.services import workspace_service
from app.services.model_service import ModelConfigService
from app.services.prompt import prompt_manager

logger = get_business_logger()


class ConversationService:
    """
    Service layer for managing conversations and messages.
    Provides methods to create, retrieve, list, and manipulate conversations and messages.
    Delegates database operations to repositories.
    """

    def __init__(self, db: Session):
        self.db = db
        self.conversation_repo = ConversationRepository(db)
        self.message_repo = MessageRepository(db)

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

    def get_user_conversations(
            self,
            user_id: uuid.UUID,
            page: int = 1,
            page_size: int = 20
    ) -> tuple[list[Conversation], int]:
        """
        Retrieve recent conversations for a specific user with pagination.

        Args:
            user_id (uuid.UUID): Unique identifier of the user.
            page (int): Page number (1-based). Defaults to 1.
            page_size (int): Number of items per page. Defaults to 20.

        Returns:
            tuple[list[Conversation], int]: A list of recent conversation entities and total count.
        """
        conversations, total = self.conversation_repo.get_conversation_by_user_id(
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
            sync_memory: bool = True,
            should_memorize: bool = True,
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
            should_memorize (bool): 会话级记忆开关——用户在会话中切换的"记忆"按钮状态。
                True → memory_messages.should_memorize=true，会触发 Write_Pipeline；
                False → memory_messages.should_memorize=false，cursor 只推进不萃取。
                由调用方根据请求 payload.memory 透传。
                注：仅当 sync_memory=True 时生效；sync_memory=False 时本参数被忽略。

        Returns:
            Message: Newly created Message instance.
        """
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
            )

            self.message_repo.add_message(message)

            conversation.message_count += 1

            if conversation.message_count <= 2 and role == "user":
                conversation.title = (
                        content[:50] + ("..." if len(content) > 50 else "")
                )

            if sync_memory:
                self._dispatch_memory_sync(message, conversation, should_memorize)

            self.db.commit()
            self.db.refresh(message)

            logger.info(
                "Message added successfully",
                extra={
                    "conversation_id": str(conversation_id),
                    "message_id": str(message.id),
                    "role": role,
                    "content_length": len(content),
                    "sync_memory": sync_memory,
                    "should_memorize": should_memorize,
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

    @staticmethod
    def _dispatch_memory_sync(
        message: Message,
        conversation: Conversation,
        should_memorize: bool = True,
    ) -> None:
        """触发 MemoryService.sync_message 把消息同步到 memory_messages 表。

        fire-and-forget：失败仅记录 warning，不影响 messages 表的主写入流程。
        在已有事件循环中走 ensure_future（附加 done_callback 记录异常），
        否则 run_until_complete。

        Args:
            message: Message 实例（尚未 commit）
            conversation: 该消息所属的 Conversation，用于读取 app_id / workspace_id /
                is_draft / user_id（即 end_user_id）
            should_memorize: 透传给 MemoryMessage.should_memorize（会话级记忆开关）
        """
        try:
            import asyncio
            from app.core.memory.memory_service import MemoryService

            workspace_id = str(conversation.workspace_id) if conversation.workspace_id else ""
            end_user_id = str(conversation.user_id) if conversation.user_id else ""
            coro = MemoryService.sync_message(
                conversation_id=str(message.conversation_id),
                message=message,
                app_id=str(conversation.app_id),
                is_draft=conversation.is_draft,
                config_id="",
                workspace_id=workspace_id,
                end_user_id=end_user_id,
                should_memorize=should_memorize,
            )

            def _on_task_done(task: asyncio.Task) -> None:
                """回调：记录 fire-and-forget 协程中未被捕获的异常。"""
                if task.cancelled():
                    return
                exc = task.exception()
                if exc is not None:
                    logger.warning(
                        f"[ConversationService] MemoryService.sync_message 异步执行失败: "
                        f"conv={message.conversation_id}, err={exc}",
                        exc_info=exc,
                    )

            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = asyncio.ensure_future(coro)
                task.add_done_callback(_on_task_done)
            else:
                loop.run_until_complete(coro)
        except Exception as e:
            logger.warning(
                f"[ConversationService] MemoryService.sync_message 调度失败（不影响主流程）: "
                f"conv={message.conversation_id}, err={e}",
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
        messages = self.message_repo.get_message_by_conversation_id(
            conversation_id,
            limit,
            current_only=current_only
        )

        return messages

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
        messages = self.message_repo.get_message_by_conversation_id(
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

    async def delete_message(
            self,
            message_id: uuid.UUID,
            workspace_id: uuid.UUID,
    ) -> None:
        """删除单条消息（逻辑删除）

        如果消息有多个版本，则一起删除所有版本。

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

        # 如果是 assistant 消息且有 parent_message_id，删除同一 parent 下的所有版本
        if message.role == "assistant" and message.parent_message_id:
            sibling_messages = self.db.query(Message).filter(
                Message.parent_message_id == message.parent_message_id,
                Message.role == "assistant",
                Message.is_deleted.is_not(True),
            ).all()

            for sibling in sibling_messages:
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

        conversation_detail = self.conversation_repo.get_conversation_detail(
            conversation_id=conversation_id,
        )
        conversation = self.get_conversation(
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
        configs = workspace_service.get_workspace_models_configs(
            db=self.db,
            workspace_id=workspace_id,
            user=user
        )
        model_id = configs.get('llm')
        if not model_id:
            logger.error(f"Workspace model configuration not found for workspace_id={workspace_id}")
            raise BusinessException("Workspace model configuration not found. Please configure a model first.", code=BizCode.MODEL_NOT_FOUND)
        config = ModelConfigService.get_model_by_id(db=self.db, model_id=model_id)

        if not config:
            logger.error("Configured model not found for model_id={model_id}")
            raise BusinessException("Configured model does not exist.", BizCode.NOT_FOUND)

        if not config.api_keys or len(config.api_keys) == 0:
            logger.error(f"Model API keys missing for model_id={model_id}", )
            raise BusinessException("Model configuration missing API keys.", BizCode.INVALID_PARAMETER)

        api_config = config.api_keys[0]
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

            self.db.commit()
            self.db.refresh(conversation_detail)

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

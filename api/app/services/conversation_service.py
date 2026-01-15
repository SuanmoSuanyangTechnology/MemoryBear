"""会话服务"""
import uuid
from datetime import datetime, timedelta
from typing import Annotated
from typing import Optional, List, Tuple

import json_repair
from fastapi import Depends
from jinja2 import Template
from sqlalchemy.orm import Session

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
from app.services import workspace_service
from app.services.model_service import ModelConfigService

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
            user_id: uuid.UUID
    ) -> list[Conversation]:
        """
        Retrieve recent conversations for a specific user

        This method delegates persistence logic to the repository layer and
        applies service-level defaults (e.g. recent conversation limit).

        Args:
            user_id (uuid.UUID): Unique identifier of the user.

        Returns:
            list[Conversation]: A list of recent conversation entities.
        """
        conversations = self.conversation_repo.get_conversation_by_user_id(
            user_id,
            limit=10
        )
        return conversations

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
            meta_data: Optional[dict] = None
    ) -> Message:
        """
        Add a message to a conversation using UnitOfWork.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            role (str): Role of the message sender ('user' or 'assistant').
            content (str): Message content.
            meta_data (Optional[dict]): Optional metadata.

        Returns:
            Message: Newly created Message instance.
        """
        try:
            conversation = self.conversation_repo.get_conversation_by_conversation_id(
                conversation_id
            )

            message = Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                meta_data=meta_data,
            )

            self.message_repo.add_message(message)

            conversation.message_count += 1

            if conversation.message_count == 1 and role == "user":
                conversation.title = (
                        content[:50] + ("..." if len(content) > 50 else "")
                )

            self.db.commit()
            self.db.refresh(message)

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

    def get_messages(
            self,
            conversation_id: uuid.UUID,
            limit: Optional[int] = None
    ) -> List[Message]:
        """
        Retrieve messages for a conversation.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            limit (Optional[int]): Optional maximum number of messages.

        Returns:
            List[Message]: List of messages ordered by creation time.
        """
        messages = self.message_repo.get_message_by_conversation_id(
            conversation_id,
            limit
        )

        return messages

    def get_conversation_history(
            self,
            conversation_id: uuid.UUID,
            max_history: Optional[int] = None
    ) -> List[dict]:
        """
        Retrieve historical conversation messages formatted as dictionaries.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            max_history (Optional[int]): Maximum number of messages to retrieve.

        Returns:
            List[dict]: List of message dictionaries with keys 'role' and 'content'.
        """
        messages = self.message_repo.get_message_by_conversation_id(
            conversation_id,
            limit=max_history
        )

        # 转换为字典格式
        history = [
            {
                "role": msg.role,
                "content": msg.content
            }
            for msg in messages
        ]

        return history

    def save_conversation_messages(
            self,
            conversation_id: uuid.UUID,
            user_message: str,
            assistant_message: str
    ):
        """
        Save a pair of user and assistant messages to the conversation.

        Args:
            conversation_id (uuid.UUID): Conversation UUID.
            user_message (str): User's message content.
            assistant_message (str): Assistant's response content.
        """
        self.add_message(
            conversation_id=conversation_id,
            role="user",
            content=user_message
        )

        self.add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_message
        )

        logger.debug(
            "Saved conversation messages successfully",
            extra={
                "conversation_id": str(conversation_id),
                "user_message_length": len(user_message),
                "assistant_message_length": len(assistant_message)
            }
        )

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
                and datetime.now() - conversation.updated_at > timedelta(days=1)
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
        model_type = config.type

        llm = RedBearLLM(
            RedBearModelConfig(
                model_name=model_name,
                provider=provider,
                api_key=api_key,
                base_url=api_base
            ),
            type=ModelType(model_type)
        )

        conversation_messages = self.get_conversation_history(
            conversation_id=conversation_id,
            max_history=20
        )
        if len(conversation_messages) == 0:
            return ConversationOut(
                theme="",
                question=[],
                summary="",
                takeaways=[],
                info_score=0,
            )

        with open('app/services/prompt/conversation_summary_system.jinja2', 'r', encoding='utf-8') as f:
            system_prompt = f.read()
        rendered_system_message = Template(system_prompt).render()

        with open('app/services/prompt/conversation_summary_user.jinja2', 'r', encoding='utf-8') as f:
            user_prompt = f.read()
        rendered_user_message = Template(user_prompt).render(
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
        print(result)
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

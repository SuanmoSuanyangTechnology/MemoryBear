import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.response_utils import success, fail

from app.core.logging_config import get_api_logger
from app.db import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas.response_schema import ApiResponse
from app.services import conversation_service
from app.services.conversation_service import ConversationService

api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory/work",
    tags=["Perceptual Memory System"],
    dependencies=[Depends(get_current_user)]
)


@router.get("/{group_id}/count", response_model=ApiResponse)
def get_memory_count(
        group_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    pass


@router.get("/{group_id}/conversations", response_model=ApiResponse)
def get_conversations(
        group_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """
    Retrieve all conversations for the current user in a specific group.

    Args:
        group_id (UUID): The group identifier.
        current_user (User, optional): The authenticated user.
        db (Session, optional): SQLAlchemy session.

    Returns:
        ApiResponse: Contains a list of conversation IDs.

    Notes:
        - Initializes the ConversationService with the current DB session.
        - Returns only conversation IDs for lightweight response.
        - Logs can be added to trace requests in production.
    """
    conversation_service = ConversationService(db)
    conversations = conversation_service.get_user_conversations(
        group_id,
        current_user.current_workspace_id
    )
    return success(data=[conversation.id for conversation in conversations], msg="get conversations success")


@router.get("/{group_id}/messages", response_model=ApiResponse)
def get_messages(
        conversation_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """
    Retrieve the message history for a specific conversation.

    Args:
        conversation_id (UUID): The ID of the conversation to fetch messages from.
        current_user (User, optional): The authenticated user.
        db (Session, optional): SQLAlchemy session.

    Returns:
        ApiResponse: Contains the list of messages in the conversation.

    Notes:
        - Uses ConversationService to fetch messages.
        - Consider paginating results if message history is large.
        - Logging can be added for audit and debugging.
    """
    conversation_service = ConversationService(db)
    messages = conversation_service.get_conversation_history(
        conversation_id,
    )
    return success(data=messages, msg="get conversation history success")


@router.get("/{group_id}/detail", response_model=ApiResponse)
async def get_conversation_detail(
        conversation_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """
    Retrieve detailed information about a specific conversation.

    This endpoint will fetch the conversation detail for the user. If the detail
    does not exist or is outdated, it will trigger the LLM to generate a new summary.

    Args:
        conversation_id (UUID): The ID of the conversation.
        current_user (User, optional): The authenticated user making the request.
        db (Session, optional): SQLAlchemy session.

    Returns:
        ApiResponse: Contains the conversation detail serialized as a dictionary.

    Notes:
        - Uses async ConversationService to fetch or generate the conversation detail.
        - Handles workspace and user-specific context automatically.
        - Logging and exception handling should be implemented for production monitoring.
    """
    conversation_service = ConversationService(db)
    detail = await conversation_service.get_conversation_detail(
        user=current_user,
        conversation_id=conversation_id,
        workspace_id=current_user.current_workspace_id
    )
    return success(data=detail.model_dump(), msg="get conversation detail success")

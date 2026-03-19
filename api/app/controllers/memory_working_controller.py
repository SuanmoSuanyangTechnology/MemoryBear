import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.logging_config import get_api_logger
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user
from app.models import User
from app.schemas import conversation_schema
from app.schemas.response_schema import ApiResponse
from app.services.conversation_service import ConversationService

api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory/work",
    tags=["Working Memory System"],
    dependencies=[Depends(get_current_user)]
)


@router.get("/{end_user_id}/count", response_model=ApiResponse)
def get_memory_count(
        end_user_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    pass


@router.get("/{end_user_id}/conversations", response_model=ApiResponse)
def get_conversations(
        end_user_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """
    Retrieve conversations for the current user in a specific group with pagination.

    Args:
        end_user_id (UUID): The group identifier.
        page (int): Page number (1-based). Defaults to 1.
        page_size (int): Number of items per page. Defaults to 20.
        current_user (User, optional): The authenticated user.
        db (Session, optional): SQLAlchemy session.

    Returns:
        ApiResponse: Contains a paginated list of conversations.
    """
    page = max(1, page)
    page_size = max(1, min(page_size, 100))  # Limit page size between 1 and 100
    conversation_service = ConversationService(db)
    conversations, total = conversation_service.get_user_conversations(
        end_user_id,
        page=page,
        page_size=page_size
    )
    return success(data={
        "items": [
            {
                "id": conversation.id,
                "title": conversation.title
            } for conversation in conversations
        ],
        "total": total,
        "page": {
            "page": page,
            "pagesize": page_size,
            "total": total,
            "hasnext": (page * page_size) < total
        },
    }, msg="get conversations success")


@router.get("/{end_user_id}/messages", response_model=ApiResponse)
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
    messages_obj = conversation_service.get_messages(
        conversation_id,
    )
    messages = [
        conversation_schema.Message.model_validate(message)
        for message in messages_obj
    ]
    return success(data=messages, msg="get conversation history success")


@router.get("/{end_user_id}/detail", response_model=ApiResponse)
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

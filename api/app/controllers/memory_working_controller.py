import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.logging_config import get_api_logger
from app.core.memory.enums import MemoryMessageSource
from app.core.response_utils import success
from app.core.utils.datetime_utils import to_timestamp_ms
from app.db import get_db
from app.dependencies import get_current_user
from app.models import User
from app.repositories.memory_message_repository import MemoryMessageRepository
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
        pagesize: int = 20,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """
    Retrieve conversations for the current user in a specific group with pagination.

    Args:
        end_user_id (UUID): The group identifier.
        page (int): Page number (1-based). Defaults to 1.
        pagesize (int): Number of items per page. Defaults to 20.
        current_user (User, optional): The authenticated user.
        db (Session, optional): SQLAlchemy session.

    Returns:
        ApiResponse: Contains a paginated list of conversations.
    """
    page = max(1, page)
    page_size = max(1, min(pagesize, 100))  # Limit page size between 1 and 100
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


# ──────────────────────────────────────────────
# API/MCP 工作记忆展示接口
# ──────────────────────────────────────────────


def _parse_dialog_at_to_ms(dialog_at: str | None) -> int | None:
    """将 dialog_at（ISO 8601 字符串）解析为毫秒时间戳；无法解析时返回 None。"""
    if not dialog_at:
        return None
    try:
        dt = datetime.fromisoformat(dialog_at)
        return to_timestamp_ms(dt)
    except (ValueError, TypeError):
        return None


@router.get("/{end_user_id}/sources", response_model=ApiResponse)
def get_sources(
        end_user_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """查询该用户通过 API/MCP 写入的记忆来源摘要。

    每个来源（service_api / mcp）最多返回一条，包含消息总数和最后写入时间。
    用户从未用过 API/MCP 时 items 为空数组。
    """
    memory_message_repo = MemoryMessageRepository(db)
    sources = memory_message_repo.get_working_memory_sources(str(end_user_id))
    data = [
        {
            "source": row["source"],
            "message_count": row["message_count"],
            "latest_at": to_timestamp_ms(row["latest_at"]),
        }
        for row in sources
    ]
    return success(data=data, msg="查询成功")


@router.get("/{end_user_id}/source_messages", response_model=ApiResponse)
def get_source_messages(
        end_user_id: uuid.UUID,
        source: str = Query(..., description="来源：service_api 或 mcp"),
        limit: int = Query(default=20, ge=1, le=100, description="返回条数上限"),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """按来源获取最近 N 条消息，从旧到新排列，形成连贯对话流。

    Args:
        end_user_id: 终端用户 UUID
        source: service_api 或 mcp
        limit: 返回条数上限（默认 20，最大 100）
    """
    allowed_sources = {MemoryMessageSource.SERVICE_API.value, MemoryMessageSource.MCP.value}
    if source not in allowed_sources:
        return success(data={"items": [], "total": 0, "source": source}, msg="unsupported source")

    memory_message_repo = MemoryMessageRepository(db)
    rows, total = memory_message_repo.list_recent_messages_by_source(
        end_user_id=str(end_user_id),
        source=source,
        limit=limit,
    )
    items = [
        {
            "role": m.role,
            "content": m.content,
            "message_seq": m.message_seq,
            "created_at": to_timestamp_ms(m.created_at),
            "dialog_at": _parse_dialog_at_to_ms(m.dialog_at),
        }
        for m in rows
    ]
    return success(data={"items": items, "total": total, "source": source}, msg="查询成功")

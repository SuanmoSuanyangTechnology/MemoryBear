import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_api_logger
from app.core.memory.enums import MemoryMessageSource
from app.core.response_utils import success
from app.core.utils.datetime_utils import parse_timestamp_to_utc_naive, to_timestamp_ms
from app.db import get_async_db_context
from app.dependencies import get_current_user_async, CurrentUserSnapshot
from app.repositories.memory_message_repository import MemoryMessageRepository
from app.schemas import conversation_schema
from app.schemas.response_schema import ApiResponse
from app.services.conversation_service import ConversationService

api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory/work",
    tags=["Working Memory System"],
    dependencies=[Depends(get_current_user_async)],  # 兜底鉴权：每路由仍需显式声明 get_current_user_async
)


@router.get("/{end_user_id}/conversations", response_model=ApiResponse)
async def get_conversations(
        end_user_id: uuid.UUID,
        page: int = 1,
        pagesize: int = 20,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """
    Retrieve conversations for the current user in a specific group with pagination.

    Args:
        end_user_id (UUID): The group identifier.
        page (int): Page number (1-based). Defaults to 1.
        pagesize (int): Number of items per page. Defaults to 20.
        current_user (CurrentUserSnapshot, optional): The authenticated user.

    Returns:
        ApiResponse: Contains a paginated list of conversations.
    """
    page = max(1, page)
    page_size = max(1, min(pagesize, 100))  # Limit page size between 1 and 100
    async with get_async_db_context() as db:
        conversation_service = ConversationService(db)
        conversations, total = await conversation_service.get_user_conversations_async(
            end_user_id,
            page=page,
            page_size=page_size
        )
        items = [
            {
                "id": conversation.id,
                "title": conversation.title
            } for conversation in conversations
        ]
    return success(data={
        "items": items,
        "total": total,
        "page": {
            "page": page,
            "pagesize": page_size,
            "total": total,
            "hasnext": (page * page_size) < total
        },
    }, msg="get conversations success")


@router.get("/{end_user_id}/messages", response_model=ApiResponse)
async def get_messages(
        conversation_id: uuid.UUID,
        keyword: str | None = Query(default=None, description="消息正文关键词"),
        start_date: int | None = Query(default=None, ge=0, description="开始时间（毫秒时间戳，UTC）"),
        end_date: int | None = Query(default=None, ge=0, description="结束时间（毫秒时间戳，UTC）"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """
    Retrieve the message history for a specific conversation.

    Args:
        conversation_id (UUID): The ID of the conversation to fetch messages from.
        keyword (str | None): Optional keyword matched against message content.
        start_date (int | None): Optional inclusive UTC start time in milliseconds.
        end_date (int | None): Optional inclusive UTC end time in milliseconds.
        current_user (CurrentUserSnapshot, optional): The authenticated user.

    Returns:
        ApiResponse: Contains the list of messages in the conversation.

    Notes:
        - Uses ConversationService to fetch messages.
        - Consider paginating results if message history is large.
        - Logging can be added for audit and debugging.
    """
    keyword, start_at, end_at_exclusive = _normalize_message_filters(
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
    )

    async with get_async_db_context() as db:
        conversation_service = ConversationService(db)
        messages_obj = await conversation_service.get_messages_async(
            conversation_id,
            keyword=keyword,
            start_at=start_at,
            end_at_exclusive=end_at_exclusive,
        )
        messages = [
            conversation_schema.Message.model_validate(message)
            for message in messages_obj
        ]
    return success(data=messages, msg="get conversation history success")


@router.get("/{end_user_id}/detail", response_model=ApiResponse)
async def get_conversation_detail(
        conversation_id: uuid.UUID,
        end_user_id: str,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """
    Retrieve detailed information about a specific conversation.

    This endpoint will fetch the conversation detail for the user. If the detail
    does not exist or is outdated, it will trigger the LLM to generate a new summary.

    Args:
        end_user_id:
        conversation_id (UUID): The ID of the conversation.
        current_user (CurrentUserSnapshot, optional): The authenticated user making the request.

    Returns:
        ApiResponse: Contains the conversation detail serialized as a dictionary.

    Notes:
        - Uses async ConversationService to fetch or generate the conversation detail.
        - Handles workspace and user-specific context automatically.
        - Logging and exception handling should be implemented for production monitoring.
    """
    async with get_async_db_context() as db:
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


def _normalize_message_filters(
        keyword: str | None,
        start_date: int | None,
        end_date: int | None,
) -> tuple[str | None, datetime | None, datetime | None]:
    """Normalize shared message keyword and inclusive timestamp filters.

    Args:
        keyword: Optional message-content keyword.
        start_date: Optional inclusive UTC start time in milliseconds.
        end_date: Optional inclusive UTC end time in milliseconds.

    Returns:
        A normalized keyword, inclusive start datetime, and exclusive end datetime.

    Raises:
        BusinessException: If the start timestamp is greater than the end timestamp.
    """
    if start_date is not None and end_date is not None and start_date > end_date:
        raise BusinessException("start_date 不能大于 end_date", BizCode.INVALID_PARAMETER)

    normalized_keyword = keyword.strip() if keyword is not None else None
    if normalized_keyword == "":
        normalized_keyword = None

    start_at = parse_timestamp_to_utc_naive(start_date)
    end_at = parse_timestamp_to_utc_naive(end_date)
    end_at_exclusive = end_at + timedelta(milliseconds=1) if end_at is not None else None
    return normalized_keyword, start_at, end_at_exclusive


@router.get("/{end_user_id}/sources", response_model=ApiResponse)
async def get_sources(
        end_user_id: uuid.UUID,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """查询该用户通过 API/MCP 写入的记忆来源摘要。

    每个来源（service_api / mcp）最多返回一条，包含消息总数和最后写入时间。
    用户从未用过 API/MCP 时 items 为空数组。
    """
    async with get_async_db_context() as db:
        memory_message_repo = MemoryMessageRepository(db)
        sources = await memory_message_repo.get_working_memory_sources_async(str(end_user_id))
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
async def get_source_messages(
        end_user_id: uuid.UUID,
        source: str = Query(..., description="来源：service_api 或 mcp"),
        page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
        pagesize: int = Query(default=20, ge=1, le=100, description="每页数量，最大 100"),
        keyword: str | None = Query(default=None, description="消息正文关键词"),
        start_date: int | None = Query(default=None, ge=0, description="开始时间（毫秒时间戳，UTC）"),
        end_date: int | None = Query(default=None, ge=0, description="结束时间（毫秒时间戳，UTC）"),
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """按来源分页获取工作记忆消息，按 created_at 从旧到新排列，形成连贯对话流。

    分页语义（service_api / mcp 两种来源完全一致）：
    - page 从 1 开始；pagesize 默认 20、最大 100
    - 返回 page 元数据 {page, pagesize, total, hasnext}
    - 仅按 created_at ASC 排序：入库时间从早到晚，即消息从旧到新

    Args:
        end_user_id: 终端用户 UUID
        source: service_api 或 mcp
        page: 页码（默认 1，最小 1）
        pagesize: 每页数量（默认 20，最小 1，最大 100）
        keyword: Optional message-content keyword.
        start_date: Optional inclusive UTC start time in milliseconds.
        end_date: Optional inclusive UTC end time in milliseconds.
        current_user: The authenticated user snapshot.
    """
    keyword, start_at, end_at_exclusive = _normalize_message_filters(
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
    )

    allowed_sources = {MemoryMessageSource.SERVICE_API.value, MemoryMessageSource.MCP.value}
    if source not in allowed_sources:
        return success(
            data={
                "items": [],
                "source": source,
                "page": {"page": page, "pagesize": pagesize, "total": 0, "hasnext": False},
            },
            msg="unsupported source",
        )

    async with get_async_db_context() as db:
        memory_message_repo = MemoryMessageRepository(db)
        rows, total = await memory_message_repo.list_recent_messages_by_source_async(
            end_user_id=str(end_user_id),
            source=source,
            page=page,
            pagesize=pagesize,
            keyword=keyword,
            start_at=start_at,
            end_at_exclusive=end_at_exclusive,
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
    return success(
        data={
            "items": items,
            "source": source,
            "page": {
                "page": page,
                "pagesize": pagesize,
                "total": total,
                "hasnext": (page * pagesize) < total,
            },
        },
        msg="查询成功",
    )

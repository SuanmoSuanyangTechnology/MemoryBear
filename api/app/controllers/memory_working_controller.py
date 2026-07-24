import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.core.logging_config import get_api_logger
from app.core.memory.enums import MemoryMessageSource
from app.core.response_utils import success
from app.core.utils.datetime_utils import to_timestamp_ms
from app.db import get_async_db_context
from app.dependencies import CurrentUserSnapshot, get_current_user_async
from app.repositories.memory_message_repository import MemoryMessageRepository
from app.schemas import conversation_schema
from app.schemas.response_schema import ApiResponse
from app.services.conversation_service import ConversationService

api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory/work",
    tags=["Working Memory System"],
    dependencies=[Depends(get_current_user_async)],
)


@router.get("/{end_user_id}/conversations", response_model=ApiResponse)
async def get_conversations(
        end_user_id: uuid.UUID,
        page: int = 1,
        pagesize: int = 20,
):
    """
    分页查询指定用户的会话列表（异步版本）。

    Args:
        end_user_id (UUID): 终端用户 ID。
        page (int): 页码，从 1 开始。
        pagesize (int): 每页数量，最大 100。
        current_user: 已认证用户快照。

    Returns:
        ApiResponse: 分页会话列表。
    """
    page = max(1, page)
    page_size = max(1, min(pagesize, 100))

    async with get_async_db_context() as db:
        conversation_service = ConversationService(db)
        conversations, total = await conversation_service.get_user_conversations_async(
            end_user_id,
            page=page,
            page_size=page_size,
        )
        items = [
            {"id": conversation.id, "title": conversation.title}
            for conversation in conversations
        ]

    return success(data={
        "items": items,
        "total": total,
        "page": {
            "page": page,
            "pagesize": page_size,
            "total": total,
            "hasnext": (page * page_size) < total,
        },
    }, msg="get conversations success")


@router.get("/{end_user_id}/messages", response_model=ApiResponse)
async def get_messages(
        conversation_id: uuid.UUID,
):
    """
    异步查询指定会话的消息历史。

    Args:
        conversation_id (UUID): 会话 ID。
        current_user: 已认证用户快照。
    """
    async with get_async_db_context() as db:
        conversation_service = ConversationService(db)
        messages_obj = await conversation_service.get_messages_async(conversation_id)
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
    获取会话详情。若详情不存在或已过期，会触发 LLM 生成新的摘要。

    Args:
        end_user_id: 终端用户 ID
        conversation_id (UUID): 会话 ID
        current_user: 已认证用户快照

    Returns:
        ApiResponse: 会话详情字典。
    """
    async with get_async_db_context() as db:
        conversation_service = ConversationService(db)
        detail = await conversation_service.get_conversation_detail(
            user=current_user,
            conversation_id=conversation_id,
            workspace_id=current_user.current_workspace_id,
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
async def get_sources(
        end_user_id: uuid.UUID,
):
    """查询该用户通过 API/MCP 写入的记忆来源摘要（异步版本）。

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
        limit: int = Query(default=20, ge=1, le=100, description="返回条数上限"),
):
    """按来源获取最近 N 条消息（异步版本），从旧到新排列，形成连贯对话流。

    Args:
        end_user_id: 终端用户 UUID
        source: service_api 或 mcp
        limit: 返回条数上限（默认 20，最大 100）
    """
    allowed_sources = {MemoryMessageSource.SERVICE_API.value, MemoryMessageSource.MCP.value}
    if source not in allowed_sources:
        return success(data={"items": [], "total": 0, "source": source}, msg="unsupported source")

    async with get_async_db_context() as db:
        memory_message_repo = MemoryMessageRepository(db)
        rows, total = await memory_message_repo.list_recent_messages_by_source_async(
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

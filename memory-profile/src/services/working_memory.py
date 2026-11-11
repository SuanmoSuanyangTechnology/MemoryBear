"""Working-memory read use cases (T01–T04).

Every normalization rule mirrors the monolith's ``memory_working_controller``
and the repositories it calls: same silent page clamp, same keyword stripping,
same inclusive/exclusive timestamp window, same ``hasnext`` arithmetic.  No
workspace or user-ownership validation is added beyond what the monolith
already does.  Transport concerns (envelope, msg strings) stay in the module
handler; business validation failures raise :class:`ProfileError` carrying the
monolith's exact code/message so the handler can reproduce the legacy
``fail(...)`` envelope.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from src.common.errors import BizCode, ProfileError
from src.common.normalization import datetime_to_epoch_ms, epoch_ms_to_datetime
from src.repositories.working_memory import API_MCP_SOURCES, WorkingMemoryRepository
from src.runtime import ProfileRuntime
from src.schemas.working_memory import (
    ConversationItem,
    ConversationsData,
    MessageItem,
    MessagesData,
    PageInfo,
    SourceMessageItem,
    SourceMessagesData,
    SourceStatItem,
)

logger = logging.getLogger(__name__)


def _parse_dialog_at_to_ms(dialog_at: str | None) -> int | None:
    """Parse an ISO-8601 ``dialog_at`` string to millisecond timestamp."""

    if not dialog_at:
        return None
    try:
        return datetime_to_epoch_ms(datetime.fromisoformat(dialog_at))
    except (ValueError, TypeError):
        return None


def _normalize_keyword(keyword: str | None) -> str | None:
    """Monolith ``_normalize_message_filters``: strip; empty after strip → None."""

    normalized = keyword.strip() if keyword is not None else None
    return None if normalized == "" else normalized


def _validate_date_window(start_date: int | None, end_date: int | None) -> None:
    """Monolith ``_normalize_message_filters`` guard (BusinessException path)."""

    if start_date is not None and end_date is not None and start_date > end_date:
        raise ProfileError(
            BizCode.INVALID_PARAMETER,
            "start_date 不能大于 end_date",
        )


class WorkingMemoryService:
    """T01–T04 read use cases; holds data access, owns no transport detail."""

    def __init__(self, runtime: ProfileRuntime) -> None:
        self.repo = WorkingMemoryRepository(runtime.postgres)

    # ── T01: conversations ───────────────────────────────────────────────────

    async def conversations_page(
        self,
        *,
        end_user_id: str,
        page: int,
        pagesize: int,
    ) -> ConversationsData:
        # Silent clamp — matches monolith behaviour exactly.
        page = max(1, page)
        page_size = max(1, min(pagesize, 100))

        items_raw, total = await self.repo.list_conversations_by_end_user(
            end_user_id, page=page, page_size=page_size
        )
        return ConversationsData(
            items=[ConversationItem(**item) for item in items_raw],
            total=total,
            page=PageInfo(
                page=page,
                pagesize=page_size,
                total=total,
                hasnext=(page * page_size) < total,
            ),
        )

    # ── T02: messages ────────────────────────────────────────────────────────

    async def messages_page(
        self,
        *,
        conversation_id: str,
        page: int,
        pagesize: int,
        keyword: str | None,
        start_date: int | None,
        end_date: int | None,
    ) -> MessagesData:
        _validate_date_window(start_date, end_date)
        start_at = epoch_ms_to_datetime(start_date)
        end_at = epoch_ms_to_datetime(end_date)
        end_at_exclusive = end_at + timedelta(milliseconds=1) if end_at is not None else None

        items_raw, total = await self.repo.page_messages_by_conversation(
            conversation_id,
            page=page,
            pagesize=pagesize,
            keyword=_normalize_keyword(keyword),
            start_at=start_at,
            end_at_exclusive=end_at_exclusive,
        )
        items = [
            MessageItem(
                id=row["id"],
                conversation_id=row["conversation_id"],
                role=row["role"],
                content=row["content"],
                status=row["status"],
                meta_data=row.get("meta_data") or {},
                created_at=datetime_to_epoch_ms(row["created_at"]) or 0,
                feedback_type=row.get("feedback_type"),
                feedback_content=row.get("feedback_content"),
                is_favorited=row.get("is_favorited"),
                version=row.get("version", 1),
                is_current=row.get("is_current", True),
                parent_message_id=row.get("parent_message_id"),
            )
            for row in items_raw
        ]
        return MessagesData(
            items=items,
            page=PageInfo(
                page=page,
                pagesize=pagesize,
                total=total,
                hasnext=(page * pagesize) < total,
            ),
        )

    # ── T03: sources ─────────────────────────────────────────────────────────

    async def source_stats(self, *, end_user_id: str) -> list[SourceStatItem]:
        rows = await self.repo.working_memory_source_stats(end_user_id)
        return [
            SourceStatItem(
                source=row["source"],
                message_count=row["message_count"],
                # Parity: the monolith serializes MAX(created_at) as-is (null
                # when every row in the group has a NULL created_at).
                latest_at=datetime_to_epoch_ms(row["latest_at"]),
            )
            for row in rows
        ]

    # ── T04: source_messages ─────────────────────────────────────────────────

    async def source_messages_page(
        self,
        *,
        end_user_id: str,
        source: str,
        page: int,
        pagesize: int,
        keyword: str | None,
        start_date: int | None,
        end_date: int | None,
    ) -> SourceMessagesData | None:
        """Paged messages for one API/MCP source.

        Returns ``None`` when ``source`` is unsupported: the monolith answers
        with a success envelope whose items are empty and whose msg is
        "unsupported source", so the handler owns that payload.
        """

        if source not in API_MCP_SOURCES:
            return None
        _validate_date_window(start_date, end_date)
        start_at = epoch_ms_to_datetime(start_date)
        end_at = epoch_ms_to_datetime(end_date)
        end_at_exclusive = end_at + timedelta(milliseconds=1) if end_at is not None else None

        items_raw, total = await self.repo.page_messages_by_source(
            end_user_id,
            source,
            page=page,
            pagesize=pagesize,
            keyword=_normalize_keyword(keyword),
            start_at=start_at,
            end_at_exclusive=end_at_exclusive,
        )
        items = [
            SourceMessageItem(
                role=row["role"],
                content=row["content"],
                message_seq=row["message_seq"],
                created_at=datetime_to_epoch_ms(row["created_at"]) or 0,
                dialog_at=_parse_dialog_at_to_ms(row["dialog_at"]),
            )
            for row in items_raw
        ]
        return SourceMessagesData(
            items=items,
            source=source,
            page=PageInfo(
                page=page,
                pagesize=pagesize,
                total=total,
                hasnext=(page * pagesize) < total,
            ),
        )

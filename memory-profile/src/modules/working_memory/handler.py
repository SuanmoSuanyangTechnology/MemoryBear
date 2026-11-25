"""HTTP-facing handlers for working-memory read endpoints (T01–T04).

Thin transport layer in the analytics/implicit-memory style: it translates
service results into the monolith-compatible ``ApiResponse`` envelope and
converts service-raised ``ProfileError`` into the legacy ``fail(...)``
envelope (HTTP 200).  All normalization, validation and data shaping live in
``src.services.working_memory``.
"""

from __future__ import annotations

import logging

from fastapi.responses import JSONResponse

from src.common.errors import ProfileError
from src.common.responses import ApiResponse, legacy_fail_response
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime
from src.schemas.working_memory import (
    ConversationsData,
    MessagesData,
    PageInfo,
    SourceMessagesData,
    SourceStatItem,
)
from src.services.working_memory import WorkingMemoryService

logger = logging.getLogger(__name__)


class WorkingMemoryHandler:
    """Envelope adapter between the transport and the working-memory service."""

    def __init__(
        self,
        runtime: ProfileRuntime | None = None,
        *,
        service: WorkingMemoryService | None = None,
    ) -> None:
        if service is not None:
            self.service = service
        elif runtime is not None:
            self.service = WorkingMemoryService(runtime)
        else:
            raise ValueError("Either runtime or service must be provided")

    async def get_conversations(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        page: int,
        pagesize: int,
    ) -> ApiResponse[ConversationsData]:
        data = await self.service.conversations_page(
            end_user_id=end_user_id, page=page, pagesize=pagesize
        )
        return ApiResponse[ConversationsData](
            data=data, msg="get conversations success"
        )

    async def get_messages(
        self,
        context: MemoryPrincipal,
        conversation_id: str,
        page: int,
        pagesize: int,
        keyword: str | None,
        start_date: int | None,
        end_date: int | None,
    ) -> ApiResponse[MessagesData] | JSONResponse:
        try:
            data = await self.service.messages_page(
                conversation_id=conversation_id,
                page=page,
                pagesize=pagesize,
                keyword=keyword,
                start_date=start_date,
                end_date=end_date,
            )
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)
        return ApiResponse[MessagesData](
            data=data, msg="get conversation history success"
        )

    async def get_sources(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
    ) -> ApiResponse[list[SourceStatItem]]:
        items = await self.service.source_stats(end_user_id=end_user_id)
        return ApiResponse[list[SourceStatItem]](data=items, msg="查询成功")

    async def get_source_messages(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        source: str,
        page: int,
        pagesize: int,
        keyword: str | None,
        start_date: int | None,
        end_date: int | None,
    ) -> ApiResponse[SourceMessagesData] | JSONResponse:
        try:
            data = await self.service.source_messages_page(
                end_user_id=end_user_id,
                source=source,
                page=page,
                pagesize=pagesize,
                keyword=keyword,
                start_date=start_date,
                end_date=end_date,
            )
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)
        if data is None:
            # Unsupported source → success envelope with empty items, as the
            # monolith answers.
            empty = SourceMessagesData(
                items=[],
                source=source,
                page=PageInfo(page=page, pagesize=pagesize, total=0, hasnext=False),
            )
            return ApiResponse[SourceMessagesData](data=empty, msg="unsupported source")
        return ApiResponse[SourceMessagesData](data=data, msg="查询成功")

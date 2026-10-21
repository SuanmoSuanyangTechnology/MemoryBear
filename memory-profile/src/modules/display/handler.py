"""Display module transport handler."""

from __future__ import annotations

import logging
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.common.errors import BizCode, ProfileError
from src.common.pagination import PageData
from src.common.responses import ApiResponse
from src.common.validation import parse_optional_uuid
from src.i18n import resolve_language
from src.middleware.auth import MemoryPrincipal
from src.modules.display.service import MemoryDisplayService
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)

DisplayPageApiResponse = ApiResponse[PageData[dict[str, Any]]]


def validate_time_window(start_time: int | None, end_time: int | None) -> None:
    if start_time is not None and end_time is not None and start_time > end_time:
        raise ProfileError(
            BizCode.INVALID_PARAMETER,
            "start_time 不能大于 end_time",
            detail=f"start={start_time}, end={end_time}",
            status_code=400,
        )


def validate_timezone(timezone_str: str | None) -> str:
    if not timezone_str or not timezone_str.strip():
        raise ProfileError(
            BizCode.MISSING_PARAMETER,
            "X-Timezone 不能为空",
            detail="X-Timezone header is required",
            status_code=400,
        )
    normalized = timezone_str.strip()
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ProfileError(
            BizCode.INVALID_PARAMETER,
            f"无效的时区: {normalized}",
            detail=str(exc),
            status_code=400,
        ) from exc
    return normalized


class MemoryDisplayHandler:
    def __init__(
        self,
        runtime: ProfileRuntime | None = None,
        *,
        service: MemoryDisplayService | None = None,
    ) -> None:
        if service is not None:
            self.service = service
        elif runtime is not None:
            self.service = MemoryDisplayService(runtime)
        else:
            raise ValueError("Either runtime or service must be provided")

    async def get_written(
        self,
        context: MemoryPrincipal,
        *,
        page: int,
        pagesize: int,
        end_user_id: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> DisplayPageApiResponse:
        validate_time_window(start_time, end_time)
        user_uuid = parse_optional_uuid(end_user_id)
        items, total = await self.service.query_written(
            workspace_id=context.workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=user_uuid,
            start_time=start_time,
            end_time=end_time,
        )
        return ApiResponse(
            data=PageData.create(
                page=page,
                pagesize=pagesize,
                total=total,
                items=items,
            ),
            msg="查询成功",
        )

    async def get_retrieved(
        self,
        context: MemoryPrincipal,
        *,
        page: int,
        pagesize: int,
        end_user_id: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> DisplayPageApiResponse:
        validate_time_window(start_time, end_time)
        user_uuid = parse_optional_uuid(end_user_id)
        items, total = await self.service.query_retrieved(
            workspace_id=context.workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=user_uuid,
            start_time=start_time,
            end_time=end_time,
        )
        return ApiResponse(
            data=PageData.create(
                page=page,
                pagesize=pagesize,
                total=total,
                items=items,
            ),
            msg="查询成功",
        )

    async def get_engines(
        self,
        context: MemoryPrincipal,
        *,
        timezone: str,
        language_type: str | None,
        page: int,
        pagesize: int,
        end_user_id: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> DisplayPageApiResponse:
        validate_time_window(start_time, end_time)
        tz_name = validate_timezone(timezone)
        language = resolve_language(language_type)
        user_uuid = parse_optional_uuid(end_user_id)

        cards, total = await self.service.query_engines(
            workspace_id=context.workspace_id,
            timezone=tz_name,
            language=language,
            page=page,
            pagesize=pagesize,
            end_user_id=user_uuid,
            start_time=start_time,
            end_time=end_time,
        )
        return ApiResponse(
            data=PageData.create(
                page=page,
                pagesize=pagesize,
                total=total,
                items=cards,
            ),
            msg="查询成功",
        )

    async def get_all(
        self,
        context: MemoryPrincipal,
        *,
        timezone: str,
        language_type: str | None,
        page: int,
        pagesize: int,
        end_user_id: str | None = None,
        include_engines: bool = True,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> DisplayPageApiResponse:
        validate_time_window(start_time, end_time)
        tz_name = validate_timezone(timezone)
        language = resolve_language(language_type)
        user_uuid = parse_optional_uuid(end_user_id)

        items, total = await self.service.query_all(
            workspace_id=context.workspace_id,
            timezone=tz_name,
            language=language,
            page=page,
            pagesize=pagesize,
            end_user_id=user_uuid,
            include_engines=include_engines,
            start_time=start_time,
            end_time=end_time,
        )
        return ApiResponse(
            data=PageData.create(
                page=page,
                pagesize=pagesize,
                total=total,
                items=items,
            ),
            msg="查询成功",
        )

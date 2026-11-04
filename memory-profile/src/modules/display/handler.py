"""Display module transport handler."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.common.errors import BizCode, ProfileError
from src.common.normalization import resolve_timezone
from src.common.pagination import PageData
from src.common.responses import ApiResponse, failure
from src.common.validation import parse_optional_uuid
from src.i18n import resolve_language
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime
from src.schemas.display import (
    ActivityCompositionData,
    ActivityStatisticsData,
    AllItem,
    EngineItem,
    RetrievedItem,
    WrittenItem,
)
from src.services.display.query import MemoryDisplayService, activity_range

logger = logging.getLogger(__name__)


WrittenPageApiResponse = ApiResponse[PageData[WrittenItem]]
RetrievedPageApiResponse = ApiResponse[PageData[RetrievedItem]]
EnginePageApiResponse = ApiResponse[PageData[EngineItem]]
AllPageApiResponse = ApiResponse[PageData[AllItem]]


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
    try:
        _, normalized = resolve_timezone(timezone_str)
    except ValueError as exc:
        raise ProfileError(
            BizCode.INVALID_PARAMETER,
            f"无效的时区: {timezone_str.strip()}",
            detail=str(exc.__cause__ or exc),
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

    async def get_yearly_activity_statistics(
        self, context: MemoryPrincipal, year: str | None, timezone_name: str | None
    ):
        if context.workspace_id is None:
            return failure(
                BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None"
            )
        try:
            zone, zone_name = resolve_timezone(timezone_name)
            first, length, start, end = activity_range(datetime.now(UTC), zone, year, None, True)
        except ValueError as exc:
            return failure(BizCode.INVALID_PARAMETER, "无效的统计参数", str(exc))
        try:
            data = await self.service.get_yearly_activity_statistics(
                context.workspace_id, zone, zone_name, first, length, start, end
            )
            return ApiResponse[ActivityStatisticsData](data=data)
        except Exception as exc:
            logger.exception("年度记忆活动统计失败")
            return failure(BizCode.INTERNAL_ERROR, "年度记忆活动统计失败", str(exc))

    async def get_monthly_activity_composition(
        self,
        context: MemoryPrincipal,
        year: str | None,
        month: str | None,
        timezone_name: str | None,
    ):
        if context.workspace_id is None:
            return failure(
                BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None"
            )
        try:
            zone, zone_name = resolve_timezone(timezone_name)
            first, length, start, end = activity_range(datetime.now(UTC), zone, year, month, False)
        except ValueError as exc:
            return failure(BizCode.INVALID_PARAMETER, "无效的统计参数", str(exc))
        try:
            data = await self.service.get_monthly_activity_composition(
                context.workspace_id, zone, zone_name, first, length, start, end
            )
            return ApiResponse[ActivityCompositionData](data=data)
        except Exception as exc:
            logger.exception("月度记忆活动统计失败")
            return failure(BizCode.INTERNAL_ERROR, "月度记忆活动统计失败", str(exc))

    async def get_written(
        self,
        context: MemoryPrincipal,
        *,
        page: int,
        pagesize: int,
        end_user_id: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> WrittenPageApiResponse:
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
        return WrittenPageApiResponse(
            data=PageData[WrittenItem].create(
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
    ) -> RetrievedPageApiResponse:
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
        return RetrievedPageApiResponse(
            data=PageData[RetrievedItem].create(
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
    ) -> EnginePageApiResponse:
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
        return EnginePageApiResponse(
            data=PageData[EngineItem].create(
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
    ) -> AllPageApiResponse:
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
        return AllPageApiResponse(
            data=PageData[AllItem].create(
                page=page,
                pagesize=pagesize,
                total=total,
                items=items,
            ),
            msg="查询成功",
        )

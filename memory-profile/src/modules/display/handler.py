"""Display module transport handler."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.common.errors import BizCode, LocalizedProfileError, ProfileError
from src.common.normalization import resolve_timezone
from src.common.pagination import PageData
from src.common.responses import ApiResponse
from src.common.validation import parse_optional_uuid
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


# v0.5.6–v0.5.9 V1 compatibility; remove this policy in v0.5.10.
def _v1_display_exclusions(context: MemoryPrincipal, kind: str) -> dict:
    if context.source != "ex_api":
        return {}
    exclusions = {}
    if kind in {"retrieved", "all"}:
        exclusions["excluded_search_modes"] = ("community",)
    if kind in {"engines", "all"}:
        exclusions["excluded_engine_types"] = ("PERSONALITY_ANCHOR",)
    return exclusions


def validate_time_window(start_time: int | None, end_time: int | None) -> None:
    if start_time is not None and end_time is not None and start_time > end_time:
        raise LocalizedProfileError(
            BizCode.INVALID_PARAMETER,
            "memory.display.error.invalid_time_window",
            detail=f"start={start_time}, end={end_time}",
            status_code=400,
        )


def validate_timezone(timezone_str: str | None) -> str:
    if not timezone_str or not timezone_str.strip():
        raise LocalizedProfileError(
            BizCode.MISSING_PARAMETER,
            "memory.display.error.timezone_required",
            detail_key="memory.display.error.timezone_header_required",
            status_code=400,
        )
    try:
        _, normalized = resolve_timezone(timezone_str)
    except ValueError as exc:
        raise LocalizedProfileError(
            BizCode.INVALID_PARAMETER,
            "memory.display.error.invalid_timezone",
            message_params={"timezone": timezone_str.strip()},
            detail_key="memory.display.error.invalid_iana_timezone",
            detail_params={"timezone": timezone_str.strip()},
            status_code=400,
        ) from exc
    return normalized


def _statistics_window(
    timezone_name: str | None, year: str | None, month: str | None, *, annual: bool
):
    try:
        zone, zone_name = resolve_timezone(timezone_name)
    except ValueError as exc:
        name = (timezone_name or "").strip()
        raise LocalizedProfileError(
            BizCode.INVALID_PARAMETER,
            "memory.display.error.invalid_statistics_parameters",
            detail_key=(
                "memory.display.error.invalid_iana_timezone"
                if name
                else "memory.display.error.timezone_required"
            ),
            detail_params={"timezone": name},
            status_code=200,
        ) from exc
    first, length, start, end = activity_range(datetime.now(UTC), zone, year, month, annual)
    return zone, zone_name, first, length, start, end


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
            raise LocalizedProfileError(
                BizCode.INVALID_PARAMETER,
                "memory.errors.workspace_required",
                detail_key="memory.errors.current_workspace_missing",
                status_code=200,
            )
        zone, zone_name, first, length, start, end = _statistics_window(
            timezone_name, year, None, annual=True
        )
        try:
            data = await self.service.get_yearly_activity_statistics(
                context.workspace_id, zone, zone_name, first, length, start, end
            )
            return ApiResponse[ActivityStatisticsData](data=data)
        except Exception as exc:
            logger.exception("年度记忆活动统计失败")
            raise LocalizedProfileError(
                BizCode.INTERNAL_ERROR,
                "memory.display.error.yearly_statistics_failed",
                detail=str(exc),
                status_code=200,
            ) from exc

    async def get_monthly_activity_composition(
        self,
        context: MemoryPrincipal,
        year: str | None,
        month: str | None,
        timezone_name: str | None,
    ):
        if context.workspace_id is None:
            raise LocalizedProfileError(
                BizCode.INVALID_PARAMETER,
                "memory.errors.workspace_required",
                detail_key="memory.errors.current_workspace_missing",
                status_code=200,
            )
        zone, zone_name, first, length, start, end = _statistics_window(
            timezone_name, year, month, annual=False
        )
        try:
            data = await self.service.get_monthly_activity_composition(
                context.workspace_id, zone, zone_name, first, length, start, end
            )
            return ApiResponse[ActivityCompositionData](data=data)
        except Exception as exc:
            logger.exception("月度记忆活动统计失败")
            raise LocalizedProfileError(
                BizCode.INTERNAL_ERROR,
                "memory.display.error.monthly_statistics_failed",
                detail=str(exc),
                status_code=200,
            ) from exc

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
        user_uuid = parse_optional_uuid(end_user_id)
        validate_time_window(start_time, end_time)
        items, total = await self._read(
            self.service.query_written,
            "memory.display.error.written_failed",
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
        user_uuid = parse_optional_uuid(end_user_id)
        validate_time_window(start_time, end_time)
        items, total = await self._read(
            self.service.query_retrieved,
            "memory.display.error.retrieved_failed",
            workspace_id=context.workspace_id,
            page=page,
            pagesize=pagesize,
            end_user_id=user_uuid,
            start_time=start_time,
            end_time=end_time,
            **_v1_display_exclusions(context, "retrieved"),
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
        user_uuid = parse_optional_uuid(end_user_id)
        tz_name = validate_timezone(timezone)
        validate_time_window(start_time, end_time)
        language = str(getattr(language_type, "value", language_type) or "zh").lower().strip()
        if language not in {"zh", "en"}:
            language = "zh"

        cards, total = await self._read(
            self.service.query_engines,
            "memory.display.error.engines_failed",
            workspace_id=context.workspace_id,
            timezone=tz_name,
            language=language,
            page=page,
            pagesize=pagesize,
            end_user_id=user_uuid,
            start_time=start_time,
            end_time=end_time,
            **_v1_display_exclusions(context, "engines"),
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
        user_uuid = parse_optional_uuid(end_user_id)
        tz_name = validate_timezone(timezone)
        validate_time_window(start_time, end_time)
        language = str(getattr(language_type, "value", language_type) or "zh").lower().strip()
        if language not in {"zh", "en"}:
            language = "zh"

        items, total = await self._read(
            self.service.query_all,
            "memory.display.error.all_failed",
            workspace_id=context.workspace_id,
            timezone=tz_name,
            language=language,
            page=page,
            pagesize=pagesize,
            end_user_id=user_uuid,
            include_engines=include_engines,
            start_time=start_time,
            end_time=end_time,
            **_v1_display_exclusions(context, "all"),
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

    @staticmethod
    async def _read(method, message_key: str, **kwargs):
        try:
            return await method(**kwargs)
        except ProfileError:
            raise
        except Exception as exc:
            logger.exception("Memory display read failed: operation=%s", message_key)
            raise LocalizedProfileError(
                BizCode.INTERNAL_ERROR, message_key, detail_key=message_key, status_code=200
            ) from exc

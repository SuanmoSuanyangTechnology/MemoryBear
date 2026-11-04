"""HTTP-facing workspace activity handler."""

import logging
from datetime import UTC, datetime

from src.common.errors import BizCode
from src.common.responses import ApiResponse, failure
from src.middleware.auth import MemoryPrincipal
from src.modules.memory_display.schemas import ActivityCompositionData, ActivityStatisticsData
from src.runtime import ProfileRuntime
from src.services.activity_statistics import (
    ActivityStatisticsService,
    activity_range,
    resolve_timezone,
)

logger = logging.getLogger(__name__)


class MemoryDisplayHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.service = ActivityStatisticsService(runtime)

    async def statistics(
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
            data = await self.service.statistics(
                context.workspace_id, zone, zone_name, first, length, start, end
            )
            return ApiResponse[ActivityStatisticsData](data=data)
        except Exception as exc:
            logger.exception("年度记忆活动统计失败")
            return failure(BizCode.INTERNAL_ERROR, "年度记忆活动统计失败", str(exc))

    async def composition(
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
            data = await self.service.composition(
                context.workspace_id, zone, zone_name, first, length, start, end
            )
            return ApiResponse[ActivityCompositionData](data=data)
        except Exception as exc:
            logger.exception("月度记忆活动统计失败")
            return failure(BizCode.INTERNAL_ERROR, "月度记忆活动统计失败", str(exc))

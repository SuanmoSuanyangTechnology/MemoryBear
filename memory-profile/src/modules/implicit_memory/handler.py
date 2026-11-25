"""HTTP handlers for implicit memory profile reads."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from src.common.errors import BizCode, LocalizedProfileError, ProfileError
from src.common.responses import ApiResponse, failure
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime
from src.schemas.implicit_memory import ImplicitProfile, ProfileExists, SnapshotSection
from src.services.implicit_memory import ImplicitMemoryService, implicit_read_error
from src.services.implicit_memory_generation.service import ImplicitMemoryGenerationService

logger = logging.getLogger(__name__)

ExistsApiResponse = ApiResponse[ProfileExists]
ObjectApiResponse = ApiResponse[SnapshotSection]
ListApiResponse = ApiResponse[list[Any]]
AggregateApiResponse = ApiResponse[ImplicitProfile]


class ImplicitMemoryHandler:
    def __init__(
        self,
        runtime: ProfileRuntime | None = None,
        *,
        service: ImplicitMemoryService | None = None,
    ) -> None:
        self.runtime = runtime
        if service is not None:
            self.service = service
        elif runtime is not None:
            self.service = ImplicitMemoryService(runtime)
        else:
            raise ValueError("Either runtime or service must be provided")

    async def check_data(self, context: MemoryPrincipal, end_user_id: str) -> ExistsApiResponse:
        exists = await self._read(
            self.service.check_data,
            "memory.implicit.error.check_data_failed",
            context,
            end_user_id,
        )
        return ExistsApiResponse(
            data={"exists": exists},
            msg=("画像数据已存在" if exists else "画像数据不存在，请点击右上角刷新进行初始化"),
        )

    async def preferences(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        *,
        confidence_threshold: float,
        tag_category: str | None,
        start_date: datetime | None,
        end_date: datetime | None,
    ) -> ListApiResponse:
        data = await self._read(
            self.service.preferences,
            "memory.implicit.error.preferences_failed",
            context,
            end_user_id,
            confidence_threshold=confidence_threshold,
            tag_category=tag_category,
            start_date=start_date,
            end_date=end_date,
        )
        return ListApiResponse(data=data, msg="偏好标签获取成功（缓存）")

    async def portrait(self, context: MemoryPrincipal, end_user_id: str) -> ObjectApiResponse:
        data = await self._read(
            self.service.portrait,
            "memory.implicit.error.portrait_failed",
            context,
            end_user_id,
        )
        return ObjectApiResponse(data={} if data is None else data, msg="四维画像获取成功（缓存）")

    async def interest_areas(self, context: MemoryPrincipal, end_user_id: str) -> ObjectApiResponse:
        data = await self._read(
            self.service.interest_areas,
            "memory.implicit.error.interest_areas_failed",
            context,
            end_user_id,
        )
        return ObjectApiResponse(
            data={} if data is None else data, msg="兴趣领域分布获取成功（缓存）"
        )

    async def habits(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        *,
        confidence_level: Literal["high", "medium", "low"] | None,
        frequency_pattern: Literal[
            "daily", "weekly", "monthly", "seasonal", "occasional", "event_triggered"
        ]
        | None,
        time_period: Literal["current", "past"] | None,
    ) -> ListApiResponse:
        data = await self._read(
            self.service.habits,
            "memory.implicit.error.habits_failed",
            context,
            end_user_id,
            confidence_level=confidence_level,
            frequency_pattern=frequency_pattern,
            time_period=time_period,
        )
        return ListApiResponse(data=data, msg="行为习惯获取成功（缓存）")

    async def aggregate(self, context: MemoryPrincipal, end_user_id: str) -> AggregateApiResponse:
        data = await self._read(
            self.service.aggregate,
            "memory.implicit.error.aggregate_failed",
            context,
            end_user_id,
            include_detail=False,
        )
        if data is None:
            raise LocalizedProfileError(
                BizCode.NOT_FOUND,
                "memory.implicit.error.snapshot_not_ready",
                status_code=200,
            )
        return AggregateApiResponse(data=data, msg="查询成功")

    @staticmethod
    async def _read(
        method,
        operation_key: str,
        context: MemoryPrincipal,
        end_user_id: str,
        *,
        include_detail: bool = True,
        **filters,
    ) -> Any:
        try:
            if not isinstance(end_user_id, str) or not end_user_id.strip():
                raise LocalizedProfileError(
                    BizCode.INVALID_FILTER_PARAMS,
                    "memory.implicit.error.invalid_parameters",
                    detail_key="memory.implicit.error.user_id_empty",
                    status_code=200,
                )
            return await method(
                end_user_id=end_user_id, workspace_id=context.workspace_id, **filters
            )
        except ProfileError:
            raise
        except Exception as exc:
            raise implicit_read_error(exc, operation_key, include_detail=include_detail) from exc

    async def generate_profile(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> ObjectApiResponse:
        try:
            if not end_user_id or not end_user_id.strip():
                raise ValueError("User ID cannot be empty")
            service = await ImplicitMemoryGenerationService.create(
                self.runtime, end_user_id, context.workspace_id
            )
            profile_data = await service.generate_complete_profile(user_id=end_user_id)
            await service.save_profile_cache(end_user_id, profile_data)
            profile_data["end_user_id"] = end_user_id
            profile_data["cached"] = False
            return ObjectApiResponse(data=profile_data, msg="用户画像生成成功")
        except Exception as exc:
            return _generation_error(exc, "用户画像生成", end_user_id)


def _generation_error(e: Exception, operation: str, user_id: str = None) -> ApiResponse[Any]:
    """
    Centralized error handling for implicit memory operations.

    Args:
        e: The exception that occurred
        operation: Description of the operation that failed
        user_id: Optional user ID for logging context

    Returns:
        Standardized error response
    """
    error_context = f"user_id={user_id}" if user_id else "unknown user"

    if isinstance(e, ValueError):
        if "user" in str(e).lower() and "not found" in str(e).lower():
            logger.warning(f"Invalid user ID for {operation}: {error_context}")
            return failure(BizCode.INVALID_USER_ID, "无效的用户ID", str(e))
        elif "insufficient" in str(e).lower() or "no data" in str(e).lower():
            logger.warning(f"Insufficient data for {operation}: {error_context}")
            return failure(BizCode.INSUFFICIENT_DATA, "数据不足，无法进行分析", str(e))
        else:
            logger.warning(f"Invalid parameters for {operation}: {error_context}")
            return failure(BizCode.INVALID_FILTER_PARAMS, "无效的参数", str(e))

    elif isinstance(e, KeyError):
        logger.warning(f"Missing required data for {operation}: {error_context}")
        return failure(BizCode.INSUFFICIENT_DATA, "缺少必要的数据", str(e))

    elif isinstance(e, (ConnectionError, TimeoutError)):
        logger.error(f"Service unavailable for {operation}: {error_context}")
        return failure(BizCode.SERVICE_UNAVAILABLE, "服务暂时不可用", str(e))

    elif "analysis" in str(e).lower() or "llm" in str(e).lower():
        logger.error(f"Analysis failed for {operation}: {error_context}", exc_info=True)
        return failure(BizCode.ANALYSIS_FAILED, "分析处理失败", str(e))

    elif "storage" in str(e).lower() or "database" in str(e).lower():
        logger.error(f"Storage error for {operation}: {error_context}", exc_info=True)
        return failure(BizCode.PROFILE_STORAGE_ERROR, "数据存储失败", str(e))

    else:
        logger.error(f"Unexpected error for {operation}: {error_context}", exc_info=True)
        return failure(BizCode.INTERNAL_ERROR, f"{operation}失败", str(e))

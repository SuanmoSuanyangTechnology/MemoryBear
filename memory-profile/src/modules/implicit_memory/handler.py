"""HTTP handlers for implicit memory profile reads."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime
from src.schemas.implicit_memory import ImplicitProfile, ProfileExists, SnapshotSection
from src.services.implicit_memory import ImplicitMemoryService, implicit_read_error

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
        if service is not None:
            self.service = service
        elif runtime is not None:
            self.service = ImplicitMemoryService(runtime)
        else:
            raise ValueError("Either runtime or service must be provided")

    async def check_data(self, context: MemoryPrincipal, end_user_id: str) -> ExistsApiResponse:
        exists = await self._read(
            self.service.check_data,
            "检查画像数据",
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
            "偏好标签获取",
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
            "四维画像获取",
            context,
            end_user_id,
        )
        return ObjectApiResponse(data={} if data is None else data, msg="四维画像获取成功（缓存）")

    async def interest_areas(self, context: MemoryPrincipal, end_user_id: str) -> ObjectApiResponse:
        data = await self._read(
            self.service.interest_areas,
            "兴趣领域分布获取",
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
            "行为习惯获取",
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
            "隐性记忆查询",
            context,
            end_user_id,
            include_detail=False,
        )
        if data is None:
            raise ProfileError(
                BizCode.NOT_FOUND,
                "隐性记忆快照尚未生成",
                status_code=200,
            )
        return AggregateApiResponse(data=data, msg="查询成功")

    @staticmethod
    async def _read(
        method,
        operation: str,
        context: MemoryPrincipal,
        end_user_id: str,
        *,
        include_detail: bool = True,
        **filters,
    ) -> Any:
        try:
            if not isinstance(end_user_id, str) or not end_user_id.strip():
                raise ValueError("User ID cannot be empty")
            return await method(
                end_user_id=end_user_id, workspace_id=context.workspace_id, **filters
            )
        except ProfileError:
            raise
        except Exception as exc:
            raise implicit_read_error(exc, operation, include_detail=include_detail) from exc

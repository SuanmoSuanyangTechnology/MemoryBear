"""HTTP handlers for implicit memory profile reads."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse
from src.common.validation import parse_required_uuid
from src.middleware.auth import MemoryPrincipal
from src.modules.implicit_memory.service import ImplicitMemoryService
from src.runtime import ProfileRuntime

ObjectApiResponse = ApiResponse[dict[str, Any]]
ListApiResponse = ApiResponse[list[dict[str, Any]]]


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

    async def check_data(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> ObjectApiResponse:
        exists = await self.service.check_data(
            end_user_id=parse_required_uuid(end_user_id),
            workspace_id=context.workspace_id,
        )
        return ObjectApiResponse(
            data={"exists": exists},
            msg=(
                "画像数据已存在"
                if exists
                else "画像数据不存在，请点击右上角刷新进行初始化"
            ),
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
        data = await self.service.preferences(
            end_user_id=parse_required_uuid(end_user_id),
            workspace_id=context.workspace_id,
            confidence_threshold=confidence_threshold,
            tag_category=tag_category,
            start_date=start_date,
            end_date=end_date,
        )
        return ListApiResponse(data=data, msg="偏好标签获取成功（缓存）")

    async def portrait(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> ObjectApiResponse:
        data = await self.service.portrait(
            end_user_id=parse_required_uuid(end_user_id),
            workspace_id=context.workspace_id,
        )
        return ObjectApiResponse(data=data, msg="四维画像获取成功（缓存）")

    async def interest_areas(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> ObjectApiResponse:
        data = await self.service.interest_areas(
            end_user_id=parse_required_uuid(end_user_id),
            workspace_id=context.workspace_id,
        )
        return ObjectApiResponse(data=data, msg="兴趣领域分布获取成功（缓存）")

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
        data = await self.service.habits(
            end_user_id=parse_required_uuid(end_user_id),
            workspace_id=context.workspace_id,
            confidence_level=confidence_level,
            frequency_pattern=frequency_pattern,
            time_period=time_period,
        )
        return ListApiResponse(data=data, msg="行为习惯获取成功（缓存）")

    async def aggregate(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> ObjectApiResponse:
        parsed = parse_required_uuid(end_user_id)
        data = await self.service.aggregate(
            end_user_id=parsed,
            workspace_id=context.workspace_id,
        )
        if data is None:
            raise ProfileError(
                BizCode.NOT_FOUND,
                "隐性记忆快照尚未生成",
                status_code=404,
            )
        return ObjectApiResponse(data=data, msg="查询成功")

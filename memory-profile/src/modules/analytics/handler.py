"""HTTP-facing analytics handlers."""

from __future__ import annotations

import logging
from typing import Any

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse
from src.middleware.auth import MemoryPrincipal
from src.modules.analytics.repository.memory_graph import SUPPORTED_NODE_TYPES
from src.modules.analytics.service import ProfileAnalyticsService
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)
ObjectApiResponse = ApiResponse[dict[str, Any]]
ListApiResponse = ApiResponse[list[dict[str, Any]]]


class AnalyticsHandler:
    """Translate transport inputs and service results without owning data access."""

    def __init__(self, runtime: ProfileRuntime) -> None:
        self.service = ProfileAnalyticsService(runtime)

    @staticmethod
    def parse_per_type_limits(value: str | None) -> dict[str, int] | None:
        if not value:
            return None
        result: dict[str, int] = {}
        try:
            for item in value.split(","):
                item = item.strip()
                if not item:
                    continue
                node_type, raw_limit = (part.strip() for part in item.split(":", 1))
                if not node_type.isidentifier():
                    raise ValueError(f"invalid node type: {node_type}")
                limit = int(raw_limit)
                if limit < 0:
                    raise ValueError(f"limit for {node_type} must be non-negative")
                if node_type not in SUPPORTED_NODE_TYPES:
                    logger.warning("ignoring unsupported graph node type: %s", node_type)
                    continue
                result[node_type] = min(limit, 500)
        except (TypeError, ValueError) as exc:
            raise ProfileError(
                BizCode.INVALID_PARAMETER,
                "per_type_limits 参数格式错误",
                detail=str(exc),
            ) from exc
        return result

    async def memory_insight(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> ApiResponse[dict[str, Any]]:
        data = await self.service.memory_insight(end_user_id, context.workspace_id)
        return ObjectApiResponse(
            data=data,
            msg="查询成功" if data.get("is_cached") else "数据尚未生成",
        )

    async def user_summary(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        language_type: str | None,
    ) -> ApiResponse[dict[str, Any]]:
        del language_type
        data = await self.service.user_summary(end_user_id, context.workspace_id)
        return ObjectApiResponse(
            data=data,
            msg="查询成功" if data.get("is_cached") else "数据尚未生成",
        )

    async def node_statistics(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> ApiResponse[list[dict[str, Any]]]:
        data = await self.service.node_statistics(end_user_id, context.workspace_id)
        return ListApiResponse(data=data, msg="查询成功")

    async def workspace_statistics(
        self, context: MemoryPrincipal
    ) -> ApiResponse[dict[str, Any]]:
        data = await self.service.workspace_statistics(context.workspace_id)
        return ObjectApiResponse(data=data, msg="查询成功")

    async def graph_data(
        self,
        context: MemoryPrincipal,
        *,
        end_user_id: str,
        node_types: str | None,
        limit: int,
        depth: int,
        center_node_id: str | None,
        per_type_limits: str | None,
    ) -> ApiResponse[dict[str, Any]]:
        types = (
            [item.strip() for item in node_types.split(",") if item.strip()] if node_types else None
        )
        data = await self.service.graph_data(
            end_user_id,
            context.workspace_id,
            types,
            limit,
            depth,
            center_node_id,
            self.parse_per_type_limits(per_type_limits),
        )
        return ObjectApiResponse(data=data, msg=data.get("message", "查询成功"))

    async def community_graph(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> ApiResponse[dict[str, Any]]:
        data = await self.service.community_graph(end_user_id, context.workspace_id)
        return ObjectApiResponse(data=data, msg=data.get("message", "查询成功"))

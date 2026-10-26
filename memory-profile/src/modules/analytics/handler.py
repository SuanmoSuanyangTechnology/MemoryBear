"""HTTP-facing analytics handlers."""

from __future__ import annotations

import logging
from typing import Any

from src.common.errors import BizCode, LocalizedProfileError
from src.common.responses import ApiResponse
from src.i18n import message
from src.middleware.auth import MemoryPrincipal
from src.modules.analytics.constants import (
    SINGLE_TYPE_LIMIT_HARD_MAX,
    SUPPORTED_NODE_TYPES,
)
from src.modules.analytics.service import ProfileAnalyticsService
from src.runtime import ProfileRuntime

logger = logging.getLogger(__name__)
ObjectApiResponse = ApiResponse[dict[str, Any]]
ListApiResponse = ApiResponse[list[dict[str, Any]]]


def _localize_message(data: dict[str, Any], language: str) -> dict[str, Any]:
    """Render the service's language-neutral message key into ``data.message``."""

    message_key = data.pop("message_key", None)
    if isinstance(message_key, str):
        data["message"] = message(message_key, language)
    return data


class AnalyticsHandler:
    """Translate transport inputs and service results without owning data access."""

    def __init__(self, runtime: ProfileRuntime) -> None:
        self.service = ProfileAnalyticsService(runtime)

    @staticmethod
    def parse_per_type_limits(value: str | None) -> dict[str, int] | None:
        """解析 ``Type:Limit`` 逗号串为 ``{Node_Type: Per_Type_Limit}``（api 同规则同文案）。

        - 缺冒号 / Node_Type 非标识符 / Limit 非整数 / Limit 为负 → ``INVALID_PARAMETER``，
          ``error`` 字段是 api ``parse_per_type_limits`` 的原文；
        - Node_Type 不在受支持集合 → 记 warning 后跳过该条目（不整体失败）；
        - Limit 超过单类型硬上限 → 截断并 warning；
        - Limit 为 0 → 保留，调用方据此跳过该类型的查询。
        """

        if not value or not value.strip():
            return None
        result: dict[str, int] = {}
        try:
            for entry in value.split(","):
                entry = entry.strip()
                if entry == "":
                    continue
                if ":" not in entry:
                    raise LocalizedProfileError(
                        BizCode.INVALID_PARAMETER,
                        "analytics.error.invalid_per_type_limits",
                        detail_key="analytics.error.per_type_missing_colon",
                        detail_params={"entry": entry},
                    )
                node_type, limit_part = (part.strip() for part in entry.split(":", 1))
                if not node_type.isidentifier():
                    raise LocalizedProfileError(
                        BizCode.INVALID_PARAMETER,
                        "analytics.error.invalid_per_type_limits",
                        detail_key="analytics.error.per_type_invalid_node_type",
                        detail_params={"node_type": node_type},
                    )
                try:
                    limit = int(limit_part)
                except ValueError as exc:
                    raise LocalizedProfileError(
                        BizCode.INVALID_PARAMETER,
                        "analytics.error.invalid_per_type_limits",
                        detail_key="analytics.error.per_type_limit_not_integer",
                        detail_params={"node_type": node_type, "limit": limit_part},
                    ) from exc
                if limit < 0:
                    raise LocalizedProfileError(
                        BizCode.INVALID_PARAMETER,
                        "analytics.error.invalid_per_type_limits",
                        detail_key="analytics.error.per_type_limit_negative",
                        detail_params={"node_type": node_type, "limit": limit_part},
                    )
                if node_type not in SUPPORTED_NODE_TYPES:
                    logger.warning(
                        "per_type_limits 包含未支持的 Node_Type，已忽略: %s", node_type
                    )
                    continue
                if limit > SINGLE_TYPE_LIMIT_HARD_MAX:
                    logger.warning(
                        "per_type_limits 中 %s 的 limit=%d 超过单值上限 %d，已截断",
                        node_type,
                        limit,
                        SINGLE_TYPE_LIMIT_HARD_MAX,
                    )
                    limit = SINGLE_TYPE_LIMIT_HARD_MAX
                result[node_type] = limit
        except (TypeError, ValueError) as exc:
            raise LocalizedProfileError(
                BizCode.INVALID_PARAMETER,
                "analytics.error.invalid_per_type_limits",
                detail=str(exc),
            ) from exc
        return result

    async def memory_insight(
        self, context: MemoryPrincipal, end_user_id: str, language: str
    ) -> ApiResponse[dict[str, Any]]:
        data = _localize_message(
            await self.service.memory_insight(end_user_id, context.workspace_id), language
        )
        return ObjectApiResponse(
            data=data,
            msg=message(
                "analytics.msg.query_success"
                if data.get("is_cached")
                else "analytics.msg.data_not_ready",
                language,
            ),
        )

    async def user_summary(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        language: str,
    ) -> ApiResponse[dict[str, Any]]:
        data = _localize_message(
            await self.service.user_summary(end_user_id, context.workspace_id), language
        )
        return ObjectApiResponse(
            data=data,
            msg=message(
                "analytics.msg.query_success"
                if data.get("is_cached")
                else "analytics.msg.data_not_ready",
                language,
            ),
        )

    async def node_statistics(
        self, context: MemoryPrincipal, end_user_id: str, language: str
    ) -> ApiResponse[list[dict[str, Any]]]:
        data = await self.service.node_statistics(end_user_id, context.workspace_id)
        return ListApiResponse(data=data, msg=message("analytics.msg.query_success", language))

    async def workspace_statistics(
        self, context: MemoryPrincipal, language: str
    ) -> ApiResponse[dict[str, Any]]:
        data = await self.service.workspace_statistics(context.workspace_id)
        return ObjectApiResponse(data=data, msg=message("analytics.msg.query_success", language))

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
        language: str,
    ) -> ApiResponse[dict[str, Any]]:
        types = (
            [item.strip() for item in node_types.split(",") if item.strip()] if node_types else None
        )
        data = _localize_message(
            await self.service.graph_data(
                end_user_id,
                context.workspace_id,
                types,
                limit,
                depth,
                center_node_id,
                self.parse_per_type_limits(per_type_limits),
                language,
            ),
            language,
        )
        return ObjectApiResponse(
            data=data,
            msg=data.get("message", message("analytics.msg.query_success", language)),
        )

    async def community_graph(
        self, context: MemoryPrincipal, end_user_id: str, language: str
    ) -> ApiResponse[dict[str, Any]]:
        data = _localize_message(
            await self.service.community_graph(end_user_id, context.workspace_id),
            language,
        )
        return ObjectApiResponse(
            data=data,
            msg=data.get("message", message("analytics.msg.query_success", language)),
        )

    async def hot_memory_tags(
        self, context: MemoryPrincipal, limit: int, language: str
    ) -> ApiResponse[list[dict[str, Any]]]:
        data = await self.service.workspace_hot_memory_tags(context.workspace_id, limit)
        return ListApiResponse(data=data, msg=message("analytics.msg.query_success", language))

    async def user_profile(
        self, context: MemoryPrincipal, end_user_id: str | None, language: str
    ) -> ApiResponse[dict[str, Any]]:
        data = await self.service.user_profile(end_user_id, context.workspace_id)
        return ObjectApiResponse(data=data, msg=message("analytics.msg.query_success", language))

    async def interest_distribution(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        limit: int,
        language: str,
    ) -> ApiResponse[list[dict[str, Any]]]:
        data = await self.service.interest_distribution(
            end_user_id, context.workspace_id, limit, language
        )
        return ListApiResponse(
            data=data,
            msg=message("analytics.msg.interest_distribution_success", language),
        )

    async def generate_cache(
        self,
        context: MemoryPrincipal,
        end_user_id: str | None,
        language: str,
    ) -> ApiResponse[dict[str, Any]]:
        data = await self.service.generate_cache(
            context.workspace_id, end_user_id, language
        )
        if not end_user_id:
            return ObjectApiResponse(
                data=data,
                msg=message("analytics.msg.generate_batch_completed", language),
            )
        if data["insight_success"] and data["summary_success"]:
            return ObjectApiResponse(
                data=data, msg=message("analytics.msg.generate_completed", language)
            )
        if data["insight_success"] or data["summary_success"]:
            return ObjectApiResponse(
                data=data, msg=message("analytics.msg.generate_partial", language)
            )
        error_msg = (
            "; ".join(
                f"{item.get('type')}: {item.get('error')}" for item in data["errors"]
            )
            or "unknown error"
        )
        # The legacy contract answers business failures with HTTP 200 and a
        # non-zero code; the API's ``fail()`` never changed the status code.
        raise LocalizedProfileError(
            BizCode.INTERNAL_ERROR,
            "analytics.generate.failed",
            detail=error_msg,
            data=data,
            status_code=200,
        )

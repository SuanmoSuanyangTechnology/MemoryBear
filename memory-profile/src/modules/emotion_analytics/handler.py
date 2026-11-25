"""HTTP adaptation for emotion reads. Generation is not executed in profile."""

from __future__ import annotations

import logging
from datetime import date

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse, legacy_fail_response
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime
from src.services.emotion_analytics import EmotionAnalyticsService
from src.services.operations import OperationService

logger = logging.getLogger(__name__)


class EmotionAnalyticsHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.operations = OperationService(runtime)
        self.service = EmotionAnalyticsService(runtime)

    async def read(self, action: str, context: MemoryPrincipal, body, language: str):
        try:
            if action == "tags":
                data = await self.service.tags(
                    context.workspace_id,
                    body.end_user_id,
                    body.start_date,
                    body.end_date,
                    body.limit,
                    language,
                )
            elif action == "wordcloud":
                data = await self.service.wordcloud(
                    context.workspace_id, body.end_user_id, body.limit
                )
            elif action == "health":
                data = await self.service.health(
                    context.workspace_id, body.end_user_id, body.time_range
                )
            else:
                data = await self.service.suggestions(context.workspace_id, body.end_user_id)
                if data is None:
                    return ApiResponse(
                        data={"exists": False}, msg="情绪建议数据不存在，请点击右上角刷新进行初始化"
                    )
            return ApiResponse(
                data=data,
                msg={
                    "tags": "情绪标签获取成功",
                    "wordcloud": "情绪词云获取成功",
                    "health": "情绪健康指数获取成功",
                    "suggestions": "个性化建议获取成功",
                }[action],
            )
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)
        except Exception:
            logger.exception("emotion %s failed", action)
            return legacy_fail_response(BizCode.INTERNAL_ERROR, "情绪分析查询失败")

    async def daily(
        self,
        context: MemoryPrincipal,
        end_user_id: str,
        timezone: str | None,
        language: str,
        *,
        overview: bool,
        page: int = 1,
        pagesize: int = 5,
        sort: str = "asc",
        start: date | None = None,
        end: date | None = None,
    ):
        try:
            data = await self.service.daily(
                context.workspace_id,
                end_user_id,
                timezone,
                language,
                overview=overview,
                page=page,
                pagesize=pagesize,
                sort=sort,
                start=start,
                end=end,
            )
            return ApiResponse(
                data=data, msg="情绪数据概览获取成功" if overview else "情绪时间轴获取成功"
            )
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)
        except Exception:
            logger.exception("emotion daily query failed")
            return legacy_fail_response(BizCode.INTERNAL_ERROR, "情绪数据查询失败")

    async def generate_suggestions(self, context, body, language: str):
        try:
            data = await self.operations.execute(
                "emotion_suggestions", context, language, end_user_id=body.end_user_id
            )
            return ApiResponse(data=data, msg="个性化建议生成成功")
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)

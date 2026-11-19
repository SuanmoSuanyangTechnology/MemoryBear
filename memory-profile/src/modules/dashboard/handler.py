"""Dashboard transport adaptation; business logic lives in DashboardService."""

from __future__ import annotations

import logging

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse, legacy_fail_response
from src.runtime import ProfileRuntime
from src.services.dashboard import DashboardService
from src.services.operations import OperationService

logger = logging.getLogger(__name__)


class DashboardHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.operations = OperationService(runtime)
        self.service = DashboardService(runtime)

    async def dispatch(self, action: str, *args):
        try:
            data = await getattr(self.service, action)(*args)
            return ApiResponse(
                data=data,
                msg={
                    "end_users": "宿主列表获取成功",
                    "dashboard_data": "Dashboard数据获取成功",
                    "total_memory": "记忆总量获取成功",
                    "rag_content": "宿主RAG记忆消息获取成功",
                    "summary": "获取成功",
                    "insight": "获取成功",
                }.get(action, "查询成功"),
            )
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)
        except Exception:
            logger.exception("dashboard %s failed", action)
            return legacy_fail_response(BizCode.INTERNAL_ERROR, "Dashboard查询失败")

    async def generate_rag_profile(self, context, body, language: str):
        try:
            data = await self.operations.execute(
                "rag_profile",
                context,
                language,
                end_user_id=body.end_user_id,
                limit=body.limit,
                max_tags=body.max_tags,
            )
            return ApiResponse(data=data, msg="RAG画像生产完成")
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)

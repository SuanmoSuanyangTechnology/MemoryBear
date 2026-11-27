"""Transport-only adapters for forgetting reads and cache invalidation."""

from __future__ import annotations

import logging

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse, legacy_fail_response
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime
from src.services.forgetting import ForgettingService
from src.services.operations import OperationService

logger = logging.getLogger(__name__)


class ForgettingHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.operations = OperationService(runtime)
        self.service = ForgettingService(runtime)

    async def dispatch(self, action: str, context: MemoryPrincipal, end_user_id: str, *args):
        try:
            data = await getattr(self.service, action)(context, end_user_id, *args)
            if action == "refresh":
                return ApiResponse(data={"refreshed": True}, msg="缓存已清除")
            return ApiResponse(data=data)
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)
        except Exception:
            logger.exception("forgetting %s failed", action)
            return legacy_fail_response(BizCode.INTERNAL_ERROR, "遗忘数据查询失败")

    async def trigger(self, context, body, language: str):
        try:
            data = await self.operations.execute(
                "forgetting",
                context,
                language,
                end_user_id=body.end_user_id,
                max_merge_batch_size=body.max_merge_batch_size,
                min_days_since_access=body.min_days_since_access,
            )
            return ApiResponse(data=data, msg="遗忘周期执行成功")
        except ProfileError as exc:
            return legacy_fail_response(exc.code, exc.message, exc.detail)

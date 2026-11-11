"""Build value-ranking responses using the existing profile envelope."""

import logging
from collections.abc import Awaitable

from src.common.errors import BizCode, ProfileError
from src.common.responses import ApiResponse
from src.middleware.auth import MemoryPrincipal
from src.runtime import ProfileRuntime
from src.schemas.value_ranking import (
    PermanentMemoryList,
    PermanentMemoryQuota,
    PermanentMemoryUnmarkResult,
)
from src.services.value_ranking import ValueRankingService

logger = logging.getLogger(__name__)


async def _respond[T](operation: Awaitable[T], msg: str) -> ApiResponse[T]:
    try:
        data = await operation
    except ProfileError:
        raise
    except Exception as exc:
        logger.exception("Unexpected permanent-memory API failure")
        raise ProfileError(
            BizCode.INTERNAL_ERROR, "永久记忆操作失败", detail="internal error", status_code=500
        ) from exc
    return ApiResponse[T](data=data, msg=msg)


class ValueRankingHandler:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.service = ValueRankingService(runtime)

    async def get_quota(
        self, context: MemoryPrincipal, end_user_id: str
    ) -> ApiResponse[PermanentMemoryQuota]:
        return await _respond(
            self.service.get_quota(end_user_id, context.workspace_id), "查询成功"
        )

    async def list_memories(
        self, context: MemoryPrincipal, end_user_id: str, page: int, pagesize: int
    ) -> ApiResponse[PermanentMemoryList]:
        return await _respond(
            self.service.list_memories(end_user_id, context.workspace_id, page, pagesize),
            "查询成功",
        )

    async def unmark(
        self, context: MemoryPrincipal, node_id: str, end_user_id: str
    ) -> ApiResponse[PermanentMemoryUnmarkResult]:
        return await _respond(
            self.service.unmark(node_id, end_user_id, context.workspace_id),
            "已取消永久记忆标识",
        )

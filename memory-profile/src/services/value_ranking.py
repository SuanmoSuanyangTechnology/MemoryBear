"""Manage permanent-memory quotas, ranked lists and unmarking."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from numbers import Number
from uuid import UUID

from src.common.errors import BizCode, ProfileError
from src.common.normalization import parse_uuid
from src.common.pagination import PageMeta
from src.common.redis_cache import invalidate_cache, redis_cache
from src.infrastructure.value_ranking_mutation import ValueRankingMutation
from src.repositories.value_ranking import ValueRankingRepository
from src.runtime import ProfileRuntime
from src.schemas.value_ranking import (
    PermanentMemoryList,
    PermanentMemoryQuota,
    PermanentMemoryUnmarkResult,
)

logger = logging.getLogger(__name__)


def _not_found() -> ProfileError:
    return ProfileError(
        BizCode.NOT_FOUND, "终端用户或记忆不存在", detail="resource not found", status_code=404
    )


def _unavailable(detail: str) -> ProfileError:
    return ProfileError(
        BizCode.SERVICE_UNAVAILABLE, "永久记忆服务暂时不可用", detail=detail, status_code=503
    )


@contextmanager
def _memory_operation(detail: str) -> Iterator[None]:
    try:
        yield
    except ProfileError:
        raise
    except Exception as exc:
        logger.exception(detail)
        raise _unavailable(detail) from exc


def _quota(total_memory_limit: int, used: int) -> PermanentMemoryQuota:
    permanent_limit = int(total_memory_limit * 0.10)
    return PermanentMemoryQuota(
        total_memory_limit=total_memory_limit,
        permanent_limit=permanent_limit,
        used=used,
        remaining=max(permanent_limit - used, 0),
    )


class ValueRankingService:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.repository = ValueRankingRepository(runtime.postgres, runtime.neo4j)
        self.mutation = ValueRankingMutation(runtime.postgres, runtime.neo4j)
        self.entitlements = runtime.quota

    async def _resolve_user(self, end_user_id: str, workspace_id: UUID) -> tuple[str, UUID]:
        user_id = parse_uuid(end_user_id)
        if user_id is None:
            raise _not_found()
        tenant_id = await self.repository.tenant_for_active_user(user_id, workspace_id)
        if tenant_id is None:
            raise _not_found()
        return str(user_id), tenant_id

    @redis_cache(ttl=300, prefix="quota", id_arg="tenant_id", skip_args=["self"])
    async def _total_memory_limit(self, tenant_id: UUID) -> int:
        limits = await self.entitlements.effective_limits(tenant_id, fallback_on_error=False)
        value = limits.get("end_user_memory_limit")
        if isinstance(value, bool) or not isinstance(value, Number) or value <= 0:
            raise _unavailable("end-user memory quota unavailable")
        normalized = int(value)
        if normalized <= 0:
            raise _unavailable("end-user memory quota unavailable")
        return normalized

    async def get_quota(self, end_user_id: str, workspace_id: UUID) -> PermanentMemoryQuota:
        user_id, tenant_id = await self._resolve_user(end_user_id, workspace_id)
        with _memory_operation("failed to load permanent-memory quota"):
            limit = await self._total_memory_limit(tenant_id)
            used = await self.repository.count_permanent(user_id)
            return _quota(limit, used)

    async def list_memories(
        self, end_user_id: str, workspace_id: UUID, page: int, pagesize: int
    ) -> PermanentMemoryList:
        user_id, tenant_id = await self._resolve_user(end_user_id, workspace_id)
        return await self._list_memories(user_id, tenant_id, page, pagesize)

    @redis_cache(
        ttl=600,
        prefix="permanent_memories",
        id_arg="end_user_id",
        skip_args=["self"],
        return_type=PermanentMemoryList,
    )
    async def _list_memories(
        self, end_user_id: str, tenant_id: UUID, page: int, pagesize: int
    ) -> PermanentMemoryList:
        with _memory_operation("failed to list permanent memories"):
            limit = await self._total_memory_limit(tenant_id)
            used = await self.repository.count_permanent(end_user_id)
            total = await self.repository.count_live(end_user_id)
            items = await self.repository.list_memories(end_user_id, page, pagesize)
            return PermanentMemoryList(
                page=PageMeta.create(page=page, pagesize=pagesize, total=total),
                quota=_quota(limit, used),
                items=items,
            )

    async def unmark(
        self, element_id: str, end_user_id: str, workspace_id: UUID
    ) -> PermanentMemoryUnmarkResult:
        user_id, tenant_id = await self._resolve_user(end_user_id, workspace_id)
        with _memory_operation("failed to unmark permanent memory"):
            limit = await self._total_memory_limit(tenant_id)
            if not await self.mutation.unmark(element_id, user_id):
                raise _not_found()
            try:
                used = await self.repository.count_permanent(user_id)
            finally:
                for prefix in ("forget_candidates", "permanent_memories"):
                    try:
                        await invalidate_cache(prefix=f"{prefix}:{user_id}")
                    except Exception:
                        logger.exception("Failed to invalidate %s for end user %s", prefix, user_id)
            return PermanentMemoryUnmarkResult(id=element_id, quota=_quota(limit, used))

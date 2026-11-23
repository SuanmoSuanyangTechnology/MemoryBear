"""Forgetting previews, quota and history; no graph or database mutations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from src.common.errors import BizCode, ProfileError
from src.common.normalization import datetime_to_epoch_ms
from src.common.redis_cache import invalidate_cache, redis_cache
from src.middleware.auth import MemoryPrincipal
from src.repositories.forgetting import ForgettingRepository
from src.runtime import ProfileRuntime
from src.schemas.forgetting import (
    ForgettingCandidatesData,
    ForgettingQuotaData,
    ForgettingTrendItem,
    ForgottenLogsData,
)
from src.services.end_user import EndUserService


class ForgettingService:
    def __init__(self, runtime: ProfileRuntime) -> None:
        self.repo = ForgettingRepository(runtime.postgres, runtime.neo4j)
        self.users = EndUserService.from_postgres(runtime.postgres)
        self.entitlements = runtime.quota

    async def _user(self, workspace_id: UUID, value: str) -> UUID:
        try:
            user_id = UUID(value)
        except ValueError as exc:
            raise ProfileError(
                BizCode.INVALID_PARAMETER, "end_user_id 无效", detail="invalid UUID"
            ) from exc
        if not await self.users.exists_in_workspace(user_id, workspace_id):
            raise ProfileError(BizCode.USER_NOT_FOUND, "终端用户不存在或不可访问")
        return user_id

    async def _policy(self, workspace_id: UUID, tenant_id: UUID) -> tuple[int, float]:
        limits = await self.entitlements.effective_limits(tenant_id)
        # Preserve the monolith's free-plan fallback for a zero/missing limit.
        limit = int(limits.get("end_user_memory_limit") or 300)
        return limit, await self.repo.lambda_mem(workspace_id)

    @redis_cache(ttl=60, prefix="quota_breakdown", id_arg="end_user_id", skip_args=["self"])
    async def _breakdown(self, end_user_id: UUID) -> dict[str, int]:
        return await self.repo.quota_breakdown(end_user_id)

    async def quota(self, context: MemoryPrincipal, value: str) -> ForgettingQuotaData:
        user_id = await self._user(context.workspace_id, value)
        breakdown = await self._breakdown(end_user_id=user_id)
        limit, fraction = await self._policy(context.workspace_id, context.tenant_id)
        auxiliary = {key: breakdown[key] for key in ("MemorySummary", "Dialogue")}
        return ForgettingQuotaData(
            memory_limit=limit,
            trigger_count=limit,
            target_count=max(int(limit * (1 - fraction)), 50),
            breakdown=breakdown,
            active_count=sum(breakdown[key] for key in ("Statement", "Chunk", "ExtractedEntity")),
            auxiliary_active_count=sum(auxiliary.values()),
            auxiliary_breakdown=auxiliary,
            forgotten_count=await self.repo.forgotten_count(user_id),
        )

    async def trend(
        self, context: MemoryPrincipal, value: str, days: int
    ) -> list[ForgettingTrendItem]:
        user_id = await self._user(context.workspace_id, value)
        now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        rows = await self.repo.daily_trend(user_id, (now - timedelta(days=days)).date())
        return [
            ForgettingTrendItem(
                date=int((now - timedelta(days=offset)).timestamp() * 1000),
                count=rows.get((now - timedelta(days=offset)).date().isoformat(), 0),
            )
            for offset in range(days - 1, -1, -1)
        ]

    @redis_cache(ttl=300, prefix="forget_candidates", id_arg="end_user_id", skip_args=["self"])
    async def _candidates(
        self, end_user_id: UUID, memory_limit: int, fraction: float
    ) -> list[dict[str, Any]]:
        counts = await self.repo.quota_breakdown(end_user_id)
        active = sum(counts[key] for key in ("Statement", "Chunk", "ExtractedEntity"))
        if active <= memory_limit:
            return []
        budget = active - max(int(memory_limit * (1 - fraction)), 50)
        if budget <= 0:
            return []
        evaluated = int(datetime.now(UTC).timestamp() * 1000)
        isolated = await self.repo.core_candidates(
            end_user_id, budget, evaluated, isolated_only=True
        )
        remaining = max(0, budget - len(isolated))
        auxiliary_budget = min(counts["MemorySummary"] + counts["Dialogue"], remaining)
        auxiliary = (
            await self.repo.auxiliary_candidates(end_user_id, auxiliary_budget)
            if auxiliary_budget
            else []
        )
        fallback = (
            await self.repo.core_candidates(end_user_id, remaining, evaluated, isolated_only=False)
            if remaining and not auxiliary
            else []
        )
        core = [
            {
                "pool": "core",
                "node_type": item["node_type"],
                "created_at": int(item["created_epoch"]),
                "content": item.get("content") or "",
                "forgetting_activation": item.get("forgetting_activation"),
            }
            for item in (isolated + fallback)[:budget]
        ]
        return core + [
            {
                "pool": "auxiliary",
                "node_type": item["node_type"],
                "created_at": int(item["created_epoch"]),
                "content": item.get("content") or "",
            }
            for item in auxiliary[:auxiliary_budget]
        ]

    async def candidates(
        self, context: MemoryPrincipal, value: str, page: int, pagesize: int
    ) -> ForgettingCandidatesData:
        user_id = await self._user(context.workspace_id, value)
        limit, fraction = await self._policy(context.workspace_id, context.tenant_id)
        items = await self._candidates(end_user_id=user_id, memory_limit=limit, fraction=fraction)
        total = len(items)
        return ForgettingCandidatesData(
            items=items[(page - 1) * pagesize : page * pagesize],
            page={
                "page": page,
                "pagesize": pagesize,
                "total": total,
                "hasnext": page * pagesize < total,
            },
        )

    async def logs(
        self, context: MemoryPrincipal, value: str, page: int, pagesize: int
    ) -> ForgottenLogsData:
        user_id = await self._user(context.workspace_id, value)
        items, total = await self.repo.forgotten_logs(user_id, page, pagesize)
        for item in items:
            item["delete_at"] = datetime_to_epoch_ms(item["delete_at"])
        return ForgottenLogsData(
            items=items,
            page={
                "page": page,
                "pagesize": pagesize,
                "total": total,
                "hasnext": page * pagesize < total,
            },
        )

    async def refresh(self, context: MemoryPrincipal, value: str) -> None:
        user_id = await self._user(context.workspace_id, value)
        for prefix in (f"quota_breakdown:{user_id}", f"forget_candidates:{user_id}"):
            await invalidate_cache(prefix=prefix)

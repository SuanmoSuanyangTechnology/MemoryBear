"""Workspace statistics snapshot read and refresh orchestration."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from src.common.errors import BizCode, LocalizedProfileError
from src.repositories.graph import MemoryGraphRepository
from src.repositories.profile import MemoryStatisticsRepository
from src.repositories.search import MemorySearchRepository
from src.repositories.workspace_statistics import (
    WorkspaceStatisticsCorruptError,
    WorkspaceStatisticsRefreshLockLostError,
    WorkspaceStatisticsRepository,
    WorkspaceStatisticsUnavailableError,
)
from src.runtime import ProfileRuntime
from src.schemas.workspace import WorkspaceStatistics
from src.services._helpers import short_term_count
from src.services.profile import MEMORY_TYPES

logger = logging.getLogger(__name__)

#: 与写侧（api ``workspace_memory_statistics_service``）逐值一致：批量大小、
#: 等待/轮询预算、25 小时新鲜度与隐性记忆的最小摘要数。
_WORKSPACE_STATISTICS_BATCH_SIZE = 1000
_WORKSPACE_STATISTICS_REFRESH_POLL_INTERVAL_SECONDS = 0.5
_WORKSPACE_STATISTICS_REFRESH_WAIT_TIMEOUT_SECONDS = 30.0
_WORKSPACE_STATISTICS_REFRESH_LOCK_RENEW_INTERVAL_SECONDS = 30
_TODAY_SNAPSHOT_MAX_AGE = timedelta(hours=25)


class WorkspaceStatisticsRefreshTimeoutError(RuntimeError):
    """请求等待预算内没有等到可用快照。"""

    def __init__(self, workspace_id: UUID) -> None:
        super().__init__(
            f"workspace statistics refresh timed out: workspace_id={workspace_id}"
        )


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _snapshot_age(snapshot: dict[str, Any], now: datetime) -> timedelta:
    """快照年龄：同时接受毫秒整数与旧版 ISO 8601 字符串。"""

    generated_at = snapshot.get("generated_at")
    if isinstance(generated_at, bool):
        raise ValueError("workspace statistics snapshot has invalid generated_at")
    if isinstance(generated_at, (int, float)):
        seconds = generated_at / 1000 if generated_at > 1e10 else generated_at
        generated_at_datetime = datetime.fromtimestamp(seconds, UTC).replace(tzinfo=None)
    elif isinstance(generated_at, str) and generated_at:
        parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        generated_at_datetime = (
            parsed.replace(tzinfo=None)
            if parsed.tzinfo is None
            else parsed.astimezone(UTC).replace(tzinfo=None)
        )
    else:
        raise ValueError("workspace statistics snapshot has no generated_at")
    return now - generated_at_datetime


def _snapshot_version(snapshots: list[dict[str, Any]]) -> int | str | None:
    if not snapshots:
        return None
    generated_at = snapshots[0].get("generated_at")
    if isinstance(generated_at, bool):
        raise ValueError("workspace statistics snapshot has invalid generated_at")
    if isinstance(generated_at, int):
        return generated_at
    if isinstance(generated_at, str) and generated_at:
        # 旧 ISO 快照在 72h TTL 排空前仍参与版本比较。
        return generated_at
    raise ValueError("workspace statistics snapshot has no generated_at")


def _zero_statistics_snapshot() -> dict[str, Any]:
    return {
        "statistics": [
            {"type": memory_type, "count": 0, "percentage": 0.0}
            for memory_type in MEMORY_TYPES
        ],
        "total_count": 0,
        "total_users": 0,
        "generated_at": None,
    }


def _fresh_cached_response(
    snapshots: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """恰好两份且最新一份不超过 25 小时时可直接复用。"""

    if len(snapshots) != 2:
        return None
    today, yesterday = snapshots
    if _snapshot_age(today, _utcnow_naive()) > _TODAY_SNAPSHOT_MAX_AGE:
        return None
    return {**today, "yesterday": yesterday}


def _response_from_completed_refresh(
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    """本轮刷新完成后的结果：没有前一份快照时用零值基线。"""

    if not snapshots:
        raise ValueError("workspace statistics refresh produced no snapshot")
    yesterday = snapshots[1] if len(snapshots) > 1 else _zero_statistics_snapshot()
    return {**snapshots[0], "yesterday": yesterday}


class StatisticsService:
    """工作空间统计快照的读取、刷新与租约协调。"""

    def __init__(self, runtime: ProfileRuntime) -> None:
        self.workspace_statistics_repository = WorkspaceStatisticsRepository(
            runtime.redis
        )
        self.memory_statistics = MemoryStatisticsRepository(runtime.postgres)
        self.memory_search = MemorySearchRepository(runtime.elasticsearch)
        self.memory_graph = MemoryGraphRepository(runtime.neo4j)

    async def workspace_statistics(self, workspace_id: UUID) -> WorkspaceStatistics:
        """读取或刷新工作空间统计快照。

        降级档位（设计 §7）：刷新等待超时/租约丢失 → 503；缓存不可用或损坏 → 10001。
        快照缺失/损坏不能用 0 冒充“昨日无数据”，必须显式不可用。
        """

        try:
            response = await self._get_or_refresh_workspace_statistics(workspace_id)
        except (
            WorkspaceStatisticsRefreshTimeoutError,
            WorkspaceStatisticsRefreshLockLostError,
        ) as exc:
            logger.warning(
                "workspace statistics refresh unavailable: workspace_id=%s error=%s",
                workspace_id,
                type(exc).__name__,
                exc_info=True,
            )
            raise LocalizedProfileError(
                BizCode.SERVICE_UNAVAILABLE,
                "analytics.workspace_statistics.not_ready",
                detail_key="analytics.workspace_statistics.not_ready",
                status_code=503,
            ) from exc
        except WorkspaceStatisticsUnavailableError as exc:
            logger.error(
                "workspace statistics cache unavailable: workspace_id=%s error=%s",
                workspace_id,
                type(exc).__name__,
                exc_info=True,
            )
            raise LocalizedProfileError(
                BizCode.INTERNAL_ERROR,
                "analytics.workspace_statistics.unavailable",
                status_code=500,
            ) from exc
        except WorkspaceStatisticsCorruptError as exc:
            logger.error(
                "workspace statistics cache corrupt: workspace_id=%s error=%s",
                workspace_id,
                type(exc).__name__,
                exc_info=True,
            )
            raise LocalizedProfileError(
                BizCode.INTERNAL_ERROR,
                "analytics.workspace_statistics.corrupt",
                status_code=500,
            ) from exc
        return WorkspaceStatistics.from_refreshed(response)

    async def _get_or_refresh_workspace_statistics(
        self, workspace_id: UUID
    ) -> dict[str, Any]:
        """复用 25h 内的两份快照；否则抢租约刷新，抢不到则等待他人完成。"""

        repository = self.workspace_statistics_repository
        snapshots = await repository.workspace_snapshots(workspace_id)
        cached = _fresh_cached_response(snapshots)
        if cached is not None:
            return cached

        initial_version = _snapshot_version(snapshots)
        token = uuid4().hex
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _WORKSPACE_STATISTICS_REFRESH_WAIT_TIMEOUT_SECONDS

        if await repository.acquire_refresh_lock(workspace_id, token):
            return await self._refresh_workspace_statistics_under_lock(
                workspace_id, token, initial_version
            )

        while loop.time() < deadline:
            remaining = deadline - loop.time()
            await asyncio.sleep(
                min(_WORKSPACE_STATISTICS_REFRESH_POLL_INTERVAL_SECONDS, remaining)
            )
            observed = await repository.workspace_snapshots(workspace_id)
            if observed and _snapshot_version(observed) != initial_version:
                return _response_from_completed_refresh(observed)
            if loop.time() >= deadline:
                break
            if await repository.acquire_refresh_lock(workspace_id, token):
                return await self._refresh_workspace_statistics_under_lock(
                    workspace_id, token, initial_version
                )

        final = await repository.workspace_snapshots(workspace_id)
        if final and _snapshot_version(final) != initial_version:
            return _response_from_completed_refresh(final)
        raise WorkspaceStatisticsRefreshTimeoutError(workspace_id)

    async def _refresh_workspace_statistics_under_lock(
        self,
        workspace_id: UUID,
        token: str,
        initial_version: int | str | None,
    ) -> dict[str, Any]:
        repository = self.workspace_statistics_repository
        renewal_task = asyncio.create_task(
            self._renew_workspace_statistics_lock(workspace_id, token)
        )
        try:
            snapshots = await repository.workspace_snapshots(workspace_id)
            if renewal_task.done():
                await renewal_task
            # 等锁期间可能已有别的副本完成刷新，无需重复计算。
            if snapshots and _snapshot_version(snapshots) != initial_version:
                return _response_from_completed_refresh(snapshots)

            statistics = await self._calculate_workspace_statistics_while_lease_is_valid(
                workspace_id, renewal_task
            )
            await repository.add_snapshot(workspace_id, statistics, token)
            refreshed = await repository.workspace_snapshots(workspace_id)
            return _response_from_completed_refresh(refreshed)
        finally:
            renewal_task.cancel()
            with suppress(
                asyncio.CancelledError, WorkspaceStatisticsRefreshLockLostError
            ):
                await renewal_task
            try:
                await repository.release_refresh_lock(workspace_id, token)
            except Exception:
                logger.exception(
                    "workspace statistics refresh lock release failed: workspace_id=%s",
                    workspace_id,
                )

    async def _renew_workspace_statistics_lock(
        self, workspace_id: UUID, token: str
    ) -> None:
        """刷新期间周期性续租；失去租约立即中止本轮计算。"""

        while True:
            await asyncio.sleep(_WORKSPACE_STATISTICS_REFRESH_LOCK_RENEW_INTERVAL_SECONDS)
            try:
                renewed = await self.workspace_statistics_repository.renew_refresh_lock(
                    workspace_id, token
                )
            except Exception as exc:
                logger.exception(
                    "workspace statistics refresh lock renewal failed: workspace_id=%s",
                    workspace_id,
                )
                raise WorkspaceStatisticsRefreshLockLostError(
                    f"workspace statistics refresh lock renewal failed: "
                    f"workspace_id={workspace_id}"
                ) from exc
            if not renewed:
                logger.warning(
                    "workspace statistics refresh lock lost: workspace_id=%s",
                    workspace_id,
                )
                raise WorkspaceStatisticsRefreshLockLostError(
                    f"workspace statistics refresh lock lost: workspace_id={workspace_id}"
                )

    async def _calculate_workspace_statistics_while_lease_is_valid(
        self,
        workspace_id: UUID,
        renewal_task: asyncio.Task[None],
    ) -> dict[str, Any]:
        """计算与续租赛跑：续租先结束说明已失去租约，取消计算并上抛。"""

        calculation_task = asyncio.create_task(
            self._calculate_workspace_statistics(workspace_id)
        )
        done, _ = await asyncio.wait(
            {calculation_task, renewal_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if renewal_task in done:
            calculation_task.cancel()
            with suppress(asyncio.CancelledError):
                await calculation_task
            await renewal_task
            raise WorkspaceStatisticsRefreshLockLostError(
                f"workspace statistics refresh lock stopped: "
                f"workspace_id={workspace_id}"
            )
        return calculation_task.result()

    async def _calculate_workspace_statistics(self, workspace_id: UUID) -> dict[str, Any]:
        """Aggregate active users in batches using PG counts and cached habits."""

        totals = {memory_type: 0 for memory_type in MEMORY_TYPES}
        total_users = 0
        after_id: UUID | None = None
        evaluated_at = datetime.now(UTC)

        while True:
            end_user_ids = await self.memory_statistics.active_end_user_ids_page(
                workspace_id, after_id, _WORKSPACE_STATISTICS_BATCH_SIZE
            )
            if not end_user_ids:
                break
            raw_ids = [str(end_user_id) for end_user_id in end_user_ids]
            postgresql, search_result, implicit_count, short_term = await asyncio.gather(
                self.memory_statistics.counts_for_users(end_user_ids),
                self.memory_search.workspace_memory_counts(raw_ids),
                self.memory_statistics.habit_count_for_users(raw_ids),
                short_term_count(self.memory_search, self.memory_graph, raw_ids, evaluated_at),
            )
            elastic_statistics, present_ids = search_result
            episodic_count = sum(
                item["episodic_count"] for item in elastic_statistics.values()
            )
            explicit_entity_count = sum(
                item["explicit_entity_count"] for item in elastic_statistics.values()
            )
            emotional_count = sum(
                item["emotional_count"] for item in elastic_statistics.values()
            )
            missing_ids = [
                end_user_id
                for end_user_id in raw_ids
                if end_user_id not in present_ids
            ]
            if missing_ids:
                fallback = await self.memory_graph.workspace_fallback_statistics(
                    missing_ids
                )
                episodic_count += fallback["episodic_count"]
                explicit_entity_count += fallback["explicit_entity_count"]
                emotional_count += fallback["emotional_count"]

            totals["PERCEPTUAL_MEMORY"] += postgresql["perceptual"]
            totals["WORKING_MEMORY"] += (
                postgresql["active_conversations"] + postgresql["direct_sources"]
            )
            totals["SHORT_TERM_MEMORY"] += short_term
            totals["EXPLICIT_MEMORY"] += episodic_count + explicit_entity_count
            totals["IMPLICIT_MEMORY"] += implicit_count
            totals["EMOTIONAL_MEMORY"] += emotional_count
            totals["EPISODIC_MEMORY"] += episodic_count
            totals["FORGET_MEMORY"] += postgresql["forgotten"]
            total_users += len(end_user_ids)

            if len(end_user_ids) < _WORKSPACE_STATISTICS_BATCH_SIZE:
                break
            after_id = end_user_ids[-1]

        total_count = sum(totals.values())
        statistics = [
            {
                "type": memory_type,
                "count": totals[memory_type],
                "percentage": (
                    round(totals[memory_type] / total_count * 100, 2)
                    if total_count > 0
                    else 0.0
                ),
            }
            for memory_type in MEMORY_TYPES
        ]
        return {
            "statistics": statistics,
            "total_count": total_count,
            "total_users": total_users,
        }

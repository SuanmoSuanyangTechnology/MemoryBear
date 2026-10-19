"""Workspace statistics snapshots over the shared Memory Redis cache."""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import UUID

from redis.exceptions import RedisError

from src.common.redis_cache import (
    RedisCacheDecodeError,
    RedisCacheUnavailableError,
    zrange_json,
)
from src.infrastructure.redis import RedisManager

#: 快照键保留 72 小时；每次成功写入时续期。刷新租约 120 秒，每 30 秒续租一次，
#: 与写侧（api ``workspace_statistics_cache``）逐值一致，保证两边可互操作。
_SNAPSHOT_TTL_SECONDS = 72 * 60 * 60
_REFRESH_LOCK_TTL_SECONDS = 120
_SNAPSHOT_KEY_PREFIX = "cache:memory:workspace_statistics:v1"
_REFRESH_LOCK_KEY_PREFIX = "cache:memory:workspace_statistics:refresh_lock:v1"

# 释放/续租都要求 token 仍匹配：租约过期后被他人接管时，旧持有者不能误删新锁。
_RELEASE_REFRESH_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

_RENEW_REFRESH_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
end
return 0
"""

# 写快照与「校验租约 → zadd → 只留两份 → 续期」必须原子：否则失去租约的刷新者
# 仍可能把旧计算结果写进 zset。zremrangebyrank(0, -3) 保留分数最高的两个成员。
_WRITE_SNAPSHOT_SCRIPT = """
if redis.call("get", KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call("zadd", KEYS[2], ARGV[2], ARGV[3])
redis.call("zremrangebyrank", KEYS[2], 0, -3)
redis.call("expire", KEYS[2], ARGV[4])
return 1
"""


class WorkspaceStatisticsRepositoryError(RuntimeError):
    """Base error exposed by this repository to the service layer."""


class WorkspaceStatisticsUnavailableError(WorkspaceStatisticsRepositoryError):
    """Redis is unavailable for reading snapshots or coordinating refreshes."""


class WorkspaceStatisticsCorruptError(WorkspaceStatisticsRepositoryError):
    """Snapshot JSON or schema does not match the contract."""


class WorkspaceStatisticsRefreshLockLostError(WorkspaceStatisticsRepositoryError):
    """The refresh lease expired or changed ownership before the snapshot write."""


def _snapshot_key(workspace_id: UUID) -> str:
    return f"{_SNAPSHOT_KEY_PREFIX}:{workspace_id}"


def _refresh_lock_key(workspace_id: UUID) -> str:
    return f"{_REFRESH_LOCK_KEY_PREFIX}:{workspace_id}"


class WorkspaceStatisticsRepository:
    def __init__(self, redis: RedisManager) -> None:
        self.redis = redis

    async def workspace_snapshots(self, workspace_id: UUID) -> list[dict[str, Any]]:
        """Return the two newest snapshots, newest first."""

        key = _snapshot_key(workspace_id)
        try:
            values = await zrange_json(
                key,
                0,
                1,
                desc=True,
                client_provider=self.redis.client,
            )
        except RedisCacheUnavailableError as exc:
            raise WorkspaceStatisticsUnavailableError(
                "workspace statistics cache unavailable"
            ) from exc
        except RedisCacheDecodeError as exc:
            raise WorkspaceStatisticsCorruptError(
                "workspace statistics cache contains invalid JSON"
            ) from exc

        snapshots: list[dict[str, Any]] = []
        for index, value in enumerate(values):
            if not self._is_snapshot(value):
                raise WorkspaceStatisticsCorruptError(
                    f"workspace statistics snapshot schema invalid: index={index}"
                )
            snapshots.append(value)
        return snapshots

    async def acquire_refresh_lock(self, workspace_id: UUID, token: str) -> bool:
        """Try to take the per-workspace refresh lease."""

        client = await self.redis.client()
        try:
            acquired = await client.set(
                _refresh_lock_key(workspace_id),
                token,
                nx=True,
                ex=_REFRESH_LOCK_TTL_SECONDS,
            )
        except RedisError as exc:
            raise WorkspaceStatisticsUnavailableError(
                "workspace statistics refresh lock unavailable"
            ) from exc
        return bool(acquired)

    async def renew_refresh_lock(self, workspace_id: UUID, token: str) -> bool:
        """Extend the lease only while ``token`` still owns it."""

        client = await self.redis.client()
        try:
            renewed = await client.eval(
                _RENEW_REFRESH_LOCK_SCRIPT,
                1,
                _refresh_lock_key(workspace_id),
                token,
                _REFRESH_LOCK_TTL_SECONDS,
            )
        except RedisError as exc:
            raise WorkspaceStatisticsUnavailableError(
                "workspace statistics refresh lock unavailable"
            ) from exc
        return bool(renewed)

    async def release_refresh_lock(self, workspace_id: UUID, token: str) -> bool:
        """Release the lease only while ``token`` still owns it."""

        client = await self.redis.client()
        try:
            released = await client.eval(
                _RELEASE_REFRESH_LOCK_SCRIPT,
                1,
                _refresh_lock_key(workspace_id),
                token,
            )
        except RedisError as exc:
            raise WorkspaceStatisticsUnavailableError(
                "workspace statistics refresh lock unavailable"
            ) from exc
        return bool(released)

    async def add_snapshot(
        self,
        workspace_id: UUID,
        statistics: dict[str, Any],
        refresh_token: str,
    ) -> dict[str, Any]:
        """Atomically append a snapshot while the caller still owns the lease."""

        generated_at = int(time.time() * 1000)
        snapshot = {
            "statistics": statistics["statistics"],
            "total_count": statistics["total_count"],
            "total_users": statistics["total_users"],
            "generated_at": generated_at,
        }
        payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
        client = await self.redis.client()
        try:
            written = await client.eval(
                _WRITE_SNAPSHOT_SCRIPT,
                2,
                _refresh_lock_key(workspace_id),
                _snapshot_key(workspace_id),
                refresh_token,
                generated_at,
                payload,
                _SNAPSHOT_TTL_SECONDS,
            )
        except RedisError as exc:
            raise WorkspaceStatisticsUnavailableError(
                "workspace statistics snapshot write unavailable"
            ) from exc
        if not written:
            raise WorkspaceStatisticsRefreshLockLostError(
                f"workspace statistics refresh lock lost: workspace_id={workspace_id}"
            )
        return snapshot

    @staticmethod
    def _is_snapshot(value: object) -> bool:
        if not isinstance(value, dict):
            return False
        statistics = value.get("statistics")
        if not isinstance(statistics, list) or not all(
            isinstance(item, dict) for item in statistics
        ):
            return False
        for field in ("total_count", "total_users"):
            field_value = value.get(field)
            if isinstance(field_value, bool) or not isinstance(field_value, int):
                return False
        # v0.4.9 之前的快照用 ISO 8601 字符串；在 72h TTL 排空前继续接受。
        generated_at = value.get("generated_at")
        if isinstance(generated_at, bool):
            return False
        if isinstance(generated_at, int):
            return True
        return isinstance(generated_at, str) and bool(generated_at)

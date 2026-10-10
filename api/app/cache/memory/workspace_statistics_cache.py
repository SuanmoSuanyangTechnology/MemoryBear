"""每个 workspace 最近两次成功计算的记忆统计快照。"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.aioRedis import get_thread_safe_redis
from app.core.utils.datetime_utils import to_timestamp_ms, utcnow

WORKSPACE_STATISTICS_SNAPSHOT_TTL_SECONDS = 72 * 60 * 60
WORKSPACE_STATISTICS_REFRESH_LOCK_TTL_SECONDS = 120
WORKSPACE_STATISTICS_REFRESH_LOCK_RENEW_INTERVAL_SECONDS = 30
_WORKSPACE_STATISTICS_KEY_PREFIX = "cache:memory:workspace_statistics:v1"
_WORKSPACE_STATISTICS_REFRESH_LOCK_KEY_PREFIX = (
    "cache:memory:workspace_statistics:refresh_lock:v1"
)

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

_WRITE_SNAPSHOT_SCRIPT = """
if redis.call("get", KEYS[1]) ~= ARGV[1] then
    return 0
end
redis.call("zadd", KEYS[2], ARGV[2], ARGV[3])
redis.call("zremrangebyrank", KEYS[2], 0, -3)
redis.call("expire", KEYS[2], ARGV[4])
return 1
"""


class WorkspaceStatisticsRefreshLockLostError(RuntimeError):
    """写快照前刷新锁已过期或所有权已变化。"""


def _snapshot_key(workspace_id: uuid.UUID) -> str:
    return f"{_WORKSPACE_STATISTICS_KEY_PREFIX}:{workspace_id}"


def _refresh_lock_key(workspace_id: uuid.UUID) -> str:
    return f"{_WORKSPACE_STATISTICS_REFRESH_LOCK_KEY_PREFIX}:{workspace_id}"


async def get_workspace_statistics_snapshots(
    workspace_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """读取该空间按生成时间从新到旧排列的最多两份快照。"""
    values = await get_thread_safe_redis().zrange(
        _snapshot_key(workspace_id), 0, 1, desc=True
    )
    snapshots = [json.loads(value) for value in values]
    if any(not isinstance(snapshot, dict) for snapshot in snapshots):
        raise ValueError("Invalid workspace memory statistics snapshot")
    return snapshots


async def acquire_workspace_statistics_refresh_lock(
    workspace_id: uuid.UUID,
    token: str,
) -> bool:
    """尝试获取同 workspace 的快照刷新租约。"""
    return bool(
        await get_thread_safe_redis().set(
            _refresh_lock_key(workspace_id),
            token,
            nx=True,
            ex=WORKSPACE_STATISTICS_REFRESH_LOCK_TTL_SECONDS,
        )
    )


async def renew_workspace_statistics_refresh_lock(
    workspace_id: uuid.UUID,
    token: str,
) -> bool:
    """仅在 token 仍匹配时续期刷新租约。"""
    renewed = await get_thread_safe_redis().eval(
        _RENEW_REFRESH_LOCK_SCRIPT,
        1,
        _refresh_lock_key(workspace_id),
        token,
        WORKSPACE_STATISTICS_REFRESH_LOCK_TTL_SECONDS,
    )
    return bool(renewed)


async def release_workspace_statistics_refresh_lock(
    workspace_id: uuid.UUID,
    token: str,
) -> bool:
    """仅释放当前 token 持有的刷新租约。"""
    released = await get_thread_safe_redis().eval(
        _RELEASE_REFRESH_LOCK_SCRIPT,
        1,
        _refresh_lock_key(workspace_id),
        token,
    )
    return bool(released)


async def add_workspace_statistics_snapshot(
    workspace_id: uuid.UUID,
    statistics: dict[str, Any],
    refresh_token: str,
) -> dict[str, Any]:
    """持锁原子写入新快照、保留最新两份并将键续期 72 小时。"""
    generated_at = utcnow()
    timestamp_ms = to_timestamp_ms(generated_at)
    if timestamp_ms is None:
        raise RuntimeError("Unable to timestamp workspace statistics snapshot")
    snapshot = {
        "statistics": statistics["statistics"],
        "total_count": statistics["total_count"],
        "total_users": statistics["total_users"],
        "generated_at": timestamp_ms,
    }

    value = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    written = await get_thread_safe_redis().eval(
        _WRITE_SNAPSHOT_SCRIPT,
        2,
        _refresh_lock_key(workspace_id),
        _snapshot_key(workspace_id),
        refresh_token,
        timestamp_ms,
        value,
        WORKSPACE_STATISTICS_SNAPSHOT_TTL_SECONDS,
    )
    if not written:
        raise WorkspaceStatisticsRefreshLockLostError(
            f"Workspace statistics refresh lock lost: workspace_id={workspace_id}"
        )
    return snapshot

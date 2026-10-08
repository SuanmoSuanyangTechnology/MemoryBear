"""重算任务的 Redis 协调层：占位、取消标记、心跳与分页游标。

职责边界刻意划得很窄：**权威状态在 PG**（``memory_reembed_jobs`` 与
``memory_reembed_job_users``），Redis 只放三类东西——

- 协调：派发占位、单用户占位（防并发重复写）、取消标记（让在跑的子任务尽快停）、
  心跳（对账任务靠它判断"还在推进"还是"worker 已死"）；
- 缓存：每个标签的分页游标（丢了最多从该标签第一页重算，幂等无害）。

因此 Redis 故障时任务仍能正确收敛：只是重复做一遍已做过的工作，不会算错、
也不会卡在 running。
"""

from __future__ import annotations

import logging

from app.core.rag.utils.redis_conn import REDIS_CONN

logger = logging.getLogger(__name__)

# 派发占位只护住"谁来做 fan-out"，短 TTL 让 worker 崩溃后能被对账任务重新派发。
JOB_CLAIM_TTL_SECONDS = 30 * 60
# 单用户占位不护全程，只护"一个分页的处理时间"：由子任务每页续期，
# 于是 worker 猝死时它会在几分钟内自然过期，新任务不必等满 2 小时。
END_USER_CLAIM_TTL_SECONDS = 5 * 60
# 心跳：驱动任务派发完、以及每个子任务每页续期。
JOB_HEARTBEAT_TTL_SECONDS = 10 * 60
# 取消标记与分页游标的存活期：要活过单次任务执行（可能数小时）。
STATE_CACHE_TTL_SECONDS = 7 * 24 * 3600

_KEY_PREFIX = "memory_reembed"

_REFRESH_IF_OWNED_SCRIPT = """
local current_value = redis.call('get', KEYS[1])
if current_value and current_value == ARGV[1] then
    redis.call('expire', KEYS[1], ARGV[2])
    return 1
end
return 0
"""


def _key(job_id: str, suffix: str) -> str:
    return f"{_KEY_PREFIX}:{job_id}:{suffix}"


def cursor_key(job_id: str, end_user_id: str, label: str) -> str:
    return _key(job_id, f"cursor:{end_user_id}:{label}")


def claim_key(job_id: str) -> str:
    return _key(job_id, "claim")


def heartbeat_key(job_id: str) -> str:
    return _key(job_id, "heartbeat")


def end_user_claim_key(job_id: str, end_user_id: str) -> str:
    return _key(job_id, f"user:{end_user_id}")


def _client():
    """Return the shared sync Redis client, or ``None`` when unavailable."""
    client = REDIS_CONN.REDIS
    if client is None:
        logger.warning(
            "Redis is unavailable; memory re-embed coordination is degraded"
        )
    return client


def _safe(call, default, description: str):
    client = _client()
    if client is None:
        return default
    try:
        return call(client)
    except Exception as exc:
        logger.warning("memory re-embed %s failed: %s", description, exc)
        return default


def _decode(value) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)


def claim_job(job_id: str, owner_token: str) -> str:
    """Claim the fan-out of a job for one worker.

    :return: the owner token holding the job. Without Redis this returns
        ``owner_token`` so the caller still makes progress.
    """
    client = _client()
    if client is None:
        return owner_token
    try:
        if client.set(
            claim_key(job_id),
            owner_token,
            ex=JOB_CLAIM_TTL_SECONDS,
            nx=True,
        ):
            return owner_token
        existing = _decode(client.get(claim_key(job_id)))
        if existing is not None:
            return existing
        if client.set(
            claim_key(job_id),
            owner_token,
            ex=JOB_CLAIM_TTL_SECONDS,
            nx=True,
        ):
            return owner_token
    except Exception as exc:
        logger.warning("memory re-embed job claim failed: %s", exc)
    return owner_token


def release_job_claim(job_id: str, owner_token: str) -> bool:
    """Release the claim only if ``owner_token`` still holds it."""
    if REDIS_CONN.REDIS is None:
        return False
    try:
        return bool(REDIS_CONN.delete_if_equal(claim_key(job_id), owner_token))
    except Exception as exc:
        logger.warning("memory re-embed job claim release failed: %s", exc)
        return False


def touch_job_heartbeat(job_id: str) -> None:
    """Renew the liveness marker the reconciler uses to detect dead workers."""

    def store(client):
        client.set(heartbeat_key(job_id), "1", ex=JOB_HEARTBEAT_TTL_SECONDS)

    _safe(store, None, "job heartbeat")


def clear_job_heartbeat(job_id: str) -> None:
    """Revoke the liveness marker once nothing will renew it any more.

    A run that ends on a failed page stops renewing, but the marker it leaves
    behind still reads as "a worker is on this job" until the TTL runs out —
    parking a retry-pending job for the whole of
    :data:`JOB_HEARTBEAT_TTL_SECONDS` before the reconciler is allowed to
    re-dispatch it. Revoking it there hands the retry to the next reconcile
    pass instead of the next expiry.

    Only call this when the caller knows no further page will renew the job:
    a still-running end_user writes the marker back on its next page, so
    clearing it never costs more than one redundant dispatch.
    """
    _safe(
        lambda client: client.delete(heartbeat_key(job_id)),
        None,
        "job heartbeat revoke",
    )


def has_job_heartbeat(job_id: str) -> bool | None:
    """Liveness of a job.

    :return: ``None`` when Redis is unreachable, so the caller must not read
        "no heartbeat" as "worker died".
    """
    client = _client()
    if client is None:
        return None
    try:
        return client.get(heartbeat_key(job_id)) is not None
    except Exception as exc:
        logger.warning("memory re-embed heartbeat read failed: %s", exc)
        return None


def claim_end_user(job_id: str, end_user_id: str, owner_token: str) -> bool:
    """Claim one end_user so a resumed job cannot duplicate in-flight work."""
    return bool(
        _safe(
            lambda client: client.set(
                end_user_claim_key(job_id, end_user_id),
                owner_token,
                ex=END_USER_CLAIM_TTL_SECONDS,
                nx=True,
            ),
            # Without Redis, proceed: duplicate work is wasted cost, not
            # corruption, and the job must not stall.
            True,
            "end_user claim",
        )
    )


def refresh_end_user_claim(
    job_id: str,
    end_user_id: str,
    owner_token: str,
) -> bool:
    """Extend our claim by one more page's worth of work.

    Called every page, which is what lets the TTL stay short: a worker that
    dies stops renewing, so the claim frees up in minutes instead of hours.
    """
    client = _client()
    if client is None:
        return True
    try:
        return bool(
            client.eval(
                _REFRESH_IF_OWNED_SCRIPT,
                1,
                end_user_claim_key(job_id, end_user_id),
                owner_token,
                END_USER_CLAIM_TTL_SECONDS,
            )
        )
    except Exception as exc:
        logger.warning("memory re-embed end_user claim refresh failed: %s", exc)
        return False


def release_end_user_claim(
    job_id: str,
    end_user_id: str,
    owner_token: str,
) -> bool:
    if REDIS_CONN.REDIS is None:
        return False
    try:
        return bool(
            REDIS_CONN.delete_if_equal(
                end_user_claim_key(job_id, end_user_id),
                owner_token,
            )
        )
    except Exception as exc:
        logger.warning("memory re-embed end_user claim release failed: %s", exc)
        return False


def set_label_cursor(
    job_id: str,
    end_user_id: str,
    label: str,
    cursor: str,
) -> None:
    key = cursor_key(job_id, end_user_id, label)

    def store(client):
        client.set(key, cursor, ex=STATE_CACHE_TTL_SECONDS)

    _safe(store, None, "label cursor write")


def get_label_cursor(
    job_id: str,
    end_user_id: str,
    label: str,
) -> str | None:
    return _safe(
        lambda client: _decode(client.get(cursor_key(job_id, end_user_id, label))),
        None,
        "label cursor read",
    )


def clear_label_cursors(job_id: str, end_user_id: str) -> None:
    """Drop per-label cursors for one end_user once it is fully rebuilt."""

    def drop(client):
        pattern = _key(job_id, f"cursor:{end_user_id}:*")
        for key in client.scan_iter(match=pattern, count=100):
            client.delete(key)

    _safe(drop, None, "label cursor cleanup")


def clear_end_user_claim(job_id: str, end_user_id: str) -> None:
    """Force-drop one end_user's claim, whoever holds it.

    For a manual retry of a **terminally failed** row. The regular
    :func:`release_end_user_claim` needs the owner token, which the caller does
    not have — the worker that failed (or died) is gone, and its claim would
    otherwise sit for the full TTL and make the retried subtask skip itself as
    ``already_inflight``.

    Only safe because the caller restricts this to terminal rows: a row still
    in flight, or with retry budget left, must never have its claim stolen.
    """

    def drop(client):
        client.delete(end_user_claim_key(job_id, end_user_id))

    _safe(drop, None, "end_user claim force clear")


__all__ = [
    "END_USER_CLAIM_TTL_SECONDS",
    "JOB_CLAIM_TTL_SECONDS",
    "JOB_HEARTBEAT_TTL_SECONDS",
    "STATE_CACHE_TTL_SECONDS",
    "claim_end_user",
    "claim_job",
    "clear_end_user_claim",
    "clear_job_heartbeat",
    "clear_label_cursors",
    "get_label_cursor",
    "has_job_heartbeat",
    "refresh_end_user_claim",
    "release_end_user_claim",
    "release_job_claim",
    "set_label_cursor",
    "touch_job_heartbeat",
]

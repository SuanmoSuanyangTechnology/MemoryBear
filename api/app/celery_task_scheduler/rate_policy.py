import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from app.core.config import settings
from app.core.exceptions import RateLimitException
from app.core.logging_config import get_named_logger
from app.db import get_async_db_context

logger = get_named_logger("task_scheduler.rate_policy")

_LUA_ADMISSION = (Path(__file__).parent / "lua_scripts" / "admission.lua").read_text()


QpsResolver = Callable[[str, str], Awaitable[int | None]]
_resolvers: dict[str, QpsResolver] = {}


def register_qps_resolver(task_name: str, resolver: QpsResolver) -> None:
    _resolvers[task_name] = resolver


async def _resolve_qps(task_name: str, end_user_id: str) -> int | None:
    resolver = _resolvers.get(task_name)
    if resolver is None:
        return None
    try:
        return await resolver(task_name, end_user_id)
    except Exception:
        logger.warning(
            "_resolve_qps: resolver failed for task=%s user=%s, falling back",
            task_name, end_user_id, exc_info=True,
        )
        return None


async def _write_message_qps_resolver(_task_name: str, end_user_id: str) -> int | None:
    if not end_user_id:
        return None
    from uuid import UUID

    from app.core.quota_manager import get_pre_user_memory_write_ops_limit
    from app.repositories.end_user_repository import get_tenant_id_by_end_user_id_async

    async with get_async_db_context() as db:
        tenant_id = await get_tenant_id_by_end_user_id_async(db, UUID(end_user_id))
        if not tenant_id:
            logger.warning("write_message_qps_resolver: end_user not found id=%s", end_user_id)
            return None
        return await get_pre_user_memory_write_ops_limit(db, tenant_id)


register_qps_resolver("app.core.memory.agent.write_message", _write_message_qps_resolver)


RATE_LIMIT_PREFIX = "scheduler:rl:"   # sliding-window key prefix
RATE_WINDOW_MS = 1000                  # sliding-window size in ms

DEFAULT_MAX_QUEUE_LEN = settings.SCHEDULER_MAX_QUEUE_LEN
DEFAULT_QPS = 0

RATE_POLICY = {}


def _resolve_policy(task_name, qps_override=None):
    """Return (max_queue_len, qps) for a task type. 0 = unlimited."""
    policy = RATE_POLICY.get(task_name, {})
    max_len = int(policy.get("max_queue_len", DEFAULT_MAX_QUEUE_LEN) or 0)

    if qps_override is not None:
        qps = float(qps_override)
    else:
        qps = float(policy.get("qps", DEFAULT_QPS) or 0)

    return max_len, qps


@dataclass
class AdmissionCtx:
    """Bundles task routing keys and payload for enforce_admission."""
    task_name: str
    user_id: str
    msg: str
    msg_id: str
    unit_key: str
    queue_key: str
    active_units_key: str
    ready_set_key: str
    tracker_key: str
    tracker_val: str


async def enforce_admission(redis_client, *, ctx: AdmissionCtx):
    """Atomically admit or reject a task (queue-length + per-user QPS)."""
    qps_override = await _resolve_qps(ctx.task_name, ctx.user_id)
    max_len, qps = _resolve_policy(ctx.task_name, qps_override=qps_override)
    now_ms = int(time.time() * 1000)
    rl_key = f"{RATE_LIMIT_PREFIX}{ctx.unit_key}"

    result = redis_client.eval(
        _LUA_ADMISSION, 6,
        ctx.queue_key, rl_key, ctx.active_units_key, ctx.ready_set_key, ctx.unit_key, ctx.tracker_key,
        str(max_len), str(qps), str(now_ms), str(RATE_WINDOW_MS),
        ctx.msg, ctx.unit_key, ctx.tracker_val, ctx.msg_id,
    )

    if result == -1:
        logger.warning("Queue full, rejected: unit=%s max_queue_len=%d", ctx.unit_key, max_len)
        raise RateLimitException(
            message=f"任务队列已满，请稍后再试 (task={ctx.task_name})",
            rate_headers={
                "Retry-After": "1",
                "X-RateLimit-Reason": "queue_full",
                "X-RateLimit-Queue-Limit": str(max_len),
            },
            context={
                "reason": "queue_full",
                "task_name": ctx.task_name,
                "user_id": ctx.user_id,
                "max_queue_len": max_len,
            },
        )
    if result == -2:
        logger.warning("QPS limited, rejected: unit=%s qps=%s", ctx.unit_key, qps)
        raise RateLimitException(
            message=f"请求过于频繁，请稍后再试 (task={ctx.task_name})",
            rate_headers={
                "Retry-After": "1",
                "X-RateLimit-Reason": "qps",
                "X-RateLimit-QPS-Limit": str(qps),
            },
            context={
                "reason": "qps",
                "task_name": ctx.task_name,
                "user_id": ctx.user_id,
                "qps": qps,
            },
        )

    return True

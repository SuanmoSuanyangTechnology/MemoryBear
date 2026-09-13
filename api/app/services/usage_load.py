"""渠道 least-used 负载派生（spec §11.1 / D14）：model_usage_records 滚动窗口聚合 + 进程内缓存。

- 聚合键 = channel_id、窗口 = MODEL_USAGE_LOAD_WINDOW_MINUTES：渠道行不落计数器（D14），
  least-used 选路状态由计量表滚动窗口派生（`ix_usage_channel_time` 支撑；成功与失败事件
  都计入——持续失败的渠道同样应被降权）
- 进程内缓存 TTL 60s（与渠道快照缓存同口径）：单租户至多一次聚合查询，resolve 热路径零库压
- 失败静默降级 `{}`（debug 日志）：选路退化 `created_at asc`，绝不阻塞业务调用（旁路铁律）
- 开关 MODEL_USAGE_LEAST_USED_ENABLED=false 短路返回 `{}`（不查库）
- sync/async 双读（GC#11）：管理面/worker 用 sync 版，异步链路用 async 版，各自匹配会话类型
"""
from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging_config import get_logger
from app.models.model_usage_record import ModelUsageRecord

logger = get_logger(__name__)

_CACHE_TTL_SECONDS = 60.0
_cache: dict[uuid.UUID, tuple[float, dict[uuid.UUID, int]]] = {}


def _window_start() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(
        minutes=settings.MODEL_USAGE_LOAD_WINDOW_MINUTES
    )


def _cached(tenant_id: uuid.UUID, now: float) -> dict[uuid.UUID, int] | None:
    entry = _cache.get(tenant_id)
    if entry is None or now - entry[0] >= _CACHE_TTL_SECONDS:
        return None
    return entry[1]


def _rows_to_loads(rows) -> dict[uuid.UUID, int]:
    return {channel_id: int(count) for channel_id, count in rows if channel_id is not None}


def _aggregate_sync(db: Session, tenant_id: uuid.UUID, since: datetime) -> dict[uuid.UUID, int]:
    stmt = (
        select(ModelUsageRecord.channel_id, func.count())
        .where(
            ModelUsageRecord.tenant_id == tenant_id,
            ModelUsageRecord.channel_id.isnot(None),
            ModelUsageRecord.created_at >= since,
        )
        .group_by(ModelUsageRecord.channel_id)
    )
    return _rows_to_loads(db.execute(stmt).all())


async def _aggregate_async(
    db: AsyncSession, tenant_id: uuid.UUID, since: datetime
) -> dict[uuid.UUID, int]:
    stmt = (
        select(ModelUsageRecord.channel_id, func.count())
        .where(
            ModelUsageRecord.tenant_id == tenant_id,
            ModelUsageRecord.channel_id.isnot(None),
            ModelUsageRecord.created_at >= since,
        )
        .group_by(ModelUsageRecord.channel_id)
    )
    result = await db.execute(stmt)
    return _rows_to_loads(result.all())


def channel_loads_sync(db: Session, tenant_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """租户渠道窗口用量（渠道 id → 事件数）；缓存未命中才查库，异常降级空表。"""
    if not settings.MODEL_USAGE_LEAST_USED_ENABLED:
        return {}
    now = time.monotonic()
    cached = _cached(tenant_id, now)
    if cached is not None:
        return cached
    try:
        loads = _aggregate_sync(db, tenant_id, _window_start())
    except SQLAlchemyError as exc:
        logger.debug("least-used 聚合失败，降级 created_at asc: tenant=%s err=%s", tenant_id, exc)
        return {}
    _cache[tenant_id] = (now, loads)
    return loads


async def channel_loads_async(db: AsyncSession, tenant_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """异步镜像（GC#11：async 上下文禁止 sync session）。"""
    if not settings.MODEL_USAGE_LEAST_USED_ENABLED:
        return {}
    now = time.monotonic()
    cached = _cached(tenant_id, now)
    if cached is not None:
        return cached
    try:
        loads = await _aggregate_async(db, tenant_id, _window_start())
    except SQLAlchemyError as exc:
        logger.debug("least-used 聚合失败，降级 created_at asc: tenant=%s err=%s", tenant_id, exc)
        return {}
    _cache[tenant_id] = (now, loads)
    return loads

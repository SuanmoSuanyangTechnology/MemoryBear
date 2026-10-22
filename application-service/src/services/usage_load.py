"""Channel least-used load derivation: rolling-window aggregation over model_usage_records + in-process cache.

- Aggregation key = channel_id, window = MODEL_USAGE_LOAD_WINDOW_MINUTES: channel rows
  carry no counter; least-used routing state is derived from a rolling window over the
  usage table (backed by `ix_usage_channel_time`; success and failure events both count
  -- persistently failing channels should be de-weighted too)
- In-process cache TTL 60s (same policy as the channel snapshot cache): at most one
  aggregation query per tenant, zero DB pressure on the resolve hot path
- Failures degrade silently to `{}` (debug log): routing falls back to `created_at asc`
  and business calls are never blocked (side-channel invariant)
- With MODEL_USAGE_LEAST_USED_ENABLED=false, short-circuit to `{}` (no DB query)
- Sync/async dual reads: management-plane/workers use the sync variant, async chains use
  the async variant, each matching its session type
"""
from __future__ import annotations

import logging

import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..config import current_settings
from ..models.model_usage_record import ModelUsageRecord

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 60.0
_cache: dict[uuid.UUID, tuple[float, dict[uuid.UUID, int]]] = {}


def _window_start() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(
        minutes=current_settings().model_usage_load_window_minutes
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
    """Per-tenant channel window usage (channel id -> event count); queries the DB only on cache miss and degrades to an empty map on error."""
    if not current_settings().model_usage_least_used_enabled:
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
    """Async mirror (async contexts must never use a sync session)."""
    if not current_settings().model_usage_least_used_enabled:
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

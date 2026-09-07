"""校正任务（5.3）：1min 增量扫描 updated_at，只删不建——安全态（禁用/删除/吊销）不长期滞后。"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from auth_sdk.snapshot import api_key_hash
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from src import db, redis as iredis
from src.config import settings
from src.repositories.api_key import get_inactive_keys_since
from src.repositories.user import (
    get_inactive_member_user_ids,
    get_inactive_tenants_since,
    get_inactive_users_since,
    get_user_ids_by_tenant,
)

logger = logging.getLogger(__name__)

_LAST_RUN_KEY = "reconcile:last_run"


async def reconcile_once(session, redis, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    last_raw = await redis.get(_LAST_RUN_KEY)
    last = datetime.fromisoformat(last_raw.decode()) if last_raw else now - timedelta(minutes=5)
    deleted = 0
    # 禁用/删除的用户 → 删快照（只删不建；正常用户快照由 TTL + miss 回源处理）
    users = await get_inactive_users_since(session, last)
    for u in users:
        await redis.delete(f"user:{u.id}")
        deleted += 1
    keys = await get_inactive_keys_since(session, last)
    for k in keys:
        await redis.delete(f"api_key:{api_key_hash(k.api_key)}")   # 快照 key = sha256(明文)（5.3）
        deleted += 1
    # 禁用租户 → 批量删该租户全部用户快照（复用 notify kind=tenant 的批量删逻辑）
    tenants = await get_inactive_tenants_since(session, last)
    for t in tenants:
        user_ids = await get_user_ids_by_tenant(session, t.id)
        if user_ids:
            await redis.delete(*(f"user:{uid}" for uid in user_ids))
            deleted += len(user_ids)
    # 失效成员（workspace_members 无 updated_at，全量扫 is_active=False 兜底）→ 删所属用户快照
    member_user_ids = await get_inactive_member_user_ids(session)
    for uid in member_user_ids:
        await redis.delete(f"user:{uid}")
        deleted += 1
    await redis.set(_LAST_RUN_KEY, now.isoformat())
    return deleted


async def reconcile_loop():
    while True:
        try:
            async with db.get_async_db_context() as session:
                await reconcile_once(session, iredis.redis)
        except (SQLAlchemyError, RedisError, ValueError):
            # reconcile_once 的全部失败类型（DB/Redis/last_run 时间解析）；循环保活，TTL 兜底
            logger.exception("reconcile failed")
        await asyncio.sleep(settings.RECONCILE_INTERVAL_SEC)

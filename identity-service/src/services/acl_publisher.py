"""ACL 全量下发：读 acl_rules 全表 → SET Redis → 通知消费端热重载。

写入口有两个：ACL CRUD（controller）commit 后；以及 identity 启动时补偿一次——
Redis 重建/换实例后若长期没有 CRUD 写入，acl:rules:<DB_NAME> 会持续缺失，
消费端（AclRefresher）停在 deny-all。启动补偿失败只记录日志、不阻塞启动。
"""
import asyncio
import logging

from fastapi.encoders import jsonable_encoder
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from src import redis as iredis
from src.config import settings
from src.models import AclRule
from src.schemas.acl import rules_to_redis

logger = logging.getLogger(__name__)

# Deployment-scoped key/channel (derived from DB_NAME unless explicitly set):
# deployments sharing one Redis must not overwrite each other's full rule set.
REDIS_ACL_KEY = settings.ACL_RULES_KEY
REDIS_ACL_CHANGED_CHANNEL = settings.ACL_RULES_CHANGED_CHANNEL

# Change-notification tuning: one bounded retry on top of the initial attempt.
_NOTIFY_TIMEOUT_S = 1.0
_NOTIFY_ATTEMPTS = 2


async def notify_rules_changed() -> None:
    """Broadcast a change hint for hot-reloading consumers (best-effort).

    One bounded retry covers a transient publish failure: a dropped hint on a
    healthy consumer connection would otherwise delay propagation until the
    next unrelated change. Failures are then swallowed — letting the error
    escape would turn a successful push into a 500 at the call sites below.
    """
    for attempt in range(1, _NOTIFY_ATTEMPTS + 1):
        try:
            await asyncio.wait_for(
                iredis.redis.publish(REDIS_ACL_CHANGED_CHANNEL, "changed"),
                timeout=_NOTIFY_TIMEOUT_S,
            )
            return
        except Exception:
            if attempt < _NOTIFY_ATTEMPTS:
                logger.warning(
                    "acl change notification attempt %d failed; retrying",
                    attempt,
                    exc_info=True,
                )
            else:
                logger.exception(
                    "acl rules pushed but change notification failed after %d attempts",
                    attempt,
                )


async def _load_blob(session: AsyncSession) -> str:
    rules = (await session.execute(select(AclRule))).scalars().all()
    return rules_to_redis([jsonable_encoder(r) for r in rules])


async def _publish_blob(blob: str) -> None:
    await iredis.redis.set(REDIS_ACL_KEY, blob)
    await notify_rules_changed()


async def push_rules(session: AsyncSession) -> None:
    """全量读表 → 下发 Redis；任何一步失败抛 SQLAlchemyError/RedisError 由调用方转 500。"""
    await _publish_blob(await _load_blob(session))


async def publish_rules_on_startup() -> None:
    """One-shot full push at startup; failures are logged, never fatal.

    Redis reachability is already validated by init_redis() (ping) and the DB
    by the read-only schema check, so a failure here signals a deployment
    problem (missing acl_rules table, rejected ACL command). Blocking startup
    would take JWKS/snapshot down with it, so keep serving and leave consumers
    fail-closed.

    The DB read runs in its own session scope so the Redis SET/PUBLISH never
    executes inside an open DB transaction.
    """
    try:
        async with db.get_async_db_context() as session:
            blob = await _load_blob(session)
    except SQLAlchemyError:
        logger.exception("startup acl rules read failed; consumers keep last-known rules")
        return
    try:
        await _publish_blob(blob)
    except RedisError:
        logger.exception("startup acl rules push failed; consumers keep last-known rules")

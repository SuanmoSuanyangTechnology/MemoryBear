"""变更通知订阅（决策 #11 修订）：老单体 pub/sub 发消息，identity 重建快照。

用户消息：删旧 key + 直连 DB 组装写回。API key 消息：先删快照与网关负缓存，
带明文（创建/重建/更新）时再组装写回，仅 hash（吊销）时由网关按需回源。"""
import asyncio
import json
import logging

from auth_sdk.snapshot import SNAPSHOT_TTL, api_key_miss_key_from_hash, snapshot_to_json
from redis.asyncio import Redis
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from src.config import settings
from src.repositories.user import get_user_ids_by_tenant
from src.services import snapshot as snap
from src.services.api_keys import build_api_key_snapshot, write_api_key_snapshot

logger = logging.getLogger(__name__)

# An idle pub/sub read blocks without a socket timeout, so a half-open
# connection would hang the subscription silently; bound it and reconnect.
_IDLE_RECONNECT_S = 300.0


async def handle_invalidation(redis: Redis, session: AsyncSession, payload: dict) -> None:
    kind = payload.get("kind")
    if kind == "user":
        user_id = payload["id"]
        # Build first, replace atomically: deleting before the rebuild leaves a
        # miss window where the gateway (no backfill) rejects every request with
        # 401. Any rebuild failure still deletes the old key to stay fail-closed.
        try:
            rebuilt = await snap.build_user_snapshot(session, user_id, redis=redis)
            if rebuilt is None:                       # 查无此人（用户已删除）→ 保持删除
                await redis.delete(f"user:{user_id}")
            else:
                await redis.set(f"user:{user_id}", snapshot_to_json(rebuilt), ex=SNAPSHOT_TTL)
        except Exception:
            await redis.delete(f"user:{user_id}")
            raise
    elif kind == "api_key":
        key_hash = payload["hash"]
        # Drop the gateway negative-cache entry too: a key re-enabled or whose
        # limits changed must not stay rejected for the negative-cache TTL.
        await redis.delete(f"api_key:{key_hash}", api_key_miss_key_from_hash(key_hash))
        # 创建/重建/快照字段更新消息带明文 key（老单体 notify_api_key_created）：删旧 + 直连
        # DB 组装写回，首次访问即可用（不依赖网关回源）；吊销消息仅 hash → 只删，网关按需回源
        plain = payload.get("key")
        if plain:
            rebuilt = await build_api_key_snapshot(session, plain)
            if rebuilt is not None:  # 查无此 key（已删/禁用）→ 保持删除
                await write_api_key_snapshot(redis, plain, rebuilt)
    elif kind == "tenant":
        # 租户禁用影响该租户全部用户快照：批量删快照让网关 fail-closed 立即拒绝。
        # 不能靠 TTL 兜底——活跃用户快照经 GETEX 续期永不过期。
        tenant_id = payload["id"]
        user_ids = await get_user_ids_by_tenant(session, tenant_id)
        if user_ids:
            await redis.delete(*(f"user:{uid}" for uid in user_ids))


async def subscribe(redis, channel: str | None = None) -> None:
    channel = channel or settings.AUTH_INVALIDATION_CHANNEL
    while True:
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(channel)
            while True:
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=None),
                        timeout=_IDLE_RECONNECT_S,
                    )
                except TimeoutError:
                    raise RedisTimeoutError(
                        f"pubsub idle for {_IDLE_RECONNECT_S}s, reconnecting"
                    ) from None
                if message is None:
                    continue
                try:
                    payload = json.loads(message["data"])
                except (TypeError, ValueError):
                    # UnicodeDecodeError 是 ValueError 子类：非 UTF-8 数据解码失败，
                    # 捕获后跳过单条坏消息，避免异常穿透杀死订阅任务
                    continue
                try:
                    async with db.get_async_db_context() as session:
                        await handle_invalidation(redis, session, payload)
                except (KeyError, RedisError, SQLAlchemyError) as exc:  # 单条消息失败不影响订阅循环
                    logger.warning("invalidation handling failed: %s", exc)
        except RedisError as exc:
            # Connection errors and idle-watchdog timeouts both reconnect;
            # CancelledError is a BaseException and is not caught here, so
            # lifespan shutdown can still cancel the task.
            if isinstance(exc, RedisTimeoutError):
                logger.debug("pubsub idle timeout, reconnecting: %s", exc)
            else:
                logger.warning("pubsub subscription error, reconnecting: %s", exc)
        finally:
            try:
                await pubsub.aclose()
            except RedisError:
                pass
        await asyncio.sleep(1)

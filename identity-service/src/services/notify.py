"""变更通知订阅（决策 #11 修订）：老单体 pub/sub 发消息，identity 重建用户快照
（删旧 key + 直连 DB 组装写回）；API key 快照保持删除（网关首期不消费，重建待
API key 路径接入网关后实现）。"""
import asyncio
import json
import logging

from auth_sdk.snapshot import SNAPSHOT_TTL, snapshot_to_json
from redis.asyncio import Redis
from redis.exceptions import RedisError, TimeoutError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src import db
from src.repositories.user import get_user_ids_by_tenant
from src.services import snapshot as snap
from src.services.api_keys import build_api_key_snapshot, write_api_key_snapshot

logger = logging.getLogger(__name__)


async def handle_invalidation(redis: Redis, session: AsyncSession, payload: dict) -> None:
    kind = payload.get("kind")
    if kind == "user":
        user_id = payload["id"]
        await redis.delete(f"user:{user_id}")
        rebuilt = await snap.build_user_snapshot(session, user_id, redis=redis)
        if rebuilt is not None:                       # 查无此人（用户已删除）→ 保持删除
            await redis.set(f"user:{user_id}", snapshot_to_json(rebuilt), ex=SNAPSHOT_TTL)
    elif kind == "api_key":
        await redis.delete(f"api_key:{payload['hash']}")
        # 创建/重建消息带明文 key（老单体 notify_api_key_created）：删旧 + 直连 DB 组装写回，
        # 使新 key 首次访问即可用（网关路径 B miss 即 401，无回源自愈）；吊销消息仅 hash → 只删
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


async def subscribe(redis, channel: str = "auth:invalidations") -> None:
    while True:
        pubsub = redis.pubsub()
        try:
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message["type"] != "message":
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
            # 连接断开/重连；CancelledError 是 BaseException 不受此捕获影响，
            # 取消可正常传播（lifespan 关闭时订阅任务能退出）
            # socket_timeout（2s，与 gateway 对齐）下订阅无消息即超时属常态，降噪为 debug
            if isinstance(exc, TimeoutError):
                logger.debug("pubsub idle timeout, reconnecting: %s", exc)
            else:
                logger.warning("pubsub subscription error, reconnecting: %s", exc)
        finally:
            try:
                await pubsub.aclose()
            except RedisError:
                pass
        await asyncio.sleep(1)

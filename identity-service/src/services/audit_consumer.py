"""审计消费者：Redis Stream → consumer group → 批量插 audit_logs（XACK 后出队）。

多副本安全（XREADGROUP 按消费者分配）+ 崩溃不丢不重（XACK 前消息留在 PEL 重投，
insert 用 event_id ON CONFLICT 幂等去重）。老 list 队列（audit:queue）保留 drain 段，
兼容改造前写入的存量消息；新写入已全部走 stream。
"""
import asyncio
import json
import logging
import os
import socket

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from src import db, redis as iredis
from src.config import settings
from src.repositories.audit import insert_audit_logs

logger = logging.getLogger(__name__)

_GROUP = "audit-consumers"
_CONSUMER = f"{socket.gethostname()}:{os.getpid()}"


async def _drain_legacy_list(session, redis, batch_size: int) -> int:
    """老 list 队列存量消费（lrange + ltrim，非原子但仅存量；新写入不走此路径）。"""
    items = await redis.lrange(settings.AUDIT_QUEUE_KEY, 0, batch_size - 1)
    if not items:
        return 0
    await insert_audit_logs(session, [json.loads(i) for i in items])
    await redis.ltrim(settings.AUDIT_QUEUE_KEY, len(items), -1)
    return len(items)


async def _ensure_group(redis) -> None:
    try:
        await redis.xgroup_create(settings.AUDIT_STREAM_KEY, _GROUP, id="0", mkstream=True)
    except RedisError:
        pass  # BUSYGROUP（组已存在）或连接错误；后者由调用方捕获


async def _consume_stream(session, redis, batch_size: int) -> int:
    await _ensure_group(redis)
    # block=300ms（< REDIS_CMD_TIMEOUT_MS 500ms）：无限阻塞（block=0）会被客户端
    # socket 超时掐断抛 TimeoutError → audit_loop 误报失败无限重试；有限阻塞空转
    # 由 Redis 返回 nil，轮询节奏 = 300ms 阻塞 + loop sleep 1s
    resp = await redis.xreadgroup(_GROUP, _CONSUMER, {settings.AUDIT_STREAM_KEY: ">"},
                                  count=batch_size, block=300)
    if not resp:
        return 0
    entries = resp[0][1]
    if not entries:
        return 0
    # decode_responses=False：field/value 均为 bytes → 归一为 str（jsonb_to_recordset 按 text 接收）
    items = [{k.decode(): (v.decode() if isinstance(v, bytes) else v)
              for k, v in e[1].items()} for e in entries]
    # insert 失败 → 不 XACK（PEL 重投，下轮重试）；成功（含 ON CONFLICT 跳过重复）→ 全量 XACK
    await insert_audit_logs(session, items)
    await redis.xack(settings.AUDIT_STREAM_KEY, _GROUP, *[e[0] for e in entries])
    return len(entries)


async def consume_once(session, redis, batch_size: int = 100) -> int:
    drained = await _drain_legacy_list(session, redis, batch_size)
    if drained:
        return drained
    return await _consume_stream(session, redis, batch_size)


async def audit_loop():
    while True:
        try:
            async with db.get_async_db_context() as session:
                await consume_once(session, iredis.redis)
        except (SQLAlchemyError, RedisError, ValueError, TypeError):
            # consume_once 的全部失败类型（DB/Redis/json）；循环保活，消息留在队列/PEL 下轮再试
            logger.exception("audit_consumer failed")
        await asyncio.sleep(1.0)

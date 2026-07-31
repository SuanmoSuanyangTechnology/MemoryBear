"""
快写 user 消息情绪缓存：回复链路写、快写链路读后即删。
key = ``fastwrite:emotion:{message_id}``，其中 message_id 为回复链路的
``user_message_id``，等于 ``memory_messages.original_message_id``——两条链路天然对齐。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from app.services.fast_write_emotion_client import FastWriteEmotionResult

logger = logging.getLogger(__name__)

# TTL 仅作兜底：正常路径由快写读后即删
_TTL_SEC = 3600
_KEY = "fastwrite:emotion:{mid}"


def build_cache_key(message_id: str) -> str:
    return _KEY.format(mid=message_id)


async def take_cached_emotion(message_id: str) -> Optional[FastWriteEmotionResult]:
    """读后即删（GETDEL）：快写是唯一消费者，取出即删除。

    未命中 / Redis 故障 / 数据损坏一律返回 None，由调用方走自己的 BERT。
    """
    if not message_id:
        return None
    try:
        from app.aioRedis import get_thread_safe_redis

        client = get_thread_safe_redis()
        key = build_cache_key(str(message_id))
        # redis-py 4.0+ 支持 GETDEL 原子操作；老版本/兼容实现退回 get + delete
        getdel = getattr(client, "getdel", None)
        if getdel is not None:
            raw = await getdel(key)
        else:
            raw = await client.get(key)
            if raw:
                await client.delete(key)
        if not raw:
            return None
        data = json.loads(raw)
        return FastWriteEmotionResult(
            emotion=data["emotion"],
            emotion_score=float(data["score"]),
        )
    except Exception as e:
        # 与情绪识别的降级日志同级（warning），便于生产环境统一监控降级率；
        # 未命中走上面的 `if not raw` 分支，不会到这里，故此处均为真实故障（Redis 不可用 / 数据损坏）。
        logger.warning(
            "[EmotionCache] take failed, fallback to None: mid=%s, err=%s", message_id, e
        )
        return None


async def set_cached_emotion(message_id: str, result: Optional[FastWriteEmotionResult]) -> None:
    """仅回复链路调用：BERT 识别成功时写入（失败/超时不得调用本函数）。"""
    if not message_id or result is None:
        return
    try:
        from app.aioRedis import get_thread_safe_redis

        await get_thread_safe_redis().set(
            build_cache_key(str(message_id)),
            json.dumps({"emotion": result.emotion, "score": result.emotion_score}),
            ex=_TTL_SEC,
        )
    except Exception as e:
        # 写缓存失败不影响回复，也不影响快写（快写会 miss 后自己算）；
        # 但会导致快写重复调用 BERT，属需要感知的降级，故与识别失败同为 warning。
        logger.warning("[EmotionCache] set failed: mid=%s, err=%s", message_id, e)

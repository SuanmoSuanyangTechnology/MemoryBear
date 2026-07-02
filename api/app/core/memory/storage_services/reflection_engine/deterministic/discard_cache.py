"""子问题 3 · 丢弃缓存：Redis 7天TTL，避免重复计算低分对"""
import logging
from app.aioRedis import get_redis_connection

logger = logging.getLogger(__name__)
TTL_SECONDS = 7 * 24 * 3600  # 7 天


def _cache_key(end_user_id: str, a_id: str, b_id: str) -> str:
    pair = sorted([a_id, b_id])
    return f"dedup:discarded:{end_user_id}:{pair[0]}:{pair[1]}"


async def filter_discarded(end_user_id: str, candidates: list) -> list:
    """过滤掉近 7 天已评估过的低分对（批量 mget）"""
    if not candidates:
        return candidates
    redis = await get_redis_connection()
    keys = [_cache_key(end_user_id, p.a_id, p.b_id) for p in candidates]
    results = await redis.mget(keys)
    return [p for p, cached in zip(candidates, results) if cached is None]


async def cache_discarded(end_user_id: str, discard_pool: list) -> None:
    """将丢弃的候选对写入 Redis 缓存（pipeline 批量写入）

    写入时机：
      1. P ≤ 0.70 的候选对
      2. LLM 判定拒绝的候选对（避免下次重复调 LLM）
    """
    if not discard_pool:
        return
    redis = await get_redis_connection()
    pipe = redis.pipeline()
    for pair in discard_pool:
        key = _cache_key(end_user_id, pair.a_id, pair.b_id)
        pipe.setex(key, TTL_SECONDS, "1")
    await pipe.execute()
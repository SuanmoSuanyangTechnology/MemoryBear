"""BatchPostStoreAccumulator — 批量后处理累积器。

职责：
  - 将每轮写入结果原子性推入 Redis
  - 判断是否达到阈值
  - 达到阈值时通知调用方派发批量 Celery 任务
  - 提供 force_flush() 供 FlushTask / 手动触发
  - 提供 consume_pending() 供批量任务消费队列

风险缓解：
  - Redis 不可用时调用方回退旧逻辑（不阻塞主流程）
  - Redis Cluster 兼容：key 使用 {end_user_id} hash tag
  - TTL 兜底防止内存泄漏
  - Feature Flag: BATCH_POST_STORE_ENABLED
"""

import json
import logging
import os
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

BATCH_THRESHOLD = int(os.getenv("BATCH_POST_STORE_THRESHOLD", "10"))
BATCH_TIMEOUT = int(os.getenv("BATCH_POST_STORE_TIMEOUT", "300"))
QUEUE_TTL = 3600

# 使用 {end_user_id} hash tag 确保 Redis Cluster 下同 slot
_PENDING_KEY = "post_store:pending:{{{end_user_id}}}"
_COUNT_KEY = "post_store:count:{{{end_user_id}}}"
_DISPATCH_KEY = "post_store:dispatched:{{{end_user_id}}}"

_LUA_ACCUMULATE = """
redis.call('RPUSH', KEYS[1], ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[3]))
local count = redis.call('INCR', KEYS[2])
redis.call('EXPIRE', KEYS[2], tonumber(ARGV[3]))
if count >= tonumber(ARGV[2]) then
    if redis.call('EXISTS', KEYS[3]) == 1 then
        return 0
    end
    return 1
end
return 0
"""


def is_batch_enabled() -> bool:
    """Feature Flag: 是否启用批量累积模式。"""
    return os.getenv("BATCH_POST_STORE_ENABLED", "true").lower() != "false"


class BatchPostStoreAccumulator:
    """批量后处理累积器。

    使用 Redis List + Lua 脚本实现原子性累积和阈值判断。
    """

    def __init__(self, redis_client):
        """
        Args:
            redis_client: 同步 Redis 客户端实例
        """
        self._redis = redis_client

    def accumulate(
        self,
        end_user_id: str,
        entity_ids: List[str],
        llm_model_id: Optional[str],
        language: str = "zh",
        snapshot_dir: Optional[str] = None,
    ) -> bool:
        """累积一轮写入结果。

        使用 Lua 脚本原子性执行 RPUSH + INCR + 阈值判断。

        Args:
            end_user_id: 终端用户 ID
            entity_ids: 本轮写入的实体 ID 列表
            llm_model_id: LLM 模型 UUID
            language: 语言
            snapshot_dir: 调试快照目录

        Returns:
            True 表示达到阈值，调用方应触发批量任务。
        """
        payload = json.dumps({
            "entity_ids": entity_ids,
            "llm_model_id": llm_model_id,
            "language": language,
            "snapshot_dir": snapshot_dir,
            "pushed_at": time.time(),
        })

        keys = [
            _PENDING_KEY.format(end_user_id=end_user_id),
            _COUNT_KEY.format(end_user_id=end_user_id),
            _DISPATCH_KEY.format(end_user_id=end_user_id),
        ]

        result = self._redis.eval(
            _LUA_ACCUMULATE, 3, *keys,
            payload, str(BATCH_THRESHOLD), str(QUEUE_TTL),
        )

        if result == 1:
            logger.info(
                f"[BatchAccumulator] 达到阈值 {BATCH_THRESHOLD}，"
                f"触发批量后处理: end_user_id={end_user_id}"
            )
            return True
        return False

    def consume_pending(self, end_user_id: str) -> List[Dict]:
        """原子消费并清空累积队列。

        使用 pipeline 保证 LRANGE + DELETE 的原子性。

        Returns:
            解析后的记录列表
        """
        key = _PENDING_KEY.format(end_user_id=end_user_id)
        count_key = _COUNT_KEY.format(end_user_id=end_user_id)

        pipe = self._redis.pipeline()
        pipe.lrange(key, 0, -1)
        pipe.delete(key)
        pipe.delete(count_key)
        results = pipe.execute()

        raw_items = results[0] or []
        records = []
        for item in raw_items:
            try:
                records.append(json.loads(item))
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"[BatchAccumulator] 无法解析记录: {item!r}")
        return records

    def set_dispatch_lock(self, end_user_id: str, ttl: int = 600) -> bool:
        """设置派发锁，防止重复触发。

        Returns:
            True 表示成功获取锁。
        """
        key = _DISPATCH_KEY.format(end_user_id=end_user_id)
        return bool(self._redis.set(key, "1", nx=True, ex=ttl))

    def release_dispatch_lock(self, end_user_id: str) -> None:
        """释放派发锁。"""
        key = _DISPATCH_KEY.format(end_user_id=end_user_id)
        self._redis.delete(key)

    def force_flush(self, end_user_id: str) -> bool:
        """强制触发（不检查阈值），用于 FlushTask / 手动触发。

        Returns:
            True 表示有待处理数据且成功获取锁。
        """
        key = _PENDING_KEY.format(end_user_id=end_user_id)
        if self._redis.llen(key) == 0:
            return False
        return self.set_dispatch_lock(end_user_id)

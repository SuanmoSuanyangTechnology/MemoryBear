"""统一的 flush 派发入口，自带幂等保护。

所有需要派发 flush_conversation_task 的代码（ScanIdle、应急脚本、其它路径）
都必须通过 :func:`dispatch_flush_if_not_running` 派发，禁止直接调用
``flush_conversation_task.apply_async``。

这层抽象保证：

1. **幂等性**：同一 ``conversation_id`` 同时只允许一个 flush 任务在跑
   （通过 Redis SETNX ``flush_lock:{conv_id}`` 实现）。
2. **可观测性**：派发来源（``source``）会写到日志，便于排查"是谁派的"。
3. **失败可恢复**：任务派发失败会立即 DEL 幂等锁，防止锁假死。

设计参考：``docs/20260604_lock-flush_conversation/celery_refactor_implementation_plan.md`` §3.3

Q:考虑更改名字与SlidingWindowScheduler类似，当前flush_dispatcher是兜底任务的分发器，因为需要判断是否已有flush_conversation任务正在执行，决定是否派发。
  SlidingWindowScheduler是针对滑动窗口写入的调用器，已经使用了def push_task进行派发。（当前状态：  ）
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Redis key 前缀（与 app.tasks 共享同一个常量定义）。
# 把常量放在这里而不是 tasks.py，是为了避免 ``tasks.py → flush_dispatcher.py → tasks.py``
# 的循环 import：tasks.py 反过来 import 这两个常量即可。
FLUSH_LOCK_KEY_PREFIX = "flush_lock:"

# Flush 任务幂等锁 TTL（秒）。
# 派发 flush_conversation_task 时 SETNX 这把锁，防止同一对话被并发兜底；
# Flush 任务完成（成功或失败）会主动 DELETE 释放。
# TTL 必须 ≥ flush 任务最大执行时间，否则 ScanIdle 可能在任务还没跑完就重复派发。
FLUSH_LOCK_TTL_SECONDS = 1800


def dispatch_flush_if_not_running(
    conversation_id: str,
    end_user_id: Optional[str] = None,
    *,
    source: str = "unknown",
    flush_lock_ttl: int = FLUSH_LOCK_TTL_SECONDS,
) -> bool:
    """检查同 ``conversation_id`` 是否已有 flush 任务在跑，没有则派发。

    Args:
        conversation_id: 对话 ID
        end_user_id: 终端用户 ID（可选；缺失时由 flush 任务内部反查 conversations.user_id）
        source: 派发来源（用于日志诊断）："scan_idle" / "manual" / etc
        flush_lock_ttl: 幂等锁 TTL，默认 ``FLUSH_LOCK_TTL_SECONDS``

    Returns:
        ``True`` -> 已派发新任务
        ``False`` -> 已有任务在跑或派发失败，未派发
    """
    # lazy import：避免 ``app.tasks`` 与本模块循环 import
    from app.tasks import get_sync_redis_client

    redis_client = get_sync_redis_client()
    if redis_client is None:
        logger.warning(
            f"[FlushDispatcher][{source}] Redis 不可用，无法保证幂等，"
            f"跳过派发: conv={conversation_id}"
        )
        return False

    flush_lock_key = f"{FLUSH_LOCK_KEY_PREFIX}{conversation_id}"

    # SETNX 抢幂等锁
    try:
        acquired = redis_client.set(
            flush_lock_key, "1",
            ex=flush_lock_ttl,
            nx=True,
        )
    except Exception as e:
        logger.error(
            f"[FlushDispatcher][{source}] SETNX 失败: "
            f"conv={conversation_id}, err={e}"
        )
        return False

    if not acquired:
        logger.info(
            f"[FlushDispatcher][{source}] 已有 flush 任务在跑，跳过派发: "
            f"conv={conversation_id}"
        )
        return False

    # 派发 flush 任务
    try:
        # lazy import：避免与 ``app.tasks`` 循环
        # NOTE: Phase B 之后这里改为 ``memory_write_flush_task``
        from app.tasks import flush_conversation_task

        flush_conversation_task.apply_async(
            kwargs={"conversation_id": conversation_id},
            queue="memory_tasks",
        )
        logger.info(
            f"[FlushDispatcher][{source}] 已派发 flush 任务: "
            f"conv={conversation_id}, end_user_id={end_user_id}"
        )
        return True
    except Exception as e:
        # 派发失败必须主动释放幂等锁，否则会被锁住直到 TTL 过期
        logger.error(
            f"[FlushDispatcher][{source}] 派发失败，释放 flush_lock: "
            f"conv={conversation_id}, err={e}",
            exc_info=True,
        )
        try:
            redis_client.delete(flush_lock_key)
        except Exception:
            pass
        return False

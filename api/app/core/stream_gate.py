"""流式请求并发闸门。

多 Agent 流式请求在准备阶段会同时持有共享 session 的连接并另开独立连接，
单请求的连接峰值 > 1。高并发下"持有一个、再申请一个"会让整个连接池互相等待直到
QueuePool 超时。闸门把同时进行的请求数限制在池可承载的范围内，超出的排队，
排队期间不占用任何 DB 连接（session 在首次使用前不会取连接）。
"""
import asyncio
import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class StreamConcurrencyGate:
    """基于 asyncio.Semaphore 的并发闸门；limit<=0 表示不限制。

    用法：acquire() 返回 True 后必须在 finally 里 release()；返回 False 表示排队超时，
    此时不能 release()。
    """

    def __init__(self, name: str, limit: int, queue_timeout: float) -> None:
        self.name = name
        self.limit = max(0, int(limit))
        self.queue_timeout = float(queue_timeout)
        # 延迟到首次使用时创建，保证在运行中的事件循环里构造
        self._sem: Optional[asyncio.Semaphore] = None
        self._waiting = 0

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    async def acquire(self) -> bool:
        if not self.enabled:
            return True
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.limit)
        sem = self._sem
        if not sem.locked():
            await sem.acquire()  # 未满时不会挂起
            return True

        self._waiting += 1
        logger.info(
            "stream gate full, request queued name=%s limit=%d waiting=%d",
            self.name, self.limit, self._waiting,
        )
        try:
            if self.queue_timeout > 0:
                await asyncio.wait_for(sem.acquire(), timeout=self.queue_timeout)
            else:
                await sem.acquire()
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "stream gate queue timeout name=%s limit=%d timeout=%.1fs",
                self.name, self.limit, self.queue_timeout,
            )
            return False
        finally:
            self._waiting -= 1

    def release(self) -> None:
        if self.enabled and self._sem is not None:
            self._sem.release()


_multi_agent_gate: Optional[StreamConcurrencyGate] = None

BUSY_MESSAGE = "服务繁忙，请稍后重试"


def get_multi_agent_stream_gate() -> StreamConcurrencyGate:
    global _multi_agent_gate
    if _multi_agent_gate is None:
        _multi_agent_gate = StreamConcurrencyGate(
            "multi_agent_stream",
            settings.MULTI_AGENT_STREAM_MAX_CONCURRENCY,
            settings.MULTI_AGENT_STREAM_QUEUE_TIMEOUT,
        )
    return _multi_agent_gate

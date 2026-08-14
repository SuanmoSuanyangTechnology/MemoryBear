"""记忆读取展示专用异步写入队列

检索主链路只做「内存聚合 → put_nowait → 立即返回」，
绝不等待 PG 连接、事务或重试。展示记录不是检索事实源，
队列满或进程异常退出时允许丢弃。

当前产生读取展示记录的业务入口均运行在 FastAPI lifespan 进程中，正常通过
进程内队列批量写入。未运行 FastAPI lifespan 时的 fire-and-forget 写入仅作为
防御性降级，不属于正常业务链路，同样不阻塞检索返回。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, ClassVar

from app.core.config import settings
from app.db import get_async_db_context
from app.repositories.memory_display_record_repository import (
    MemoryDisplayRecordRepository,
)
from app.schemas.memory_retrieval_display_schema import RetrieveDisplayTask

logger = logging.getLogger(__name__)

# consumer 内的有限重试次数（重试复用相同 id / operation_id）
_MAX_RETRIES = 2

# 丢弃告警限频间隔（秒），避免队列长期打满时日志被刷爆
_DROP_WARN_INTERVAL_SECONDS = 30.0

# 关闭时 flush 的固定超时（秒），超时后允许丢弃剩余展示记录
_SHUTDOWN_TIMEOUT_SECONDS = 10.0


class MemoryRetrievalDisplayQueue:
    """单例有界异步队列 + 后台批量写入。

    用法::

        # 启动（FastAPI lifespan）
        await MemoryRetrievalDisplayQueue.start()

        # 投递（检索主链路，非 async，绝不阻塞）
        MemoryRetrievalDisplayQueue.enqueue_nowait(task)

        # 关闭（FastAPI lifespan）
        await MemoryRetrievalDisplayQueue.stop()
    """

    _instance: ClassVar["MemoryRetrievalDisplayQueue | None"] = None

    def __init__(self) -> None:
        self._queue: asyncio.Queue[RetrieveDisplayTask | None] = asyncio.Queue(
            maxsize=settings.MEMORY_RETRIEVAL_DISPLAY_QUEUE_SIZE
        )
        self._consumer_task: asyncio.Task[Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._fallback_tasks: set[asyncio.Task[Any]] = set()
        self._last_drop_warn_at = 0.0
        self._metrics: dict[str, int] = {
            "enqueued": 0,
            "written": 0,
            "dropped": 0,
            "retried": 0,
            "failed": 0,
            "fallback": 0,
        }

    # ── 单例访问 ─────────────────────────────────────────

    @classmethod
    def _get(cls) -> "MemoryRetrievalDisplayQueue":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 公开 API ─────────────────────────────────────────

    @classmethod
    async def start(cls) -> None:
        inst = cls._get()
        if inst._running and inst._consumer_task and not inst._consumer_task.done():
            return
        inst._running = True
        inst._loop = asyncio.get_running_loop()
        inst._consumer_task = asyncio.create_task(inst._consumer())
        logger.info("[RetrievalDisplay] 读取展示写入 consumer 已启动")

    @classmethod
    def enqueue_nowait(cls, task: RetrieveDisplayTask) -> bool:
        """非阻塞投递。返回 False 表示本条展示记录被丢弃。"""
        inst = cls._get()

        if not inst._consumer_available():
            return inst._schedule_fallback_write(task)

        try:
            inst._queue.put_nowait(task)
        except asyncio.QueueFull:
            inst._metrics["dropped"] += 1
            inst._warn_dropped("队列已满")
            return False

        inst._metrics["enqueued"] += 1
        return True

    @classmethod
    async def flush(cls) -> None:
        """排空队列中剩余任务（关闭流程使用）。"""
        inst = cls._get()
        tasks = inst._drain()
        if tasks:
            logger.info("[RetrievalDisplay] flush 剩余 %d 条读取展示记录", len(tasks))
            await inst._write_batch(tasks)

    @classmethod
    async def stop(cls) -> None:
        """优雅关闭：投递哨兵、等待 consumer 收尾、等待兜底任务。"""
        inst = cls._get()
        if not inst._running:
            return
        inst._running = False

        try:
            inst._queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

        if inst._consumer_task is not None:
            try:
                await asyncio.wait_for(
                    inst._consumer_task,
                    timeout=_SHUTDOWN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[RetrievalDisplay] consumer 未在 %ss 内退出，丢弃剩余展示记录",
                    _SHUTDOWN_TIMEOUT_SECONDS,
                )
                inst._consumer_task.cancel()
            except Exception:
                logger.warning("[RetrievalDisplay] consumer 退出异常", exc_info=True)

        if inst._fallback_tasks:
            pending = list(inst._fallback_tasks)
            done, still_pending = await asyncio.wait(
                pending,
                timeout=_SHUTDOWN_TIMEOUT_SECONDS,
            )
            for stale in still_pending:
                stale.cancel()

        inst._consumer_task = None
        inst._loop = None
        logger.info(
            "[RetrievalDisplay] consumer 已停止, metrics=%s, queue_size=%d",
            inst._metrics,
            inst._queue.qsize(),
        )

    @classmethod
    def stats(cls) -> dict[str, int]:
        """返回监控指标：投递数、写入成功数、丢弃数、重试数、失败数、队列长度。"""
        inst = cls._get()
        return {**inst._metrics, "queue_size": inst._queue.qsize()}

    # ── 内部实现 ─────────────────────────────────────────

    def _consumer_available(self) -> bool:
        """consumer 是否运行在当前事件循环上且仍存活。"""
        if not self._running or self._consumer_task is None:
            return False
        if self._consumer_task.done():
            return False
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    def _schedule_fallback_write(self, task: RetrieveDisplayTask) -> bool:
        """没有 consumer 时的兜底：当前事件循环上 fire-and-forget 写入。

        仍然不阻塞检索返回，只是失去批量合并和统一 flush 的能力。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._metrics["dropped"] += 1
            self._warn_dropped("当前线程没有运行中的事件循环")
            return False

        fallback = loop.create_task(self._write_batch([task]))
        self._fallback_tasks.add(fallback)
        fallback.add_done_callback(self._fallback_tasks.discard)
        self._metrics["fallback"] += 1
        return True

    def _warn_dropped(self, reason: str) -> None:
        now = time.monotonic()
        if now - self._last_drop_warn_at < _DROP_WARN_INTERVAL_SECONDS:
            return
        self._last_drop_warn_at = now
        logger.warning(
            "[RetrievalDisplay] 丢弃读取展示记录（%s）: dropped=%d, queue_size=%d",
            reason,
            self._metrics["dropped"],
            self._queue.qsize(),
        )

    def _drain(self) -> list[RetrieveDisplayTask]:
        tasks: list[RetrieveDisplayTask] = []
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is not None:
                tasks.append(item)
        return tasks

    async def _consumer(self) -> None:
        max_batch = settings.MEMORY_RETRIEVAL_DISPLAY_MAX_BATCH
        max_wait = settings.MEMORY_RETRIEVAL_DISPLAY_MAX_WAIT_MS / 1000

        while self._running:
            try:
                first = await asyncio.wait_for(self._queue.get(), timeout=max_wait)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if first is None:  # 哨兵
                break

            batch = [first]
            deadline = time.monotonic() + max_wait
            while len(batch) < max_batch:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=min(remaining, 0.05),
                    )
                except asyncio.TimeoutError:
                    break
                except asyncio.CancelledError:
                    self._running = False
                    break
                if item is None:
                    self._running = False
                    break
                batch.append(item)

            await self._write_batch(batch)

        remaining_tasks = self._drain()
        if remaining_tasks:
            await self._write_batch(remaining_tasks)

    async def _write_batch(self, batch: list[RetrieveDisplayTask]) -> None:
        """有限重试批量写 PG；单批失败不影响后续批次。"""
        if not batch:
            return

        rows = [task.to_row() for task in batch]
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                async with get_async_db_context() as db:
                    inserted = await MemoryDisplayRecordRepository.bulk_insert_retrieved_async(
                        db, rows
                    )
                self._metrics["written"] += inserted
                logger.debug(
                    "[RetrievalDisplay] PG 写入完成: attempted=%d, inserted=%d",
                    len(rows),
                    inserted,
                )
                return
            except Exception as exc:  # noqa: BLE001 - 展示记录失败不反馈到检索
                last_error = exc
                if attempt + 1 < _MAX_RETRIES:
                    self._metrics["retried"] += 1
                logger.warning(
                    "[RetrievalDisplay] PG 写入失败 (attempt %d/%d): %s",
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                    exc_info=True,
                )

        self._metrics["failed"] += len(rows)
        logger.error(
            "[RetrievalDisplay] PG 写入在 %d 次尝试后仍失败: count=%d, error=%s",
            _MAX_RETRIES,
            len(rows),
            last_error,
        )

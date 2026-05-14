"""
BearLogger — 结构化任务日志工具

在大量中间模块日志中提供醒目的 Pipeline 步骤进度标记。
基于标准 logging.Logger，不修改现有日志配置。

设计要点：
- 每个 step 只输出一行完成日志（不输出"开始"行，减少噪音）
- Pipeline 开始/结束用 ═══ 粗分隔线，在终端中一眼可辨
- step 完成行用 ▶ 图标 + 固定宽度对齐，紧凑且整齐
- 性能告警用 ⚡ 标记，超过阈值自动触发
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, Dict, Optional


# ── 上下文变量（线程/协程安全）──
_trace_id: ContextVar[str] = ContextVar("bear_trace_id", default="")


# ── 默认性能阈值（秒）──
DEFAULT_PERF_THRESHOLDS: Dict[str, float] = {
    "预处理": 10,
    "萃取": 60,
    "存储": 30,
    "聚类": 5,
    "摘要": 30,
}


class _StepScope:
    """Step 作用域，持有单步的状态和元数据。"""

    def __init__(
        self,
        logger: logging.Logger,
        index: int,
        total: int,
        category: str,
        description: str,
        threshold: Optional[float] = None,
    ):
        self._logger = logger
        self._index = index
        self._total = total
        self._category = category
        self._description = description
        self._threshold = threshold
        self._start_time = 0.0
        self._kv: Dict[str, Any] = {}

    def metadata(self, **kv: Any) -> None:
        """附加元数据，会在完成日志的行尾展示。"""
        self._kv.update(kv)

    def _start(self) -> None:
        self._start_time = time.time()

    def _succeed(self) -> None:
        elapsed = time.time() - self._start_time

        # 性能告警
        if self._threshold and elapsed > self._threshold:
            status = f"⚡ {elapsed:.2f}s [SLOW]"
        else:
            status = f"✔ {elapsed:.2f}s"

        # 元数据
        kv_str = ""
        if self._kv:
            kv_str = "  " + ", ".join(f"{k}={v}" for k, v in self._kv.items())

        self._logger.info(
            f"  ▶ [{self._index}/{self._total}] "
            f"{self._category}：{self._description} "
            f"── {status}{kv_str}"
        )

    def _fail(self, error: Exception) -> None:
        elapsed = time.time() - self._start_time
        self._logger.error(
            f"  ✘ [{self._index}/{self._total}] "
            f"{self._category}：{self._description} "
            f"── FAILED {elapsed:.2f}s  error={error}"
        )


class BearLogger:
    """结构化任务日志工具。

    用法::

        bear = BearLogger("memory.pipeline")

        async with bear.pipeline("WritePipeline", mode="正式"):
            async with bear.step(1, 5, "预处理", "消息分块") as s:
                result = await preprocess()
                s.metadata(chunks=3)
    """

    def __init__(
        self,
        name: str = "memory.pipeline",
        perf_thresholds: Optional[Dict[str, float]] = None,
    ):
        self._logger = logging.getLogger(name)
        self._thresholds = perf_thresholds or DEFAULT_PERF_THRESHOLDS

    @asynccontextmanager
    async def pipeline(self, name: str, **context_kv: Any):
        """Pipeline 级作用域。开始和结束用醒目的分隔线。"""
        trace_id = uuid.uuid4().hex[:8]
        token = _trace_id.set(trace_id)
        start = time.time()

        ctx_parts = [f"{k}={v}" for k, v in context_kv.items()]
        ctx_str = ", ".join(ctx_parts)

        self._logger.info(
            f"{'═' * 60}\n"
            f"  🚀 {name} 开始  {ctx_str}\n"
            f"{'─' * 60}"
        )

        error = None
        try:
            yield self
        except Exception as e:
            error = e
            raise
        finally:
            elapsed = time.time() - start
            if error:
                self._logger.error(
                    f"{'─' * 60}\n"
                    f"  ✘ {name} 失败 ({elapsed:.2f}s)  error={error}\n"
                    f"{'═' * 60}"
                )
            else:
                self._logger.info(
                    f"{'─' * 60}\n"
                    f"  ✔ {name} 完成 ({elapsed:.2f}s)\n"
                    f"{'═' * 60}"
                )
            _trace_id.reset(token)

    @asynccontextmanager
    async def step(
        self,
        index: int,
        total: int,
        category: str,
        description: str,
    ):
        """Step 级作用域。只在完成时输出一行日志（减少噪音）。"""
        scope = _StepScope(
            logger=self._logger,
            index=index,
            total=total,
            category=category,
            description=description,
            threshold=self._thresholds.get(category),
        )
        scope._start()
        try:
            yield scope
        except Exception as e:
            scope._fail(e)
            raise
        else:
            scope._succeed()

    def info(self, message: str, **kv: Any) -> None:
        """带缩进的 info 日志。"""
        suffix = ""
        if kv:
            suffix = "  " + ", ".join(f"{k}={v}" for k, v in kv.items())
        self._logger.info(f"  │ {message}{suffix}")

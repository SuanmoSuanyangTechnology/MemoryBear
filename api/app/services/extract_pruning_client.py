"""负责新模型的 HTTP 请求、结果校验和进程内并发控制。

本文件不处理 LLM 回退；发生错误时，由调用方执行原有 LLM 方法。
正式写入和试运行分别限制并发，同一进程的线程和事件循环共用对应计数。
每次调用单独创建 HTTP 客户端，并在结束时关闭。
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict

from app.core.config import settings

logger = logging.getLogger(__name__)
Entry = Literal["formal", "pilot"]
ASSISTANT_TYPES = frozenset({
    "comfort", "suggestion", "recommendation", "warning", "instruction",
    "question", "agreement", "repetition", "other",
})


class ExtractPruningResult(BaseModel):
    """五个字段均为必填且严格检查类型；与输入相关的规则由下方函数校验。"""

    model_config = ConfigDict(strict=True, extra="forbid")

    assistant_memory_hint: str | None
    assistant_memory_type: str | None
    should_process_user_msg: bool | None
    processed_user_msg: str | None
    processed_user_topic_entity_hint: str | None


def validate_result(data: object, user_text: str, assistant_text: str) -> ExtractPruningResult:
    result = ExtractPruningResult.model_validate(data)

    def generated(value: str | None) -> bool:
        return value is not None and bool(value.strip()) and value.strip().lower() != "null"

    if assistant_text.strip():
        if not generated(result.assistant_memory_hint) or result.assistant_memory_type not in ASSISTANT_TYPES:
            raise ValueError("invalid assistant result")
    elif result.assistant_memory_hint is not None or result.assistant_memory_type is not None:
        raise ValueError("empty assistant must have null fields")

    user_fields = (result.processed_user_msg, result.processed_user_topic_entity_hint)
    if not user_text.strip():
        if result.should_process_user_msg is not None or any(value is not None for value in user_fields):
            raise ValueError("empty user must have null fields")
    elif result.should_process_user_msg is True:
        if not all(generated(value) for value in user_fields):
            raise ValueError("processed user requires text and topic")
    elif result.should_process_user_msg is False:
        if any(value is not None for value in user_fields):
            raise ValueError("unprocessed user must have null generated fields")
    else:
        raise ValueError("nonempty user requires boolean gate")
    return result


class _ProcessSlots:
    """不绑定事件循环的计数器，供同一进程内的多个线程和事件循环共用。

    仅在检查和更新计数时短暂持锁，等待名额时异步让出执行机会。
    不在线程池中阻塞等待名额，避免任务取消后仍占用名额而无法释放。
    等待过程可以取消，不设置本地排队超时。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._active = {"formal": 0, "pilot": 0}

    @asynccontextmanager
    async def acquire(self, entry: Entry, limit: int):
        if entry not in self._active:
            raise ValueError("unknown pruning entry")
        if limit < 1:
            raise ValueError("concurrency must be positive")
        while True:
            with self._lock:
                if self._active[entry] < limit:
                    self._active[entry] += 1
                    break
            await asyncio.sleep(0.01)
        try:
            yield
        finally:
            with self._lock:
                self._active[entry] -= 1


_slots = _ProcessSlots()


def _reset_after_fork():
    global _slots
    _slots = _ProcessSlots()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


def is_available() -> bool:
    return bool(
        settings.EXTRACT_PRUNING_ENABLED
        and settings.EXTRACT_PRUNING_URL.strip()
        and settings.EXTRACT_PRUNING_API_KEY.strip()
        and settings.EXTRACT_PRUNING_MODEL.strip()
    )


async def predict(user_text: str, assistant_text: str, *, entry: Entry) -> ExtractPruningResult:
    """只调用一次，不重试；取消继续向上传递，已取得的名额在结束时释放。"""
    if not is_available():
        raise ValueError("extract-pruning is disabled or unconfigured")
    if not user_text.strip() and not assistant_text.strip():
        return validate_result({name: None for name in ExtractPruningResult.model_fields}, "", "")

    timeout = settings.EXTRACT_PRUNING_REQUEST_TIMEOUT_SECONDS
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("request timeout must be finite and positive")
    async with _slots.acquire(entry, settings.EXTRACT_PRUNING_MAX_CONCURRENT):
        started = time.monotonic()
        try:
            # 取得名额后才开始计算总超时，不包含本地排队时间。
            # 关闭 HTTP 客户端自身的超时，统一由外层控制本次调用的总等待时间。
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(timeout=None) as client:
                    response = await client.post(
                        settings.EXTRACT_PRUNING_URL,
                        headers={
                            "Authorization": f"Bearer {settings.EXTRACT_PRUNING_API_KEY}",
                            "X-GPUStack-Model": settings.EXTRACT_PRUNING_MODEL,
                        },
                        json={"msgs": [
                            {"role": "User", "msg": user_text},
                            {"role": "Assistant", "msg": assistant_text},
                        ]},
                    )
                    response.raise_for_status()
                    result = validate_result(response.json(), user_text, assistant_text)
        finally:
            logger.info("[ExtractPruning] entry=%s elapsed=%.3fs", entry, time.monotonic() - started)
        return result

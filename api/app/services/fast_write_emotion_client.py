"""Fast Write BERT 情绪 HTTP client。

调用已部署的 BERT 情绪服务（`/v1/rerank`），返回 top 情绪及分数。
失败语义：任何异常（网络/超时/HTTP 4xx-5xx/JSON 结构/枚举/值域）都向上抛出，由调用方
``FastWritePipeline._extract_emotion`` 统一降级为 None——服务故障绝不伪造 neutral。
"""

from __future__ import annotations

import asyncio
import logging
import threading

import httpx
from pydantic import BaseModel

from app.core.config import settings

logger = logging.getLogger(__name__)

# BERT 原生 10 类情绪枚举（与已部署服务对齐）
_VALID_EMOTIONS = frozenset({
    "neutral", "joy", "relief", "anxiety", "sadness",
    "anger", "frustration", "loneliness", "confusion", "hope",
})

# 超时（秒），与 FastWritePipeline.EMOTION_TIMEOUT_SEC 对齐；硬编码，不走 env。
_EMOTION_TIMEOUT_SEC = 2.0

# 每线程一个 client：连接池绑定在创建它的 loop 上，threads pool 下每线程独立 loop，
# 故用 thread-local 隔离，避免跨 loop 复用。
_local = threading.local()


class FastWriteEmotionResult(BaseModel):
    """BERT 情绪结果（仅消费 top1）。"""

    emotion: str
    emotion_score: float


def _get_client() -> httpx.AsyncClient:
    """取当前线程/loop 的 AsyncClient；不存在或 loop 被换过则重建。"""
    loop = asyncio.get_event_loop()
    client = getattr(_local, "client", None)
    if client is None or getattr(_local, "loop", None) is not loop:
        _local.client = httpx.AsyncClient(
            timeout=_EMOTION_TIMEOUT_SEC,
            # 每线程串行、一次一请求：池子稳态只需 1 条，留 1 条余量给瞬时重连。
            limits=httpx.Limits(max_keepalive_connections=1, max_connections=2),
        )
        _local.loop = loop
    return _local.client


async def predict(text: str) -> FastWriteEmotionResult:
    """调用 BERT /v1/rerank，返回 top 情绪；任何异常向上抛出（由调用方降级）。"""
    resp = await _get_client().post(
        settings.FAST_WRITE_EMOTION_URL,
        headers={"Authorization": f"Bearer {settings.FAST_WRITE_EMOTION_API_KEY}"},
        json={"model": settings.FAST_WRITE_EMOTION_MODEL, "query": text},
    )
    resp.raise_for_status()
    data = resp.json()

    emotion = data.get("top_emotion")
    raw_score = data.get("top_score")
    if emotion not in _VALID_EMOTIONS or raw_score is None:
        raise ValueError(
            f"invalid emotion payload: top_emotion={emotion!r}, top_score={raw_score!r}"
        )
    score = float(raw_score)
    if not (0.0 <= score <= 1.0):
        raise ValueError(f"top_score out of range [0,1]: {score}")

    return FastWriteEmotionResult(emotion=emotion, emotion_score=score)

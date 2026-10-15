"""Fast Write BERT emotion HTTP client.

Calls the deployed BERT emotion service (`/v1/rerank`) and returns the top emotion and score.
Failure semantics: any exception (network/timeout/HTTP 4xx-5xx/JSON shape/enum/value range)
propagates upward, where the caller ``FastWritePipeline._extract_emotion`` degrades it to None
uniformly -- a service failure never fakes "neutral".
"""

from __future__ import annotations

import asyncio
import logging
import threading

import httpx
from pydantic import BaseModel

from ..config import current_settings

logger = logging.getLogger(__name__)

# BERT's native 10-emotion enum (aligned with the deployed service)
_VALID_EMOTIONS = frozenset({
    "neutral", "joy", "relief", "anxiety", "sadness",
    "anger", "frustration", "loneliness", "confusion", "hope",
})

# One client per thread: the connection pool is bound to the loop that created it, and under
# a thread pool each thread has its own loop, so thread-local isolation avoids cross-loop reuse.
_local = threading.local()


class FastWriteEmotionResult(BaseModel):
    """BERT emotion result (only top1 is consumed)."""

    emotion: str
    emotion_score: float


def _get_client() -> httpx.AsyncClient:
    """Get the AsyncClient for the current thread/loop; rebuild it if absent or if the loop changed."""
    loop = asyncio.get_event_loop()
    client = getattr(_local, "client", None)
    if client is None or getattr(_local, "loop", None) is not loop:
        _local.client = httpx.AsyncClient(
            timeout=current_settings().fast_write_emotion_http_timeout_seconds,
            # Serial per thread, one request at a time: the pool needs just 1 connection in
            # steady state, with 1 spare for transient reconnects.
            limits=httpx.Limits(max_keepalive_connections=1, max_connections=2),
        )
        _local.loop = loop
    return _local.client


async def predict(text: str) -> FastWriteEmotionResult:
    """Call BERT /v1/rerank and return the top emotion; any exception propagates (caller degrades)."""
    resp = await _get_client().post(
        current_settings().fast_write_emotion_url,
        headers={"Authorization": f"Bearer {current_settings().fast_write_emotion_api_key.get_secret_value()}"},
        json={"model": current_settings().fast_write_emotion_model, "query": text},
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

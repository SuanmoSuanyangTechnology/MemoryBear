"""
Emotion cache for fast-write user messages: written by the reply pipeline,
read-and-delete by the fast-write pipeline.
key = ``fastwrite:emotion:{message_id}``, where message_id is the reply pipeline's
``user_message_id``, equal to ``memory_messages.original_message_id`` — the two
pipelines align naturally.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from ...services.fast_write_emotion_client import FastWriteEmotionResult

logger = logging.getLogger(__name__)

# TTL is only a safety net: the normal path is read-and-delete by fast-write
_TTL_SEC = 3600
_KEY = "fastwrite:emotion:{mid}"


def build_cache_key(message_id: str) -> str:
    return _KEY.format(mid=message_id)


async def take_cached_emotion(message_id: str) -> Optional[FastWriteEmotionResult]:
    """Read-and-delete (GETDEL): fast-write is the only consumer, so take and remove.

    Cache miss / Redis failure / corrupted data all return None; the caller then
    falls back to its own BERT.
    """
    if not message_id:
        return None
    try:
        from ...aio_redis import get_thread_safe_redis

        client = await get_thread_safe_redis()
        key = build_cache_key(str(message_id))
        # redis-py 4.0+ supports the atomic GETDEL operation; older/compatible implementations fall back to get + delete
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
        # Same level (warning) as the emotion-detection degradation logs, so production
        # can monitor the degradation rate uniformly; a miss takes the `if not raw`
        # branch above and never lands here, so every entry here is a real failure
        # (Redis unavailable / data corrupted).
        logger.warning(
            "[EmotionCache] take failed, fallback to None: mid=%s, err=%s", message_id, e
        )
        return None


async def set_cached_emotion(message_id: str, result: Optional[FastWriteEmotionResult]) -> None:
    """Called only by the reply pipeline: write on successful BERT detection (never on failure/timeout)."""
    if not message_id or result is None:
        return
    try:
        from ...aio_redis import get_thread_safe_redis

        client = await get_thread_safe_redis()
        await client.set(
            build_cache_key(str(message_id)),
            json.dumps({"emotion": result.emotion, "score": result.emotion_score}),
            ex=_TTL_SEC,
        )
    except Exception as e:
        # A cache write failure affects neither the reply nor fast-write (fast-write
        # computes its own result on a miss), but it makes fast-write call BERT again;
        # that is a degradation worth noticing, hence warning level like detection failure.
        logger.warning("[EmotionCache] set failed: mid=%s, err=%s", message_id, e)

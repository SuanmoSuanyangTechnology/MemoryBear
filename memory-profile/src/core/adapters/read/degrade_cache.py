"""Redis cache for the re-embedding degrade check (host ``@redis_cache`` parity).

The host wraps ``should_degrade_semantic`` with ``@redis_cache(ttl=60,
prefix="reembed_degrade", id_arg="end_user_id")``. Without the cache every read
queries PostgreSQL twice; the decision also differs inside the host's freshness
window. This adapter mirrors the TTL and key scope (user + workspace + model);
cache failures always fall back to the live query.
"""

from __future__ import annotations

import logging
from typing import Any

from src.infrastructure.redis import RedisManager

logger = logging.getLogger(__name__)

TTL_SECONDS = 60


class RedisDegradeCache:
    def __init__(self, redis: RedisManager) -> None:
        self._redis = redis

    async def get(self, key: str) -> bool | None:
        try:
            client = await self._redis.client()
            value = await client.get(key)
        except Exception:
            logger.warning("degrade cache read failed", exc_info=True)
            return None
        if value is None:
            return None
        return value in (b"1", "1", 1)

    async def set(self, key: str, value: bool) -> None:
        try:
            client = await self._redis.client()
            await client.set(key, b"1" if value else b"0", ex=TTL_SECONDS)
        except Exception:
            logger.warning("degrade cache write failed", exc_info=True)


def key_for(
    *, end_user_id: str, workspace_id: Any, embedding_model_id: Any
) -> str:
    return (
        f"cache:reembed_degrade:profile:{end_user_id}:"
        f"{workspace_id}:{embedding_model_id}"
    )


__all__ = ["RedisDegradeCache", "TTL_SECONDS", "key_for"]

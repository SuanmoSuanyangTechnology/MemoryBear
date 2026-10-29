"""Redis implementation of the interest-distribution cache.

Key shape, payload and TTL match memory-profile's cache-aside helpers, so either
writer can serve the other. A read failure counts as a miss; a write failure is
logged and swallowed because the next online request regenerates the entry.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from redis import Redis

logger = logging.getLogger(__name__)

CACHE_PREFIX = "cache:memory:interest_distribution"


def cache_key(end_user_id: str, language: str) -> str:
    return f"{CACHE_PREFIX}:by_user:{end_user_id}:{language}"


class RedisInterestCache:
    def __init__(
        self, client_provider: Callable[[], Redis], ttl_seconds: int
    ) -> None:
        self._client_provider = client_provider
        self._ttl_seconds = ttl_seconds

    def exists(self, end_user_id: str, language: str) -> bool:
        try:
            return bool(self._client_provider().exists(cache_key(end_user_id, language)))
        except Exception:
            logger.warning(
                "interest cache probe failed user=%s language=%s",
                end_user_id,
                language,
                exc_info=True,
            )
            return False

    def write(
        self, end_user_id: str, language: str, items: list[dict[str, Any]]
    ) -> None:
        payload = {
            "data": items,
            "generated_at": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "cached": True,
        }
        try:
            self._client_provider().set(
                cache_key(end_user_id, language),
                json.dumps(payload, ensure_ascii=False),
                ex=self._ttl_seconds,
            )
        except Exception:
            logger.warning(
                "interest cache write failed user=%s language=%s",
                end_user_id,
                language,
                exc_info=True,
            )

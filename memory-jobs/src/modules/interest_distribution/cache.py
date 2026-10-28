"""Interest-distribution cache, written by the pre-warm units.

The online path leaves the cache to its caller (memory-profile): the worker only
reports ``cacheable``. The dashboard pre-warm has no caller to hand it to, so its
units write the same key and payload themselves. Key shape, payload and TTL match
memory-profile's cache-aside helpers, so either writer can serve the other.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from src.runtime import get_runtime

logger = logging.getLogger(__name__)

#: ``cache:memory:interest_distribution:by_user:<end_user_id>:<language>``.
CACHE_PREFIX = "cache:memory:interest_distribution"


def cache_key(end_user_id: str, language: str) -> str:
    """The key memory-profile reads for this user and language."""

    return f"{CACHE_PREFIX}:by_user:{end_user_id}:{language}"


def is_cached(end_user_id: str, language: str) -> bool:
    """True when a payload exists. A Redis failure counts as a miss."""

    try:
        return bool(get_runtime().redis.client().exists(cache_key(end_user_id, language)))
    except Exception:
        logger.warning(
            "interest cache probe failed user=%s language=%s",
            end_user_id,
            language,
            exc_info=True,
        )
        return False


def write(end_user_id: str, language: str, items: list[dict[str, Any]]) -> None:
    """Store the payload under the profile service's TTL.

    A failure is logged and swallowed: the unit's generation already succeeded,
    and the next online request regenerates the cache anyway.
    """

    runtime = get_runtime()
    payload = {
        "data": items,
        "generated_at": datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "cached": True,
    }
    try:
        runtime.redis.client().set(
            cache_key(end_user_id, language),
            json.dumps(payload, ensure_ascii=False),
            ex=runtime.settings.interest_cache_expire_seconds,
        )
    except Exception:
        logger.warning(
            "interest cache write failed user=%s language=%s",
            end_user_id,
            language,
            exc_info=True,
        )

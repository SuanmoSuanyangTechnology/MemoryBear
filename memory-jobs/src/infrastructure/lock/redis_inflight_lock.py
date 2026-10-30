"""Redis implementation of the per-user in-flight lock.

Ownership rule: the per-user unit acquires the key with its attempt token; a
retried attempt that reuses its token re-enters the lock it already holds. The
sweep only reads the key and never takes it. A Redis outage degrades open in
both directions: losing a lock costs a duplicate generation, blocking the work
costs the user their refresh.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from redis import Redis

logger = logging.getLogger(__name__)

INFLIGHT_KEY_FMT = "insight_summary_cache:inflight:{end_user_id}"
INFLIGHT_TTL_SECONDS = 1800

#: Compare-and-delete: a read-then-delete pair can delete a lock that expired
#: and was retaken by another attempt between the two calls.
_RELEASE_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


def inflight_key(end_user_id: str) -> str:
    return INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)


class RedisInflightLock:
    def __init__(self, client_provider: Callable[[], Redis]) -> None:
        self._client_provider = client_provider

    def acquire(self, end_user_id: str, token: str) -> bool:
        if not token:
            # Outside the try: an empty token is a programming error, not a
            # Redis outage, so it must not degrade open.
            raise ValueError("inflight lock requires a non-empty token")
        try:
            client = self._client_provider()
            key = inflight_key(end_user_id)
            if client.set(key, token, nx=True, ex=INFLIGHT_TTL_SECONDS):
                return True
            return client.get(key) == token
        except Exception:  # pragma: no cover - degrade open rather than block work
            logger.warning("inflight lock unavailable user=%s", end_user_id, exc_info=True)
            return True

    def release(self, end_user_id: str, token: str) -> None:
        if not token:
            raise ValueError("inflight lock requires a non-empty token")
        try:
            client = self._client_provider()
            client.eval(_RELEASE_SCRIPT, 1, inflight_key(end_user_id), token)
        except Exception:  # pragma: no cover - TTL is the backstop
            logger.warning(
                "inflight lock release failed user=%s", end_user_id, exc_info=True
            )

    def is_locked(self, end_user_id: str) -> bool:
        try:
            return bool(self._client_provider().exists(inflight_key(end_user_id)))
        except Exception:  # pragma: no cover - degrade open rather than block the sweep
            logger.warning(
                "inflight lookup unavailable user=%s", end_user_id, exc_info=True
            )
            return False

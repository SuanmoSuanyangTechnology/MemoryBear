"""Per-user in-flight lock shared by the cache tasks and the sweep.

The API's scanner that used to share this namespace has been removed, so the
lock now only serialises this service's own units against each other.

Ownership rule: only the per-user unit (``generate``) writes this key, and it
writes its own task id. The sweep (``scan``) reads it and never takes it — a
scanner that acquired the lock would make the unit it just dispatched skip.
Producers (the profile service) submit and poll but must not take it either.
"""

from __future__ import annotations

import logging

from src.runtime import get_runtime

logger = logging.getLogger(__name__)

#: Shared with the API's periodic scanner.
INFLIGHT_KEY_FMT = "insight_summary_cache:inflight:{end_user_id}"
INFLIGHT_TTL_SECONDS = 1800


def inflight_key(end_user_id: str) -> str:
    return INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)


def acquire_inflight(end_user_id: str, task_id: str | None) -> bool:
    """Take the per-user lock, or confirm that we already hold it.

    A redelivered message keeps its original task id, so the value comparison
    lets that attempt continue instead of skipping forever until the TTL expires.
    A Redis outage degrades open: losing the lock costs a possible duplicate
    generation, while refusing to work costs the user's refresh.
    """

    value = task_id or "1"
    try:
        client = get_runtime().redis.client()
        key = inflight_key(end_user_id)
        if client.set(key, value, nx=True, ex=INFLIGHT_TTL_SECONDS):
            return True
        return client.get(key) == value
    except Exception:  # pragma: no cover - degrade open rather than block work
        logger.warning("inflight lock unavailable user=%s", end_user_id, exc_info=True)
        return True


def release_inflight(end_user_id: str, task_id: str | None) -> None:
    """Release the lock only when this attempt still owns it."""

    value = task_id or "1"
    try:
        client = get_runtime().redis.client()
        key = inflight_key(end_user_id)
        if client.get(key) == value:
            client.delete(key)
    except Exception:  # pragma: no cover - TTL is the backstop
        logger.warning("inflight lock release failed user=%s", end_user_id, exc_info=True)


def is_inflight(end_user_id: str) -> bool:
    """Whether someone already holds this user's refresh lock.

    Read-only on purpose: ``generate`` takes the lock keyed by its own task id
    and skips a key it does not own, so a sweep that acquired it would cancel
    the unit it just dispatched. A Redis outage degrades open — the unit's own
    lock still prevents duplicate LLM work.
    """

    try:
        return bool(get_runtime().redis.client().exists(inflight_key(end_user_id)))
    except Exception:  # pragma: no cover - degrade open rather than block the sweep
        logger.warning("inflight lookup unavailable user=%s", end_user_id, exc_info=True)
        return False

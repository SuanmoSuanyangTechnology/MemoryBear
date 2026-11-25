"""Synchronous invalidation of host-style cache key namespaces.

Port of the host ``invalidate_cache_sync``: ``cache:{prefix}:*`` (or an
explicit ``pattern``) is walked with ``SCAN`` and removed in ``UNLINK``
batches; an exact ``key`` is removed with ``DELETE``. Returns the number of
removed keys.
"""

from __future__ import annotations

from typing import Any

from src.redis import get_thread_safe_sync_redis

_DEFAULT_SCAN_BATCH_SIZE = 500


def _resolve_invalidation(
    prefix: str | None,
    key: str | None,
    pattern: str | None,
    batch_size: int,
) -> tuple[str, str]:
    if not isinstance(batch_size, int) or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")

    if key:
        return "key", key
    if pattern:
        return "pattern", pattern
    if prefix:
        return "pattern", f"cache:{prefix}:*"
    raise ValueError("one of key, prefix or pattern is required")


def _scan_unlink_sync(client: Any, pattern: str, batch_size: int) -> int:
    deleted = 0
    cursor = 0
    scan_hint = max(1, min(batch_size, _DEFAULT_SCAN_BATCH_SIZE))
    while True:
        cursor, keys = client.scan(cursor=cursor, match=pattern, count=scan_hint)
        for start in range(0, len(keys), batch_size):
            batch = keys[start : start + batch_size]
            if not batch:
                continue
            deleted += int(client.unlink(*batch) or 0)
        if cursor == 0:
            return deleted


def invalidate_cache_sync(
    prefix: str | None = None,
    *,
    key: str | None = None,
    pattern: str | None = None,
    batch_size: int = _DEFAULT_SCAN_BATCH_SIZE,
) -> int:
    kind, target = _resolve_invalidation(prefix, key, pattern, batch_size)
    client = get_thread_safe_sync_redis()

    if kind == "key":
        return int(client.delete(target) or 0)
    return _scan_unlink_sync(client, target, batch_size)


__all__ = ["invalidate_cache_sync"]

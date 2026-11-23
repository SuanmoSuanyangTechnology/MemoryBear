"""Metadata migration uses the same fail-closed fair lock as API reflection."""

from __future__ import annotations

import threading
from collections.abc import Callable

from redis import Redis
from task_bridge.redis_fair_lock import RedisFairLock


class MetadataWriteLock:
    def __init__(self, client_provider: Callable[[], Redis]) -> None:
        self._client_provider = client_provider
        self._local = threading.local()

    def acquire(self, end_user_id: str, token: str) -> bool:
        if not token:
            raise ValueError("metadata write lock requires an attempt token")
        lock = RedisFairLock(
            key=f"memory_write:{end_user_id}",
            redis_client=self._client_provider(),
            expire=600,
            timeout=1,
            retry_interval=0.1,
            auto_renewal=True,
        )
        if not lock.acquire():
            return False
        self._local.held = (end_user_id, token, lock)
        return True

    def ensure_valid(self) -> None:
        held = getattr(self._local, "held", None)
        if held is None:
            raise RuntimeError("metadata write lock is not held")
        lock = held[2]
        lock.ensure_valid()
        if lock.redis.get(lock.key) != lock.value:
            raise RuntimeError("metadata write lock ownership lost")

    def release(self, end_user_id: str, token: str) -> None:
        held = getattr(self._local, "held", None)
        if held is not None and held[:2] == (end_user_id, token):
            try:
                held[2].release()
            finally:
                del self._local.held

    def is_locked(self, end_user_id: str) -> bool:
        return bool(self._client_provider().exists(f"memory_write:{end_user_id}"))

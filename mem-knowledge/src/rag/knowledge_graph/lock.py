"""Redis lease guarding one knowledge graph mutation at a time."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_LOCK_TTL_SECONDS = 120
_LOCK_WAIT_SECONDS = 10 * 60
_LOCK_RENEW_INTERVAL_SECONDS = 40

_COMPARE_AND_DELETE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
""".strip()

_COMPARE_AND_EXPIRE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
""".strip()


class KnowledgeGraphLock:
    def __init__(
        self,
        redis: Any,
        knowledge_id: str,
        *,
        on_wait: Callable[[str, int], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._redis = redis
        self._knowledge_id = knowledge_id
        self._key = f"graphrag_task_{knowledge_id}"
        self._token = str(uuid.uuid4())
        self._stop = threading.Event()
        self._renew_thread: threading.Thread | None = None
        self._valid = False
        self._on_wait = on_wait
        self._clock = clock
        self._sleep = sleep

    def __enter__(self) -> KnowledgeGraphLock:
        started_at = self._clock()
        deadline = started_at + _LOCK_WAIT_SECONDS
        waiting_reported = False
        last_wait_report_seconds = 0.0
        while self._clock() < deadline:
            if self._redis.set(
                self._key,
                self._token,
                ex=_LOCK_TTL_SECONDS,
                nx=True,
            ):
                self._valid = True
                self._renew_thread = threading.Thread(
                    target=self._renew,
                    name="knowledge-graph-lock-renew",
                    daemon=True,
                )
                self._renew_thread.start()
                logger.info(
                    "[EvidenceGraph] lock_acquired kb_id=%s",
                    self._knowledge_id,
                )
                if waiting_reported and self._on_wait is not None:
                    self._on_wait(
                        "lock_acquired",
                        int((self._clock() - started_at) * 1000),
                    )
                return self
            waited_seconds = self._clock() - started_at
            if not waiting_reported:
                waiting_reported = True
                if self._on_wait is not None:
                    self._on_wait("lock_wait_started", 0)
            elif (
                self._on_wait is not None
                and waited_seconds >= 10
                and (
                    last_wait_report_seconds == 0
                    or waited_seconds - last_wait_report_seconds >= 30
                )
            ):
                self._on_wait("lock_waiting", int(waited_seconds * 1000))
                last_wait_report_seconds = waited_seconds
            self._sleep(1)
        raise TimeoutError("knowledge graph lock acquisition timed out")

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self._stop.set()
        thread = self._renew_thread
        if thread is not None:
            thread.join(timeout=5)
        released = bool(
            self._redis.eval(
                _COMPARE_AND_DELETE,
                1,
                self._key,
                self._token,
            )
        )
        self._valid = False
        logger.info(
            "[EvidenceGraph] lock_released kb_id=%s released=%s",
            self._knowledge_id,
            str(released).lower(),
        )

    def ensure_valid(self) -> None:
        if not self._valid:
            raise RuntimeError("knowledge graph lock lease is invalid")
        current = self._redis.get(self._key)
        if isinstance(current, bytes):
            current = current.decode()
        if current != self._token:
            self._valid = False
            raise RuntimeError("knowledge graph lock lease was lost")

    def _renew(self) -> None:
        while not self._stop.wait(_LOCK_RENEW_INTERVAL_SECONDS):
            try:
                renewed = self._redis.eval(
                    _COMPARE_AND_EXPIRE,
                    1,
                    self._key,
                    self._token,
                    _LOCK_TTL_SECONDS,
                )
            except Exception as exc:
                self._valid = False
                logger.warning(
                    "[EvidenceGraph] lock_renew_failed kb_id=%s error_type=%s",
                    self._knowledge_id,
                    type(exc).__name__,
                )
                return
            if not renewed:
                self._valid = False
                return


def create_knowledge_graph_lock(
    runtime: Any,
    knowledge_id: str,
    *,
    on_wait: Callable[[str, int], None] | None = None,
) -> KnowledgeGraphLock:
    return KnowledgeGraphLock(
        runtime.redis.sync_client(),
        knowledge_id,
        on_wait=on_wait,
    )


__all__ = ["KnowledgeGraphLock", "create_knowledge_graph_lock"]

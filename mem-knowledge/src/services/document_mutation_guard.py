"""Serialize Elasticsearch mutations and deletion for one document."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

DOCUMENT_MUTATION_LOCK_TTL_SECONDS = 120
DOCUMENT_MUTATION_LOCK_WAIT_SECONDS = 120
DOCUMENT_MUTATION_LOCK_RENEW_SECONDS = 30


def _lock_key(document_id: uuid.UUID) -> str:
    return f"doc:{document_id}:es_mutation"


class DocumentMutationLeaseLost(RuntimeError):
    """The current task no longer owns its document's ES mutation lease."""


class SyncDocumentMutationGuard:
    def __init__(self, lock: Any, lost: threading.Event) -> None:
        self._lock = lock
        self._lost = lost

    def ensure_owned(self) -> None:
        if self._lost.is_set():
            raise DocumentMutationLeaseLost("Document ES mutation lease was lost")
        try:
            owned = self._lock.owned()
        except RedisError as exc:
            self._lost.set()
            raise DocumentMutationLeaseLost("Document ES mutation ownership is unknown") from exc
        if not owned:
            raise DocumentMutationLeaseLost("Document ES mutation lease was lost")


class AsyncDocumentMutationGuard:
    def __init__(self, lock: Any, lost: asyncio.Event) -> None:
        self._lock = lock
        self._lost = lost

    async def ensure_owned(self) -> None:
        if self._lost.is_set():
            raise DocumentMutationLeaseLost("Document ES mutation lease was lost")
        try:
            owned = await self._lock.owned()
        except RedisError as exc:
            self._lost.set()
            raise DocumentMutationLeaseLost("Document ES mutation ownership is unknown") from exc
        if not owned:
            raise DocumentMutationLeaseLost("Document ES mutation lease was lost")


@contextmanager
def sync_document_mutation_guard(
    redis: Any,
    document_id: uuid.UUID,
) -> Iterator[SyncDocumentMutationGuard]:
    """Guard a short synchronous ES mutation; embedding stays outside."""

    lock = redis.lock(
        _lock_key(document_id),
        timeout=DOCUMENT_MUTATION_LOCK_TTL_SECONDS,
        blocking_timeout=DOCUMENT_MUTATION_LOCK_WAIT_SECONDS,
        thread_local=False,
    )
    if not lock.acquire():
        raise TimeoutError(f"Document ES mutation lease timed out: {document_id}")
    stop = threading.Event()
    lost = threading.Event()

    def renew() -> None:
        while not stop.wait(DOCUMENT_MUTATION_LOCK_RENEW_SECONDS):
            try:
                if not lock.extend(DOCUMENT_MUTATION_LOCK_TTL_SECONDS, replace_ttl=True):
                    lost.set()
                    return
            except Exception:
                lost.set()
                logger.warning(
                    "Document ES mutation lease renewal failed: document=%s", document_id
                )
                return

    thread = threading.Thread(target=renew, name="document-es-lease-renew", daemon=True)
    thread.start()
    guard = SyncDocumentMutationGuard(lock, lost)
    try:
        yield guard
        guard.ensure_owned()
    finally:
        stop.set()
        thread.join(timeout=5)
        try:
            if not lock.owned():
                raise DocumentMutationLeaseLost("Document ES mutation lease was lost at release")
            lock.release()
        except RedisError as exc:
            raise DocumentMutationLeaseLost("Document ES mutation lease release failed") from exc


@asynccontextmanager
async def async_document_mutation_guard(
    redis: Any,
    document_id: uuid.UUID,
) -> AsyncIterator[AsyncDocumentMutationGuard]:
    """Guard API deletion without blocking the request event loop."""

    lock = redis.lock(
        _lock_key(document_id),
        timeout=DOCUMENT_MUTATION_LOCK_TTL_SECONDS,
        blocking_timeout=DOCUMENT_MUTATION_LOCK_WAIT_SECONDS,
        thread_local=False,
    )
    if not await lock.acquire():
        raise TimeoutError(f"Document ES mutation lease timed out: {document_id}")
    stop = asyncio.Event()
    lost = asyncio.Event()

    async def renew() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=DOCUMENT_MUTATION_LOCK_RENEW_SECONDS)
                return
            except TimeoutError:
                pass
            try:
                if not await lock.extend(DOCUMENT_MUTATION_LOCK_TTL_SECONDS, replace_ttl=True):
                    lost.set()
                    return
            except Exception:
                lost.set()
                logger.warning(
                    "Document ES mutation lease renewal failed: document=%s", document_id
                )
                return

    task = asyncio.create_task(renew(), name="document-es-lease-renew")
    guard = AsyncDocumentMutationGuard(lock, lost)
    try:
        yield guard
        await guard.ensure_owned()
    finally:
        stop.set()
        await task
        try:
            if not await lock.owned():
                raise DocumentMutationLeaseLost("Document ES mutation lease was lost at release")
            await lock.release()
        except RedisError as exc:
            raise DocumentMutationLeaseLost("Document ES mutation lease release failed") from exc


__all__ = [
    "DocumentMutationLeaseLost",
    "async_document_mutation_guard",
    "sync_document_mutation_guard",
]

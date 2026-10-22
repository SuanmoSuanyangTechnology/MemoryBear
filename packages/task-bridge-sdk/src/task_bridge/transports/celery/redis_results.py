"""Async result reads straight from the Redis result backend.

Celery's result API is synchronous, so the bridge otherwise reads task state
in a worker thread. When the result backend is Redis and the optional
``redis`` package is installed, the same GET/MGET can run on
``redis.asyncio`` instead: no thread, no GIL hand-off, and cancellation
actually cancels the socket operation.

The wire format stays Celery's: key layout (including
``result_backend_transport_options.global_keyprefix``) comes from
``backend.get_key_for_task`` and payload decoding from
``backend.decode_result``, so a Celery format change remains Celery's concern.
Only ``redis://`` / ``rediss://`` URLs take this path; everything else falls
back to the backend call in a thread.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_PENDING_META: dict[str, Any] = {"status": "PENDING", "result": None}


class RedisResultReader:
    """Reads Celery task metas with an async Redis client."""

    def __init__(self, backend: Any, client: Any) -> None:
        self._backend = backend
        self._client = client
        self._notifier: RedisResultNotifier | None = None

    @classmethod
    def create(
        cls,
        backend: Any,
        url: str | None,
        *,
        socket_timeout: float | None,
        socket_connect_timeout: float | None,
        max_connections: int | None,
    ) -> RedisResultReader | None:
        """Return a reader, or ``None`` when this backend cannot use one."""

        if not url:
            return None
        if urlsplit(str(url)).scheme not in {"redis", "rediss"}:
            return None
        try:
            from celery.backends.redis import RedisBackend
        except ImportError:  # pragma: no cover - celery always ships it
            return None
        if not isinstance(backend, RedisBackend):
            return None
        try:
            from redis.asyncio import Redis
        except ImportError:
            logger.debug(
                "redis is not installed; task results stay on the thread-based path"
            )
            return None

        client = Redis.from_url(
            str(url),
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            max_connections=max_connections,
            decode_responses=False,
        )
        return cls(backend, client)

    async def ping(self) -> None:
        await self._client.ping()

    async def get_meta(self, task_id: str) -> Mapping[str, Any]:
        raw = await self._client.get(self._backend.get_key_for_task(task_id))
        return self._decode(raw)

    async def get_many_meta(
        self, task_ids: Sequence[str]
    ) -> dict[str, Mapping[str, Any]]:
        if not task_ids:
            return {}
        keys = [self._backend.get_key_for_task(task_id) for task_id in task_ids]
        raws = await self._client.mget(keys)
        return {
            task_id: self._decode(raw)
            for task_id, raw in zip(task_ids, raws, strict=True)
        }

    def notifier(self) -> RedisResultNotifier:
        """The shared pub/sub notifier, created on first use."""

        if self._notifier is None:
            self._notifier = RedisResultNotifier(
                self._client, self._backend.get_key_for_task
            )
        return self._notifier

    async def aclose(self) -> None:
        if self._notifier is not None:
            await self._notifier.aclose()
            self._notifier = None
        await self._client.aclose()

    def _decode(self, raw: Any) -> Mapping[str, Any]:
        if not raw:
            return _PENDING_META
        return self._backend.decode_result(raw)


class RedisResultNotifier:
    """Shared pub/sub listener that wakes waiters when a result is written.

    Celery's Redis backend publishes the encoded meta on the result key's
    channel in the same transaction as the ``SET``. One PubSub connection
    serves every waiter owned by this reader: tasks map to channels, and a
    channel is subscribed only while at least one waiter is registered.

    Pub/sub is a latency optimisation, never a correctness dependency. If the
    connection fails, :meth:`register` returns ``None`` and callers fall back
    to polling; a listener that loses its connection reconnects and
    re-subscribes whatever is still wanted.
    """

    def __init__(
        self,
        client: Any,
        channel_for_task: Callable[[str], str],
    ) -> None:
        self._client = client
        self._channel_for_task = channel_for_task
        self._lock = asyncio.Lock()
        self._events: dict[str, set[asyncio.Event]] = {}
        self._channel_to_task: dict[str, str] = {}
        self._pubsub: Any | None = None
        self._listener: asyncio.Task | None = None
        self._closed = False

    def _channel(self, task_id: str) -> str:
        # Celery's key_t may be bytes (the default backend does); Redis
        # PubSub reports channels as bytes too, so normalize once here.
        channel = self._channel_for_task(task_id)
        return channel.decode() if isinstance(channel, bytes) else channel

    async def register(self, task_id: str) -> asyncio.Event | None:
        """Subscribe to ``task_id``'s channel and return its wake-up event.

        Returns ``None`` when pub/sub is unavailable; the caller then polls.
        The subscription command is sent before this returns, so a result
        written afterwards wakes the waiter (a result written earlier is
        caught by the caller's first status read).
        """

        event = asyncio.Event()
        if self._closed:
            return None
        try:
            async with self._lock:
                events = self._events.setdefault(task_id, set())
                first = not events
                events.add(event)
                channel = self._channel(task_id)
                self._channel_to_task[channel] = task_id
                if self._pubsub is None:
                    await self._connect_locked()
                elif first:
                    await self._pubsub.subscribe(channel)
            return event
        except Exception:  # noqa: BLE001 - polling fallback
            logger.debug("Redis pub/sub wake unavailable for %s", task_id, exc_info=True)
            async with self._lock:
                events = self._events.get(task_id)
                if events is not None:
                    events.discard(event)
                    if not events:
                        del self._events[task_id]
                        self._channel_to_task.pop(self._channel(task_id), None)
            return None

    async def unregister(self, task_id: str, event: asyncio.Event) -> None:
        try:
            async with self._lock:
                events = self._events.get(task_id)
                if events is None:
                    return
                events.discard(event)
                if events:
                    return
                del self._events[task_id]
                channel = self._channel(task_id)
                self._channel_to_task.pop(channel, None)
                if self._pubsub is not None:
                    await self._pubsub.unsubscribe(channel)
        except Exception:  # noqa: BLE001 - listener retries the subscription state
            logger.debug("failed to release pub/sub channel for %s", task_id, exc_info=True)

    async def aclose(self) -> None:
        self._closed = True
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.cancel()
            try:
                await listener
            except asyncio.CancelledError:
                pass
        pubsub, self._pubsub = self._pubsub, None
        if pubsub is not None:
            try:
                await pubsub.aclose()
            except Exception:  # noqa: BLE001 - best-effort shutdown
                logger.debug("failed to close result pub/sub client", exc_info=True)
        # Wake anyone still waiting so they re-check via polling.
        for events in self._events.values():
            for event in events:
                event.set()
        self._events.clear()
        self._channel_to_task.clear()

    async def _connect_locked(self) -> None:
        if self._closed:
            return
        pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        self._pubsub = pubsub
        if self._channel_to_task:
            await pubsub.subscribe(*self._channel_to_task)
        if self._listener is None or self._listener.done():
            self._listener = asyncio.create_task(self._listen())

    async def _listen(self) -> None:
        while not self._closed:
            pubsub = self._pubsub
            if pubsub is None:
                # Retry for waiters that are still registered; otherwise idle
                # until the next register() triggers a connection.
                async with self._lock:
                    if (
                        self._pubsub is None
                        and self._channel_to_task
                        and not self._closed
                    ):
                        try:
                            await self._connect_locked()
                        except Exception:  # noqa: BLE001 - polling fallback
                            pass
                await asyncio.sleep(0.1)
                continue
            try:
                message = await pubsub.get_message(timeout=1.0)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reconnect, waiters keep polling
                if self._closed:
                    return
                logger.debug("Redis result pub/sub reconnecting", exc_info=True)
                async with self._lock:
                    if self._pubsub is pubsub:
                        self._pubsub = None
                        try:
                            await pubsub.aclose()
                        except Exception:  # noqa: BLE001
                            pass
                        if self._channel_to_task and not self._closed:
                            try:
                                await self._connect_locked()
                            except Exception:  # noqa: BLE001
                                pass
                await asyncio.sleep(0.1)
                continue

            if message is None:
                continue
            channel = message.get("channel")
            if isinstance(channel, bytes):
                try:
                    channel = channel.decode()
                except UnicodeDecodeError:
                    continue
            task_id = self._channel_to_task.get(channel)
            if task_id is None:
                continue
            for event in tuple(self._events.get(task_id, ())):
                event.set()


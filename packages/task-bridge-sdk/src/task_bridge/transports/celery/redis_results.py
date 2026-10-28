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


def _async_connection_class(url: str, sync_class: Any | None) -> Any:
    """Map Celery's sync connection class to the redis.asyncio equivalent."""

    from redis.asyncio import (
        Connection as AsyncConnection,
        SSLConnection as AsyncSSLConnection,
        UnixDomainSocketConnection as AsyncUnixConnection,
    )

    if isinstance(sync_class, type):
        try:
            import redis as sync_redis
        except ImportError:  # pragma: no cover - redis is already imported
            pass
        else:
            if issubclass(sync_class, sync_redis.SSLConnection):
                return AsyncSSLConnection
            if issubclass(sync_class, sync_redis.UnixDomainSocketConnection):
                return AsyncUnixConnection
            if issubclass(sync_class, sync_redis.Connection):
                return AsyncConnection

    scheme = urlsplit(str(url)).scheme
    if scheme == "rediss":
        return AsyncSSLConnection
    if scheme == "socket":
        return AsyncUnixConnection
    return AsyncConnection


def _async_pool_params(
    backend: Any,
    url: str,
    *,
    socket_timeout: float | None,
    socket_connect_timeout: float | None,
    max_connections: int | None,
) -> dict[str, Any] | None:
    """Mirror the Celery backend's resolved connection parameters.

    ``Redis.from_url`` only sees the URL: TLS settings supplied through
    ``redis_backend_use_ssl`` (certificate paths, ``ssl_cert_reqs``) or other
    Celery options would be lost. Celery resolves all of them into
    ``backend.connparams``, so reuse that and map the sync connection class
    to its async counterpart. ``None`` means the backend exposes no params;
    the caller then falls back to URL parsing.
    """

    connparams = getattr(backend, "connparams", None)
    if not isinstance(connparams, Mapping):
        return None

    params = dict(connparams)
    sync_class = params.pop("connection_class", None)
    params.pop("connection_pool", None)
    params["max_connections"] = max_connections
    params["socket_timeout"] = socket_timeout
    params["socket_connect_timeout"] = socket_connect_timeout
    params["decode_responses"] = False
    params["connection_class"] = _async_connection_class(url, sync_class)
    return params


class RedisResultReader:
    """Reads Celery task metas with an async Redis client.

    Status reads use ``client``; Pub/Sub uses a separate client of its own.
    A subscription holds its connection for the whole subscription, so sharing
    the read pool would starve reads when ``max_connections`` is small (at 1
    the first SUBSCRIBE would take the only slot).
    """

    def __init__(
        self,
        backend: Any,
        client: Any,
        *,
        pubsub_client: Any | None = None,
    ) -> None:
        self._backend = backend
        self._client = client
        # ``create`` always supplies a dedicated client; the fallback keeps
        # direct construction (tests, custom wiring) working.
        self._pubsub_client = pubsub_client
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
            from redis.asyncio import ConnectionPool, Redis
        except ImportError:
            logger.debug(
                "redis is not installed; task results stay on the thread-based path"
            )
            return None

        read_params = _async_pool_params(
            backend,
            str(url),
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
            max_connections=max_connections,
        )
        pubsub_params = _async_pool_params(
            backend,
            str(url),
            socket_timeout=None,
            socket_connect_timeout=socket_connect_timeout,
            max_connections=1,
        )
        if read_params is not None and pubsub_params is not None:
            # Mirror Celery's resolved parameters (TLS certificates, auth,
            # timeouts) instead of re-parsing the URL and losing app options.
            client = Redis(connection_pool=ConnectionPool(**read_params))
            pubsub_client = Redis(connection_pool=ConnectionPool(**pubsub_params))
        else:
            # A custom backend without connparams: URL parsing is all we have.
            client = Redis.from_url(
                str(url),
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                max_connections=max_connections,
                decode_responses=False,
            )
            pubsub_client = Redis.from_url(
                str(url),
                socket_timeout=None,
                socket_connect_timeout=socket_connect_timeout,
                max_connections=1,
                decode_responses=False,
            )
        return cls(backend, client, pubsub_client=pubsub_client)

    async def ping(self) -> None:
        await self._client.ping()

    async def warm_connections(self, connections: int) -> None:
        """Hold ``connections`` pooled status connections simultaneously.

        Concurrent pings can finish before every coroutine acquires a
        connection; taking them from the pool directly guarantees the count.
        """

        pool = self._client.connection_pool
        max_connections = getattr(pool, "max_connections", None)
        if max_connections is not None and connections > max_connections:
            raise ValueError(
                f"cannot warm {connections} connections: the result backend "
                f"pool's max_connections is {max_connections}"
            )
        held: list[Any] = []
        try:
            for _ in range(connections):
                held.append(await pool.get_connection("PING"))
        finally:
            for connection in held:
                try:
                    await pool.release(connection)
                except Exception:  # noqa: BLE001 - the connection is being dropped
                    logger.debug("failed to release warm-up connection", exc_info=True)

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
                self._pubsub_client or self._client,
                self._backend.get_key_for_task,
            )
        return self._notifier

    async def aclose(self) -> None:
        if self._notifier is not None:
            await self._notifier.aclose()
            self._notifier = None
        if self._pubsub_client is not None:
            await self._pubsub_client.aclose()
            self._pubsub_client = None
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

        State is committed only after SUBSCRIBE returns. A cancellation while
        connecting therefore leaves no event, channel mapping or subscription
        behind; ``CancelledError`` propagates without needing a ``finally`` in
        the caller.
        """

        event = asyncio.Event()
        if self._closed:
            return None
        channel = self._channel(task_id)
        try:
            async with self._lock:
                already = bool(self._events.get(task_id))
                if self._pubsub is None:
                    await self._connect_locked(extra_channel=channel)
                elif not already:
                    await self._pubsub.subscribe(channel)
                    # A previous listener may have exited without resetting
                    # _pubsub; make sure something is reading the connection.
                    self._ensure_listener_locked()
                # Commit only after every await point above: nothing to roll
                # back if the task is cancelled before this line.
                self._events.setdefault(task_id, set()).add(event)
                self._channel_to_task[channel] = task_id
            return event
        except Exception:  # noqa: BLE001 - polling fallback
            logger.debug("Redis pub/sub wake unavailable for %s", task_id, exc_info=True)
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

    async def _connect_locked(self, *, extra_channel: str | None = None) -> None:
        if self._closed:
            return
        pubsub = self._client.pubsub(ignore_subscribe_messages=True)
        channels = set(self._channel_to_task)
        if extra_channel is not None:
            channels.add(extra_channel)
        try:
            if channels:
                await pubsub.subscribe(*channels)
        except BaseException:
            # Includes CancelledError: do not publish a half-initialized
            # PubSub or leave its connection open. The close is shielded so a
            # cancellation does not interrupt it; the original error is
            # re-raised either way.
            cleanup = asyncio.create_task(pubsub.aclose())
            try:
                await asyncio.shield(cleanup)
            except BaseException:  # noqa: BLE001 - preserve the original error
                pass
            raise
        self._pubsub = pubsub
        self._ensure_listener_locked()

    def _ensure_listener_locked(self) -> None:
        if self._pubsub is None or self._closed:
            return
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


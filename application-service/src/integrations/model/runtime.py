"""Process-level lifecycle for the model service adapter."""

from __future__ import annotations

import asyncio
import inspect
import logging
import weakref
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from ...config import current_settings

from .client import ModelServiceClient, ModelServiceSyncClient
from .errors import ModelServiceConfigurationError
from .invoke import (
    ModelInvokeClient,
    ModelInvokeSyncClient,
    invoke_media_timeouts,
)

ClientFactory = Callable[[], Any]

logger = logging.getLogger(__name__)


async def _close_quietly(client: Any | None) -> None:
    if client is None:
        return
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close is None:
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        # Closing pools across loops (leftover foreign-loop pools in workers) can fail:
        # log only during teardown, never raise
        logger.warning(
            "model_integration_close_failed client=%s error=%s", type(client).__name__, exc
        )


def _validated_base_url() -> None:
    parsed = urlparse(current_settings().model_service_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelServiceConfigurationError(
            "MODEL_SERVICE_BASE_URL must be an absolute HTTP(S) URL"
        )
    if parsed.query or parsed.fragment:
        raise ModelServiceConfigurationError(
            "MODEL_SERVICE_BASE_URL must not contain query or fragment"
        )


class ModelIntegrationRuntime:
    """Always-on runtime: no toggle, no local fallback, loud failure when uninitialized (fail-fast).

    The two management-plane pools are built explicitly by ``start`` (API lifespan);
    runtime-plane pools are **lazily self-built** -- Celery workers have no lifespan,
    so the first runtime-plane call validates configuration and builds the pool
    (idempotent), while management-plane access still fails loudly inside workers.
    Runtime-plane pools: llm-profile async/sync + media-profile async/sync (the media
    profile has a different idle timeout; see the config notes).

    Async runtime-plane channels are cached per **event loop**: an httpx
    ``AsyncClient``'s connections are bound to the loop that created it, and reuse
    after a worker switches loops raises "Event loop is closed". Sync twins are
    process-level singletons (an httpx ``Client`` can be shared across threads/loops;
    management-plane ``def`` routes run on the anyio thread pool).
    """

    def __init__(
        self,
        client_factory: ClientFactory | None = None,
        sync_client_factory: ClientFactory | None = None,
        invoke_client_factory: ClientFactory | None = None,
        invoke_sync_client_factory: ClientFactory | None = None,
        media_invoke_client_factory: ClientFactory | None = None,
        media_invoke_sync_client_factory: ClientFactory | None = None,
    ):
        self._client_factory = client_factory
        self._sync_client_factory = sync_client_factory
        self._invoke_client_factory = invoke_client_factory
        self._invoke_sync_client_factory = invoke_sync_client_factory
        self._media_invoke_client_factory = media_invoke_client_factory
        self._media_invoke_sync_client_factory = media_invoke_sync_client_factory
        self._client: Any | None = None
        self._sync_client: Any | None = None
        self._invoke_sync_client: Any | None = None
        self._invoke_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any] = (
            weakref.WeakKeyDictionary()
        )
        self._media_invoke_sync_client: Any | None = None
        self._media_invoke_clients: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, Any
        ] = weakref.WeakKeyDictionary()

    @property
    def client(self) -> ModelServiceClient:
        if self._client is None:
            raise RuntimeError("Model integration is not initialized")
        return self._client

    @property
    def sync_client(self) -> ModelServiceSyncClient:
        if self._sync_client is None:
            raise RuntimeError("Model integration is not initialized")
        return self._sync_client

    @property
    def invoke_client(self) -> ModelInvokeClient:
        """Runtime-plane async channel on the current event loop (pool built on first use for that loop)."""

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("Model invoke client requires a running event loop") from exc
        return self.invoke_client_for(loop)

    @property
    def invoke_sync_client(self) -> ModelInvokeSyncClient:
        """Runtime-plane sync twin (process-level singleton; pool built at the first sync call site)."""

        if self._invoke_sync_client is None:
            _validated_base_url()
            factory = self._invoke_sync_client_factory or _default_invoke_sync_client_factory
            self._invoke_sync_client = factory()
        return self._invoke_sync_client

    def invoke_client_for(self, loop: asyncio.AbstractEventLoop) -> ModelInvokeClient:
        client = self._invoke_clients.get(loop)
        if client is not None:
            return client
        _validated_base_url()
        self._prune_closed_loops()
        factory = self._invoke_client_factory or _default_invoke_client_factory
        client = factory()
        self._invoke_clients[loop] = client
        return client

    @property
    def media_invoke_client(self) -> ModelInvokeClient:
        """Media-family async channel (media-profile timeouts: no frames for the whole media operation).

        Media calls block service-side until the task completes, so idle semantics
        differ from the llm profile (see the timeout-chain notes in the config);
        hence a dedicated pool -- sharing the llm pool would leak media-profile
        timeouts into long-form generation.
        """

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("Model media invoke client requires a running event loop") from exc
        return self.media_invoke_client_for(loop)

    @property
    def media_invoke_sync_client(self) -> ModelInvokeSyncClient:
        """Media-family sync twin (the sync celery chain: parse_document etc. in tasks.py)."""

        if self._media_invoke_sync_client is None:
            _validated_base_url()
            factory = (
                self._media_invoke_sync_client_factory
                or _default_media_invoke_sync_client_factory
            )
            self._media_invoke_sync_client = factory()
        return self._media_invoke_sync_client

    def media_invoke_client_for(
        self, loop: asyncio.AbstractEventLoop
    ) -> ModelInvokeClient:
        client = self._media_invoke_clients.get(loop)
        if client is not None:
            return client
        _validated_base_url()
        self._prune_closed_loops()
        factory = self._media_invoke_client_factory or _default_media_invoke_client_factory
        client = factory()
        self._media_invoke_clients[loop] = client
        return client

    def _prune_closed_loops(self) -> None:
        """Drop pools bound to closed loops: their connections die with the loop and cannot be closed safely, so release the reference to prevent leaks."""

        for registry in (self._invoke_clients, self._media_invoke_clients):
            for loop in [item for item in registry if item.is_closed()]:
                registry.pop(loop, None)

    async def start(self) -> None:
        await self.close()
        _validated_base_url()
        factory = self._client_factory or _default_client_factory
        sync_factory = self._sync_client_factory or _default_sync_client_factory
        # Build both management-plane pools here (sync routes and premium orchestration
        # become usable right away); connections are created on first request
        client = factory()
        sync_client = sync_factory()
        self._client = client
        self._sync_client = sync_client

    async def close(self) -> None:
        client, sync_client = self._client, self._sync_client
        invoke_sync_client = self._invoke_sync_client
        invoke_clients = list(self._invoke_clients.values())
        media_invoke_sync_client = self._media_invoke_sync_client
        media_invoke_clients = list(self._media_invoke_clients.values())
        self._client = self._sync_client = self._invoke_sync_client = None
        self._media_invoke_sync_client = None
        self._invoke_clients = weakref.WeakKeyDictionary()
        self._media_invoke_clients = weakref.WeakKeyDictionary()
        await _close_quietly(client)
        await _close_quietly(sync_client)
        await _close_quietly(invoke_sync_client)
        await _close_quietly(media_invoke_sync_client)
        for invoke_client in invoke_clients:
            await _close_quietly(invoke_client)
        for media_invoke_client in media_invoke_clients:
            await _close_quietly(media_invoke_client)


def _default_client_factory() -> ModelServiceClient:
    return ModelServiceClient.from_settings(current_settings())


def _default_sync_client_factory() -> ModelServiceSyncClient:
    return ModelServiceSyncClient.from_settings(current_settings())


def _default_invoke_client_factory() -> ModelInvokeClient:
    return ModelInvokeClient.from_settings(current_settings())


def _default_invoke_sync_client_factory() -> ModelInvokeSyncClient:
    return ModelInvokeSyncClient.from_settings(current_settings())


def _default_media_invoke_client_factory() -> ModelInvokeClient:
    return ModelInvokeClient.from_settings(current_settings(), timeouts=invoke_media_timeouts(current_settings()))


def _default_media_invoke_sync_client_factory() -> ModelInvokeSyncClient:
    return ModelInvokeSyncClient.from_settings(
        current_settings(), timeouts=invoke_media_timeouts(current_settings())
    )


_runtime = ModelIntegrationRuntime()


async def initialize_model_integration() -> None:
    await _runtime.start()


async def close_model_integration() -> None:
    await _runtime.close()


def get_model_service_client() -> ModelServiceClient:
    return _runtime.client


def get_model_service_sync_client() -> ModelServiceSyncClient:
    return _runtime.sync_client


def get_model_invoke_client() -> ModelInvokeClient:
    return _runtime.invoke_client


def get_model_invoke_sync_client() -> ModelInvokeSyncClient:
    return _runtime.invoke_sync_client


def get_model_media_invoke_client() -> ModelInvokeClient:
    return _runtime.media_invoke_client


def get_model_media_invoke_sync_client() -> ModelInvokeSyncClient:
    return _runtime.media_invoke_sync_client

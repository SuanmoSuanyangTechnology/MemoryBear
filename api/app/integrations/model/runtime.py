"""Process-level lifecycle for the model service adapter."""

from __future__ import annotations

import asyncio
import inspect
import logging
import weakref
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings

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
        # 跨循环关池（worker 里残留的异循环池）会失败：收尾阶段只记不抛
        logger.warning(
            "model_integration_close_failed client=%s error=%s", type(client).__name__, exc
        )


def _validated_base_url() -> None:
    parsed = urlparse(settings.MODEL_SERVICE_BASE_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelServiceConfigurationError(
            "MODEL_SERVICE_BASE_URL must be an absolute HTTP(S) URL"
        )
    if parsed.query or parsed.fragment:
        raise ModelServiceConfigurationError(
            "MODEL_SERVICE_BASE_URL must not contain query or fragment"
        )


class ModelIntegrationRuntime:
    """Always-on runtime: 无开关、无本地回退，未初始化即响亮失败（D2 fail-fast）。

    管理面两池由 ``start``（API lifespan）显式建；运行面各池**按需自建**——Celery worker 没有
    lifespan，运行面调用点首次使用时校验配置并建池（幂等），管理面在 worker 里照旧响亮失败。
    运行面池：llm 档异步/同步 + 媒体档异步/同步（媒体档 idle 与 llm 档不同，见配置注释）。

    运行面异步通道按**事件循环**亲和缓存：httpx ``AsyncClient`` 的连接绑在创建它的循环上，
    worker 换循环后复用会报 "Event loop is closed"。同步孪生则是进程级单例（httpx ``Client``
    可跨线程/循环共享，管理面 ``def`` 路由跑在 anyio 线程池）。
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
        """当前事件循环上的运行面异步通道（该循环首次使用时建池）。"""

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("Model invoke client requires a running event loop") from exc
        return self.invoke_client_for(loop)

    @property
    def invoke_sync_client(self) -> ModelInvokeSyncClient:
        """运行面同步孪生（进程级单例，首个同步调用点建池）。"""

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
        """媒体族异步通道（媒体档超时：整段媒体操作期间无帧）。

        媒体调用在服务侧阻塞至任务完成，idle 语义与 llm 档不同（见配置注释超时链），
        故独立成池：与 llm 档混用同一池会把媒体档超时带给长文生成。
        """

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("Model media invoke client requires a running event loop") from exc
        return self.media_invoke_client_for(loop)

    @property
    def media_invoke_sync_client(self) -> ModelInvokeSyncClient:
        """媒体族同步孪生（sync celery 链：tasks.py parse_document 等）。"""

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
        """丢弃已关闭循环上的池：其连接随循环失效且无法再安全关闭，只能解除引用防泄漏。"""

        for registry in (self._invoke_clients, self._media_invoke_clients):
            for loop in [item for item in registry if item.is_closed()]:
                registry.pop(loop, None)

    async def start(self) -> None:
        await self.close()
        _validated_base_url()
        factory = self._client_factory or _default_client_factory
        sync_factory = self._sync_client_factory or _default_sync_client_factory
        # 管理面两池在此建好（sync 路由与 premium 编排随即可用）；首次请求才建连接
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
    return ModelServiceClient.from_settings(settings)


def _default_sync_client_factory() -> ModelServiceSyncClient:
    return ModelServiceSyncClient.from_settings(settings)


def _default_invoke_client_factory() -> ModelInvokeClient:
    return ModelInvokeClient.from_settings(settings)


def _default_invoke_sync_client_factory() -> ModelInvokeSyncClient:
    return ModelInvokeSyncClient.from_settings(settings)


def _default_media_invoke_client_factory() -> ModelInvokeClient:
    return ModelInvokeClient.from_settings(settings, timeouts=invoke_media_timeouts(settings))


def _default_media_invoke_sync_client_factory() -> ModelInvokeSyncClient:
    return ModelInvokeSyncClient.from_settings(
        settings, timeouts=invoke_media_timeouts(settings)
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

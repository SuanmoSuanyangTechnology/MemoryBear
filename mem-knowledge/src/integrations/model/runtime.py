"""km 进程级 invoke 客户端生命周期（镜像宿主 ``app/integrations/model/runtime.py`` 减管理面）。

无开关、无本地回退：调用点首次使用时校验配置并建池（幂等），未配置即响亮失败（fail-fast）。
四个槽：llm 档异步/同步 + 媒体档异步/同步（媒体档 idle 与 llm 档不同，见配置注释超时链）。

异步通道按**事件循环**亲和缓存：httpx ``AsyncClient`` 的连接绑在创建它的循环上，worker 换
循环后复用会报 "Event loop is closed"。同步孪生则是进程级单例（httpx ``Client`` 可跨线程/
循环共享）。celery prefork 子进程经 :meth:`ModelInvokeRuntime.reset_after_fork` 弃置父进程槽。

与宿主底本的差别：无管理面两池、无模块级全局（``ProcessRuntime`` 显式持有本对象并把
settings 传入构造）。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import weakref
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from ...config import KnowledgeSettings
from .errors import ModelInvokeConfigurationError
from .invoke import (
    ModelInvokeClient,
    ModelInvokeSyncClient,
    invoke_media_timeouts,
)

logger = logging.getLogger(__name__)

ClientFactory = Callable[[], Any]


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
    except Exception as exc:  # noqa: BLE001 - 收尾阶段只记不抛（跨循环关池会失败）
        logger.warning(
            "model_invoke_close_failed client=%s error=%s", type(client).__name__, exc
        )


def _validated_base_url(settings: KnowledgeSettings) -> None:
    parsed = urlparse(settings.model_service_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelInvokeConfigurationError(
            "MODEL_SERVICE_BASE_URL must be an absolute HTTP(S) URL"
        )
    if parsed.query or parsed.fragment:
        raise ModelInvokeConfigurationError(
            "MODEL_SERVICE_BASE_URL must not contain query or fragment"
        )


class ModelInvokeRuntime:
    """运行面池槽：llm 档 / 媒体档 × 异步（按循环）/ 同步（进程单例）。"""

    def __init__(
        self,
        settings: KnowledgeSettings,
        invoke_client_factory: ClientFactory | None = None,
        invoke_sync_client_factory: ClientFactory | None = None,
        media_invoke_client_factory: ClientFactory | None = None,
        media_invoke_sync_client_factory: ClientFactory | None = None,
    ):
        self._settings = settings
        self._invoke_client_factory = invoke_client_factory
        self._invoke_sync_client_factory = invoke_sync_client_factory
        self._media_invoke_client_factory = media_invoke_client_factory
        self._media_invoke_sync_client_factory = media_invoke_sync_client_factory
        self._invoke_sync_client: Any | None = None
        self._invoke_clients: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, Any] = (
            weakref.WeakKeyDictionary()
        )
        self._media_invoke_sync_client: Any | None = None
        self._media_invoke_clients: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, Any
        ] = weakref.WeakKeyDictionary()

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
            _validated_base_url(self._settings)
            factory = (
                self._invoke_sync_client_factory or self._default_invoke_sync_client
            )
            self._invoke_sync_client = factory()
        return self._invoke_sync_client

    def invoke_client_for(self, loop: asyncio.AbstractEventLoop) -> ModelInvokeClient:
        client = self._invoke_clients.get(loop)
        if client is not None:
            return client
        _validated_base_url(self._settings)
        self._prune_closed_loops()
        factory = self._invoke_client_factory or self._default_invoke_client
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
            raise RuntimeError(
                "Model media invoke client requires a running event loop"
            ) from exc
        return self.media_invoke_client_for(loop)

    @property
    def media_invoke_sync_client(self) -> ModelInvokeSyncClient:
        """媒体族同步孪生（sync celery 链：parse_document / qa_import）。"""

        if self._media_invoke_sync_client is None:
            _validated_base_url(self._settings)
            factory = (
                self._media_invoke_sync_client_factory
                or self._default_media_invoke_sync_client
            )
            self._media_invoke_sync_client = factory()
        return self._media_invoke_sync_client

    def media_invoke_client_for(
        self, loop: asyncio.AbstractEventLoop
    ) -> ModelInvokeClient:
        client = self._media_invoke_clients.get(loop)
        if client is not None:
            return client
        _validated_base_url(self._settings)
        self._prune_closed_loops()
        factory = self._media_invoke_client_factory or self._default_media_invoke_client
        client = factory()
        self._media_invoke_clients[loop] = client
        return client

    def _prune_closed_loops(self) -> None:
        """丢弃已关闭循环上的池：其连接随循环失效且无法再安全关闭，只能解除引用防泄漏。"""

        for registry in (self._invoke_clients, self._media_invoke_clients):
            for loop in [item for item in registry if item.is_closed()]:
                registry.pop(loop, None)

    def _default_invoke_client(self) -> ModelInvokeClient:
        return ModelInvokeClient.from_settings(self._settings)

    def _default_invoke_sync_client(self) -> ModelInvokeSyncClient:
        return ModelInvokeSyncClient.from_settings(self._settings)

    def _default_media_invoke_client(self) -> ModelInvokeClient:
        return ModelInvokeClient.from_settings(
            self._settings, timeouts=invoke_media_timeouts(self._settings)
        )

    def _default_media_invoke_sync_client(self) -> ModelInvokeSyncClient:
        return ModelInvokeSyncClient.from_settings(
            self._settings, timeouts=invoke_media_timeouts(self._settings)
        )

    async def aclose(self) -> None:
        invoke_sync_client = self._invoke_sync_client
        invoke_clients = list(self._invoke_clients.values())
        media_invoke_sync_client = self._media_invoke_sync_client
        media_invoke_clients = list(self._media_invoke_clients.values())
        self._invoke_sync_client = None
        self._media_invoke_sync_client = None
        self._invoke_clients = weakref.WeakKeyDictionary()
        self._media_invoke_clients = weakref.WeakKeyDictionary()
        await _close_quietly(invoke_sync_client)
        await _close_quietly(media_invoke_sync_client)
        for invoke_client in invoke_clients:
            await _close_quietly(invoke_client)
        for media_invoke_client in media_invoke_clients:
            await _close_quietly(media_invoke_client)

    def reset_after_fork(self) -> None:
        """fork 子进程弃置父进程槽：父 sockets 在子进程不可用（且关闭会伤及父进程）。"""

        self._invoke_sync_client = None
        self._media_invoke_sync_client = None
        self._invoke_clients = weakref.WeakKeyDictionary()
        self._media_invoke_clients = weakref.WeakKeyDictionary()


__all__ = ["ModelInvokeRuntime"]

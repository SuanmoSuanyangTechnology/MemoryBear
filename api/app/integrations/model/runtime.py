"""Process-level lifecycle for the model service adapter."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings

from .client import ModelServiceClient, ModelServiceSyncClient
from .errors import ModelServiceConfigurationError

ClientFactory = Callable[[], Any]


async def _close_quietly(client: Any | None) -> None:
    if client is None:
        return
    close = getattr(client, "aclose", None) or getattr(client, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


class ModelIntegrationRuntime:
    """Always-on runtime: 无开关、无本地回退，未初始化即响亮失败（D2 fail-fast）。"""

    def __init__(
        self,
        client_factory: ClientFactory | None = None,
        sync_client_factory: ClientFactory | None = None,
    ):
        self._client_factory = client_factory
        self._sync_client_factory = sync_client_factory
        self._client: Any | None = None
        self._sync_client: Any | None = None

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

    async def start(self) -> None:
        await self.close()
        parsed = urlparse(settings.MODEL_SERVICE_BASE_URL)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ModelServiceConfigurationError(
                "MODEL_SERVICE_BASE_URL must be an absolute HTTP(S) URL"
            )
        if parsed.query or parsed.fragment:
            raise ModelServiceConfigurationError(
                "MODEL_SERVICE_BASE_URL must not contain query or fragment"
            )
        factory = self._client_factory or _default_client_factory
        sync_factory = self._sync_client_factory or _default_sync_client_factory
        # 同步池在此建好（管理面 sync 路由随即可用）；首次请求才建连接
        self._client = factory()
        self._sync_client = sync_factory()

    async def close(self) -> None:
        client, sync_client = self._client, self._sync_client
        self._client = self._sync_client = None
        await _close_quietly(client)
        await _close_quietly(sync_client)


def _default_client_factory() -> ModelServiceClient:
    return ModelServiceClient.from_settings(settings)


def _default_sync_client_factory() -> ModelServiceSyncClient:
    return ModelServiceSyncClient.from_settings(settings)


_runtime = ModelIntegrationRuntime()


async def initialize_model_integration() -> None:
    await _runtime.start()


async def close_model_integration() -> None:
    await _runtime.close()


def get_model_service_client() -> ModelServiceClient:
    return _runtime.client


def get_model_service_sync_client() -> ModelServiceSyncClient:
    return _runtime.sync_client

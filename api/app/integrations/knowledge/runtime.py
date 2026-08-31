"""Process-level selection and lifecycle for knowledge adapters."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings

from .contracts import KnowledgeConfigurationError
from .legacy_retriever import LegacyKnowledgeRetriever
from .retriever import KnowledgeRetriever
from .route_proxy import KnowledgeRouteProxy

RemoteFactory = Callable[[], Any]


class KnowledgeIntegrationRuntime:
    def __init__(self, remote_factory: RemoteFactory | None = None):
        self._remote_factory = remote_factory
        self._remote: Any | None = None
        self._retriever: KnowledgeRetriever | None = None
        self._route_proxy: KnowledgeRouteProxy | None = None
        self._enabled = False

    @property
    def retriever(self) -> KnowledgeRetriever:
        if self._retriever is None:
            raise RuntimeError("Knowledge integration is not initialized")
        return self._retriever

    @property
    def route_proxy(self) -> KnowledgeRouteProxy | None:
        return self._route_proxy

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def start(self, *, enabled: bool, base_url: str) -> None:
        await self.close()
        self._enabled = bool(enabled)
        if not self._enabled:
            self._retriever = LegacyKnowledgeRetriever()
            return

        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise KnowledgeConfigurationError(
                "MEM_KNOWLEDGE_BASE_URL must be an absolute HTTP(S) URL"
            )
        if parsed.query or parsed.fragment:
            raise KnowledgeConfigurationError(
                "MEM_KNOWLEDGE_BASE_URL must not contain query or fragment"
            )
        factory = self._remote_factory or _default_remote_factory
        remote = factory()
        self._remote = remote
        self._retriever = remote
        self._route_proxy = remote

    async def close(self) -> None:
        remote = self._remote
        self._remote = None
        self._retriever = None
        self._route_proxy = None
        self._enabled = False
        close = getattr(remote, "aclose", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result

    async def ready(self) -> bool:
        if not self._enabled:
            return True
        ready = getattr(self._remote, "ready", None)
        if ready is None:
            return False
        result = ready()
        return bool(await result) if inspect.isawaitable(result) else bool(result)


def _default_remote_factory() -> Any:
    from .client import KnowledgeServiceClient

    return KnowledgeServiceClient.from_settings(settings)


_runtime = KnowledgeIntegrationRuntime()


async def initialize_knowledge_integration() -> None:
    await _runtime.start(
        enabled=settings.ENABLE_MEM_KNOWLEDGE,
        base_url=settings.MEM_KNOWLEDGE_BASE_URL,
    )


async def close_knowledge_integration() -> None:
    await _runtime.close()


def get_knowledge_retriever() -> KnowledgeRetriever:
    return _runtime.retriever


def get_knowledge_route_proxy() -> KnowledgeRouteProxy | None:
    return _runtime.route_proxy


async def is_remote_knowledge_ready() -> bool:
    return await _runtime.ready()

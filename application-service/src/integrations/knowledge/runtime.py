"""Process-level selection and lifecycle for knowledge adapters."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from ...config import current_settings

from .contracts import KnowledgeConfigurationError
from .legacy_retriever import LegacyKnowledgeRetriever
from .retriever import KnowledgeRetriever
from .route_proxy import KnowledgeRouteProxy

if TYPE_CHECKING:
    from .client import KnowledgeServiceClient

logger = logging.getLogger(__name__)
RemoteFactory = Callable[[], Any]
GrpcRetrieverFactory = Callable[[Any], Any]
GrpcPoolFactory = Callable[[], Any]


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
    except Exception:
        logger.warning(
            "knowledge_integration_close_failed client=%s",
            type(client).__name__,
            exc_info=True,
        )


class KnowledgeIntegrationRuntime:
    def __init__(
        self,
        remote_factory: RemoteFactory | None = None,
        *,
        grpc_retriever_factory: GrpcRetrieverFactory | None = None,
        grpc_pool_factory: GrpcPoolFactory | None = None,
    ):
        self._remote_factory = remote_factory
        self._grpc_retriever_factory = grpc_retriever_factory
        self._grpc_pool_factory = grpc_pool_factory
        self._remote: Any | None = None
        self._retriever: KnowledgeRetriever | None = None
        self._route_proxy: KnowledgeRouteProxy | None = None
        self._grpc_pool: Any | None = None
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
        # Route forwarding, detached calls and readiness stay on the HTTP
        # client; only the retrieval face can switch protocol.
        self._route_proxy = remote
        if current_settings().mem_knowledge_protocol == "grpc":
            pool_factory = self._grpc_pool_factory or _default_grpc_pool_factory
            pool = pool_factory()
            self._grpc_pool = pool
            retriever_factory = (
                self._grpc_retriever_factory or _default_grpc_retriever_factory
            )
            self._retriever = retriever_factory(pool)
        else:
            self._retriever = remote

    async def close(self) -> None:
        remote = self._remote
        retriever = self._retriever
        pool = self._grpc_pool
        self._remote = None
        self._retriever = None
        self._route_proxy = None
        self._grpc_pool = None
        self._enabled = False
        if retriever is not remote:
            await _close_quietly(retriever)
        await _close_quietly(remote)
        await _close_quietly(pool)

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

    return KnowledgeServiceClient.from_settings(current_settings())


def _default_grpc_pool_factory() -> Any:
    from memorybear_grpc.client import ChannelPool

    settings = current_settings()
    return ChannelPool(
        max_message_bytes=settings.grpc_max_message_bytes,
        keepalive_time_ms=settings.grpc_keepalive_time_ms,
        keepalive_timeout_ms=settings.grpc_keepalive_timeout_ms,
    )


def _default_grpc_retriever_factory(pool: Any) -> Any:
    from .grpc_client import KnowledgeGrpcRetriever

    settings = current_settings()
    return KnowledgeGrpcRetriever(
        pool,
        target=settings.mem_knowledge_grpc_target,
        timeout_s=settings.mem_knowledge_grpc_timeout_seconds,
        margin_s=settings.mem_knowledge_grpc_margin_seconds,
    )


_runtime = KnowledgeIntegrationRuntime()


async def initialize_knowledge_integration() -> None:
    await _runtime.start(
        enabled=current_settings().enable_mem_knowledge,
        base_url=current_settings().mem_knowledge_base_url,
    )


async def close_knowledge_integration() -> None:
    await _runtime.close()


def get_knowledge_retriever() -> KnowledgeRetriever:
    return _runtime.retriever


def get_knowledge_route_proxy() -> KnowledgeRouteProxy | None:
    return _runtime.route_proxy


def get_knowledge_service_client() -> "KnowledgeServiceClient":
    """Detached client for in-process callers without an inbound Request.

    Raises KnowledgeConfigurationError when the remote knowledge integration
    is disabled, so callers fail fast instead of silently falling back to the
    legacy in-process controllers.
    """

    from .client import KnowledgeServiceClient

    proxy = _runtime.route_proxy
    # Deliberately an isinstance check on the concrete client rather than
    # ``_runtime.enabled`` or the KnowledgeRouteProxy protocol: call_internal()
    # exists only on KnowledgeServiceClient, so an "enabled" runtime holding
    # some other proxy implementation would fail later with AttributeError.
    # Tests injecting a fake via ``remote_factory`` should use
    # ``MagicMock(spec=KnowledgeServiceClient)`` (passes isinstance) or a subclass.
    if not isinstance(proxy, KnowledgeServiceClient):
        raise KnowledgeConfigurationError(
            "Remote knowledge service integration is not enabled"
        )
    return proxy


async def is_remote_knowledge_ready() -> bool:
    return await _runtime.ready()

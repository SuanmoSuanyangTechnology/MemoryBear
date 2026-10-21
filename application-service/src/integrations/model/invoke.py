"""Host invoke channel: in-package frame transport + host connection pool / attribution / error vocabulary.

The service-side contract (SSE frames, JSON envelopes, code surface) is implemented by the
in-package ``redbear_model.runtime.remote``; this layer covers only three host-side concerns:
borrowing a host-**dedicated** connection pool (the management-plane ``READ=120`` is
whole-response semantics, which frame streams would break), translating host attribution into
frame headers (``ModelCallContext`` identity + usage-contextvar business attribution +
request-context ``trace_id``), and translating in-package ``RemoteInvoke*`` into host
``ModelServiceClientError`` / ``BizCode`` (pass-through without re-encoding).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any
from uuid import UUID

import httpx
from redbear_model.errors import (
    RemoteInvokeError,
    RemoteInvokeFailedError,
    RemoteInvokeIdleTimeoutError,
    RemoteInvokeProtocolError,
)
from redbear_model.runtime.remote import (
    AsyncInvokeTransport,
    InvokeFrame,
    InvokeRequest,
    InvokeTarget,
    InvokeTimeouts,
    SyncInvokeTransport,
)

from ...config import current_settings
from ...trace import get_trace_id
from ...usage_context import get_usage_resource

from .client import http_limits
from .contracts import ModelCallContext
from .errors import (
    ModelInvokeFailedError,
    ModelServiceClientError,
    ModelServiceProtocolError,
    ModelServiceTimeoutError,
    ModelServiceUnavailableError,
    biz_code_from_remote,
)
from .transport import ModelServiceHttpTransport, ModelServiceSyncTransport

logger = logging.getLogger(__name__)


def _uuid_or_none(value: str | None) -> UUID | None:
    """Attribution ids cross the wire only as UUIDs; the service side rejects the whole call on non-UUID values."""

    if not value:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        logger.debug("model_invoke_resource_id_not_uuid value=%r", value)
        return None


def invoke_target(context: ModelCallContext) -> InvokeTarget:
    """Attribution headers: caller identity/source, usage-contextvar business attribution, request-context trace fallback."""

    resource = get_usage_resource()
    return InvokeTarget(
        tenant_id=context.tenant_id,
        source=context.source,
        actor_id=context.actor_id,
        actor_name=context.actor_name,
        workspace_id=context.workspace_id,
        resource_type=resource.resource_type if resource else None,
        resource_id=_uuid_or_none(resource.resource_id) if resource else None,
        trace_id=context.trace_id or get_trace_id() or None,
    )


def invoke_timeouts(settings: Any) -> InvokeTimeouts:
    """Frame-stream timeouts: connect 5s / inter-chunk idle 180s / no total cap; pool wait follows the management-plane policy."""

    return InvokeTimeouts(
        connect_s=current_settings().model_service_invoke_connect_timeout_seconds,
        idle_s=current_settings().model_service_invoke_idle_timeout_seconds,
        write_s=current_settings().model_service_invoke_write_timeout_seconds,
        pool_s=current_settings().model_service_pool_timeout_seconds,
    )


def invoke_media_timeouts(settings: Any) -> InvokeTimeouts:
    """Media-profile timeouts: connect/write/pool match :func:`invoke_timeouts`, idle uses the media profile.

    Media calls block service-side until the task completes (polling belongs to the
    service side) while the host receives no frames; idle comes from
    ``MODEL_SERVICE_INVOKE_MEDIA_IDLE_TIMEOUT_SECONDS`` (see the timeout-chain notes
    in the config).
    """

    return InvokeTimeouts(
        connect_s=current_settings().model_service_invoke_connect_timeout_seconds,
        idle_s=current_settings().model_service_invoke_media_idle_timeout_seconds,
        write_s=current_settings().model_service_invoke_write_timeout_seconds,
        pool_s=current_settings().model_service_pool_timeout_seconds,
    )


def to_host_error(exc: RemoteInvokeError) -> ModelServiceClientError:
    """In-package error -> host error vocabulary; the only translation point, codes pass through un-re-encoded."""

    if isinstance(exc, RemoteInvokeIdleTimeoutError):
        error: ModelServiceClientError = ModelServiceTimeoutError(
            f"Model invoke produced no frame within {exc.idle_seconds:g}s"
        )
    elif isinstance(exc, RemoteInvokeProtocolError):
        error = ModelServiceProtocolError(str(exc))
    elif isinstance(exc, RemoteInvokeFailedError):
        error = ModelInvokeFailedError(
            biz_code=biz_code_from_remote(exc.code),
            message=exc.message,
            attempts=exc.attempts,
            channel_id=exc.channel_id,
            retryable=exc.retryable,
            http_status=exc.http_status,
        )
    else:
        error = ModelServiceUnavailableError("Model service is unavailable")
    logger.warning(
        "model_invoke_failed error=%s remote=%s detail=%s",
        type(error).__name__,
        type(exc).__name__,
        exc,
    )
    return error


class HostAsyncInvokeTransport(AsyncInvokeTransport):
    """Host-facing side of the in-package async transport: adds only an error-translation layer (vocabulary stays in the integration layer).

    Must be a **true subclass**: the in-package chat adapter picks the sync/async
    flavor via ``isinstance``, and a wrapper would be rejected at construction time.
    The two translated error sites are the host vocabulary; all other frame
    semantics pass through untouched.
    """

    async def call(self, request: InvokeRequest, target: InvokeTarget) -> Any:
        try:
            return await super().call(request, target)
        except RemoteInvokeError as exc:
            raise to_host_error(exc) from exc

    async def stream(
        self, request: InvokeRequest, target: InvokeTarget
    ) -> AsyncIterator[InvokeFrame]:
        try:
            async for frame in super().stream(request, target):
                yield frame
        except RemoteInvokeError as exc:
            raise to_host_error(exc) from exc


class HostSyncInvokeTransport(SyncInvokeTransport):
    """Host-facing side of the in-package sync transport: same error translation as the async variant, vocabulary stays in the integration layer.

    Also must be a **true subclass**: the in-package chat adapter picks the
    sync/async flavor via ``isinstance``. Consumers: the sync LLM shell
    (``RedBearChatModel.for_invoke_sync``) and the media-family sync twins.
    """

    def call(self, request: InvokeRequest, target: InvokeTarget) -> Any:
        try:
            return super().call(request, target)
        except RemoteInvokeError as exc:
            raise to_host_error(exc) from exc

    def stream(
        self, request: InvokeRequest, target: InvokeTarget
    ) -> Iterator[InvokeFrame]:
        try:
            yield from super().stream(request, target)
        except RemoteInvokeError as exc:
            raise to_host_error(exc) from exc


class ModelInvokeClient:
    """Runtime-plane invoke channel: frames pass through as-is (``InvokeFrame``), failures are translated to host vocabulary.

    Dedicated connection pool: the runtime plane can still send while the
    management-plane pool is saturated with management-plane requests (and vice
    versa); timeouts follow frame-interval semantics, see :func:`invoke_timeouts`.
    Requests use the in-package ``InvokeRequest`` -- the shared wire contract of
    both sides -- so the host does not define its own.
    """

    def __init__(self, transport: ModelServiceHttpTransport, *, timeouts: InvokeTimeouts):
        self._transport = transport
        self._invoke = HostAsyncInvokeTransport(
            transport.client,
            base_url=str(transport.base_url).rstrip("/"),
            timeouts=timeouts,
        )

    @property
    def async_transport(self) -> HostAsyncInvokeTransport:
        """Lends the in-package transport carrying host error vocabulary; the in-package chat adapter consumes it directly."""

        return self._invoke

    @classmethod
    def from_settings(
        cls, settings: Any = None, *, timeouts: InvokeTimeouts | None = None
    ) -> ModelInvokeClient:
        settings = settings or current_settings()
        resolved = timeouts or invoke_timeouts(settings)
        transport = ModelServiceHttpTransport(
            base_url=current_settings().model_service_base_url,
            timeout=resolved.httpx_timeout(),
            limits=http_limits(settings),
        )
        return cls(transport, timeouts=resolved)

    @classmethod
    def for_test(
        cls,
        base_url: str,
        transport: httpx.AsyncBaseTransport,
        *,
        timeouts: InvokeTimeouts | None = None,
    ) -> ModelInvokeClient:
        resolved = timeouts or InvokeTimeouts()
        return cls(
            ModelServiceHttpTransport(
                base_url=base_url,
                timeout=resolved.httpx_timeout(),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                transport=transport,
            ),
            timeouts=resolved,
        )

    async def stream(
        self, request: InvokeRequest, context: ModelCallContext
    ) -> AsyncIterator[InvokeFrame]:
        """Streaming call: frame order and terminal state are validated in-package (``done`` closes the stream; disorder/truncation fail loudly)."""

        async for frame in self._invoke.stream(request, invoke_target(context)):
            yield frame

    async def call(self, request: InvokeRequest, context: ModelCallContext) -> Any:
        """Non-streaming call: returns the family result body carried in ``data`` (failures translated to host vocabulary as usual)."""

        return await self._invoke.call(request, invoke_target(context))

    async def aclose(self) -> None:
        await self._transport.aclose()


class ModelInvokeSyncClient:
    """Runtime-plane sync twin: synchronous call sites (ES vector-store paths / RAG rerank / annotations / sync LLM shell) share the same host vocabulary.

    The in-package ``SyncInvokeTransport`` already unpacks synchronous envelopes;
    this layer only borrows the pool, with error translation in
    ``HostSyncInvokeTransport`` (sharing :func:`invoke_target` / :func:`to_host_error`
    / :func:`invoke_timeouts` with the async variant). Media-family sync twins pass
    media-profile timeouts (:func:`invoke_media_timeouts`).
    """

    def __init__(self, transport: ModelServiceSyncTransport, *, timeouts: InvokeTimeouts):
        self._transport = transport
        self._invoke = HostSyncInvokeTransport(
            transport.client,
            base_url=str(transport.base_url).rstrip("/"),
            timeouts=timeouts,
        )

    @property
    def sync_transport(self) -> HostSyncInvokeTransport:
        """Lends the in-package sync transport carrying host error vocabulary; the sync LLM shell consumes it directly."""

        return self._invoke

    @classmethod
    def from_settings(
        cls, settings: Any = None, *, timeouts: InvokeTimeouts | None = None
    ) -> ModelInvokeSyncClient:
        settings = settings or current_settings()
        resolved = timeouts or invoke_timeouts(settings)
        transport = ModelServiceSyncTransport(
            base_url=current_settings().model_service_base_url,
            timeout=resolved.httpx_timeout(),
            limits=http_limits(settings),
        )
        return cls(transport, timeouts=resolved)

    @classmethod
    def for_test(
        cls,
        base_url: str,
        transport: httpx.BaseTransport,
        *,
        timeouts: InvokeTimeouts | None = None,
    ) -> ModelInvokeSyncClient:
        resolved = timeouts or InvokeTimeouts()
        return cls(
            ModelServiceSyncTransport(
                base_url=base_url,
                timeout=resolved.httpx_timeout(),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                transport=transport,
            ),
            timeouts=resolved,
        )

    def call(self, request: InvokeRequest, context: ModelCallContext) -> Any:
        """Non-streaming call: returns the family result body carried in ``data`` (failures translated to host vocabulary as usual)."""

        return self._invoke.call(request, invoke_target(context))

    def close(self) -> None:
        self._transport.close()

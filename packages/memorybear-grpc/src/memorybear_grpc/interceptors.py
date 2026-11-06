"""Server interceptors: trace, auth, logging and metrics.

Both ``unary_unary`` and ``unary_stream`` handlers are wrapped: Chat and Invoke
are unary_stream while knowledge retrieval is unary_unary. The health service is
exempted by method path (``/grpc.health.v1.Health/``), not by handler type: it
also exposes the server-streaming ``Watch`` RPC and must stay unauthenticated
for K8s probes. Any other RPC shape fails loudly instead of silently bypassing
the interceptor chain.

Interceptors wrap the handler function itself rather than the continuation:
authentication needs the ``ServicerContext`` (to abort) and the principal must
be visible to the handler through a ContextVar. Both are only available inside
the handler invocation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from typing import Any

import grpc
from prometheus_client import Counter, Gauge, Histogram

from .metadata import trace_id_from_metadata

logger = logging.getLogger(__name__)

SERVER_HANDLED = Counter(
    "grpc_server_handled_total",
    "Completed gRPC server streams by method and status code.",
    ("service", "method", "code"),
)
SERVER_HANDLING_SECONDS = Histogram(
    "grpc_server_handling_seconds",
    "gRPC server stream duration in seconds.",
    ("service", "method"),
)
SERVER_INFLIGHT = Gauge(
    "grpc_server_inflight_streams",
    "gRPC server streams currently being handled.",
    ("service", "method"),
)
STREAM_CANCELLED = Counter(
    "grpc_stream_cancelled_total",
    "Streams terminated by cancellation, by side.",
    ("service", "method", "role"),
)

AUTH_PRINCIPAL: ContextVar[Any | None] = ContextVar("grpc_auth_principal", default=None)

Authenticator = Callable[[grpc.aio.ServicerContext, str], Awaitable[Any]]
TraceBinder = Callable[[str], Any]
TraceResetter = Callable[[Any], None]
Behavior = Callable[[Any, grpc.aio.ServicerContext], AsyncIterator[Any]]

_HEALTH_METHOD_PREFIX = "/grpc.health.v1.Health/"


def current_principal(default: Any | None = None) -> Any | None:
    principal = AUTH_PRINCIPAL.get()
    return default if principal is None else principal


def _method_name(method: str) -> str:
    return method.rsplit("/", 1)[-1] or method


def _is_health_method(method: str) -> bool:
    return method.startswith(_HEALTH_METHOD_PREFIX)


def _original(handler, method: str) -> tuple[Callable[..., Any], bool]:
    """Return the servicer callable and whether it yields a stream."""

    if handler.unary_unary is not None:
        return handler.unary_unary, False
    if handler.unary_stream is not None:
        return handler.unary_stream, True
    raise RuntimeError(f"interceptor only supports unary RPCs; got {method}")


async def _invoke(
    original: Callable[..., Any],
    request: Any,
    context: grpc.aio.ServicerContext,
    *,
    stream: bool,
) -> AsyncIterator[Any]:
    """Expose both call shapes as one async generator for the wrappers."""

    if stream:
        async for item in original(request, context):
            yield item
    else:
        yield await original(request, context)


def _wrap(handler, behavior: Behavior):
    """Rebuild the handler so ``behavior`` owns the whole call lifetime.

    ``behavior`` is an async generator for both shapes. The unary adapter
    consumes exactly one item and closes the generator explicitly, so the
    wrappers' ``finally`` blocks (ContextVar reset, metrics, logging) run before
    the response is returned instead of waiting for async-generator finalization.
    """

    if handler.unary_unary is not None:

        async def unary_unary(request, context):
            iterator = behavior(request, context)
            try:
                async for item in iterator:
                    return item
                raise RuntimeError("unary handler returned no response")
            finally:
                await iterator.aclose()

        return grpc.unary_unary_rpc_method_handler(
            unary_unary,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
    return grpc.unary_stream_rpc_method_handler(
        behavior,
        request_deserializer=handler.request_deserializer,
        response_serializer=handler.response_serializer,
    )


class TraceInterceptor(grpc.aio.ServerInterceptor):
    """Bind x-trace-id (or the W3C traceparent trace-id) for the call lifetime."""

    def __init__(self, bind: TraceBinder, reset: TraceResetter) -> None:
        self._bind = bind
        self._reset = reset

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None or _is_health_method(handler_call_details.method):
            return handler
        original, stream = _original(handler, handler_call_details.method)

        async def behavior(request, context):
            trace_id = trace_id_from_metadata(context.invocation_metadata())
            token = self._bind(trace_id) if trace_id else None
            try:
                async for item in _invoke(original, request, context, stream=stream):
                    yield item
            finally:
                if token is not None:
                    self._reset(token)

        return _wrap(handler, behavior)


class AuthInterceptor(grpc.aio.ServerInterceptor):
    """Run the service authenticator before the call starts.

    The authenticator either returns a principal or calls ``context.abort``.
    Its result is exposed to the servicer through ``current_principal()``.
    """

    def __init__(self, authenticate: Authenticator) -> None:
        self._authenticate = authenticate

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None or _is_health_method(handler_call_details.method):
            return handler
        original, stream = _original(handler, handler_call_details.method)

        async def behavior(request, context):
            principal = await self._authenticate(context, handler_call_details.method)
            token = AUTH_PRINCIPAL.set(principal)
            try:
                async for item in _invoke(original, request, context, stream=stream):
                    yield item
            finally:
                AUTH_PRINCIPAL.reset(token)

        return _wrap(handler, behavior)


class LoggingInterceptor(grpc.aio.ServerInterceptor):
    """One structured line per completed call (never logs payloads)."""

    def __init__(self, service_name: str) -> None:
        self._service_name = service_name

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None or _is_health_method(handler_call_details.method):
            return handler
        original, stream = _original(handler, handler_call_details.method)
        method = _method_name(handler_call_details.method)

        async def behavior(request, context):
            started = time.perf_counter()
            code = grpc.StatusCode.OK
            try:
                async for item in _invoke(original, request, context, stream=stream):
                    yield item
            except grpc.aio.AbortError:
                code = context.code() or grpc.StatusCode.UNKNOWN
                raise
            except asyncio.CancelledError:
                code = grpc.StatusCode.CANCELLED
                raise
            except Exception:
                code = grpc.StatusCode.UNKNOWN
                raise
            finally:
                final_code = context.code()
                if final_code is not None and final_code != grpc.StatusCode.OK:
                    code = final_code
                logger.info(
                    "grpc_stream_finished service=%s method=%s code=%s duration_ms=%.1f",
                    self._service_name,
                    method,
                    code.name,
                    (time.perf_counter() - started) * 1000,
                )

        return _wrap(handler, behavior)


class MetricsInterceptor(grpc.aio.ServerInterceptor):
    def __init__(self, service_name: str) -> None:
        self._service_name = service_name

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None or _is_health_method(handler_call_details.method):
            return handler
        original, stream = _original(handler, handler_call_details.method)
        method = _method_name(handler_call_details.method)
        service = self._service_name
        inflight = SERVER_INFLIGHT.labels(service, method)
        duration = SERVER_HANDLING_SECONDS.labels(service, method)

        async def behavior(request, context):
            inflight.inc()
            started = time.perf_counter()
            code = grpc.StatusCode.OK
            try:
                async for item in _invoke(original, request, context, stream=stream):
                    yield item
            except grpc.aio.AbortError:
                code = context.code() or grpc.StatusCode.UNKNOWN
                raise
            except asyncio.CancelledError:
                code = grpc.StatusCode.CANCELLED
                STREAM_CANCELLED.labels(service, method, "server").inc()
                raise
            except Exception:
                code = grpc.StatusCode.UNKNOWN
                raise
            finally:
                final_code = context.code()
                if final_code is not None and final_code != grpc.StatusCode.OK:
                    code = final_code
                inflight.dec()
                duration.observe(time.perf_counter() - started)
                SERVER_HANDLED.labels(service, method, code.name).inc()

        return _wrap(handler, behavior)


__all__ = [
    "AUTH_PRINCIPAL",
    "AuthInterceptor",
    "Authenticator",
    "LoggingInterceptor",
    "MetricsInterceptor",
    "TraceInterceptor",
    "current_principal",
]

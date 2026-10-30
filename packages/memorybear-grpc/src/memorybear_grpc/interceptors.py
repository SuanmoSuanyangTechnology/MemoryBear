"""Server interceptors: trace, auth, logging and metrics.

Only ``unary_stream`` handlers are wrapped. Chat and Invoke are unary_stream.
The health service is exempted by method path (``/grpc.health.v1.Health/``),
not by handler type: it also exposes the server-streaming ``Watch`` RPC and
must stay unauthenticated for K8s probes. Any other non-stream RPC fails
loudly instead of silently bypassing the interceptor chain.

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

from .metadata import TRACE_ID_METADATA_KEY, metadata_dict

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

_HEALTH_METHOD_PREFIX = "/grpc.health.v1.Health/"


def current_principal(default: Any | None = None) -> Any | None:
    principal = AUTH_PRINCIPAL.get()
    return default if principal is None else principal


def _method_name(method: str) -> str:
    return method.rsplit("/", 1)[-1] or method


def _is_health_method(method: str) -> bool:
    return method.startswith(_HEALTH_METHOD_PREFIX)


def _wrap(handler, behavior: Callable[..., AsyncIterator[Any]]):
    return grpc.unary_stream_rpc_method_handler(
        behavior,
        request_deserializer=handler.request_deserializer,
        response_serializer=handler.response_serializer,
    )


class TraceInterceptor(grpc.aio.ServerInterceptor):
    """Bind x-trace-id into the service's own ContextVar for the stream lifetime."""

    def __init__(self, bind: TraceBinder, reset: TraceResetter) -> None:
        self._bind = bind
        self._reset = reset

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None or _is_health_method(handler_call_details.method):
            return handler
        if handler.unary_stream is None:
            raise RuntimeError(
                f"interceptor only supports unary_stream RPCs; got {handler_call_details.method}"
            )
        original = handler.unary_stream

        async def unary_stream(request, context):
            trace_id = metadata_dict(context.invocation_metadata()).get(
                TRACE_ID_METADATA_KEY
            )
            token = self._bind(trace_id) if trace_id else None
            try:
                async for item in original(request, context):
                    yield item
            finally:
                if token is not None:
                    self._reset(token)

        return _wrap(handler, unary_stream)


class AuthInterceptor(grpc.aio.ServerInterceptor):
    """Run the service authenticator before the stream starts.

    The authenticator either returns a principal or calls ``context.abort``.
    Its result is exposed to the servicer through ``current_principal()``.
    """

    def __init__(self, authenticate: Authenticator) -> None:
        self._authenticate = authenticate

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None or _is_health_method(handler_call_details.method):
            return handler
        if handler.unary_stream is None:
            raise RuntimeError(
                f"interceptor only supports unary_stream RPCs; got {handler_call_details.method}"
            )
        original = handler.unary_stream

        async def unary_stream(request, context):
            principal = await self._authenticate(context, handler_call_details.method)
            token = AUTH_PRINCIPAL.set(principal)
            try:
                async for item in original(request, context):
                    yield item
            finally:
                AUTH_PRINCIPAL.reset(token)

        return _wrap(handler, unary_stream)


class LoggingInterceptor(grpc.aio.ServerInterceptor):
    """One structured line per completed stream (never logs payloads)."""

    def __init__(self, service_name: str) -> None:
        self._service_name = service_name

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None or _is_health_method(handler_call_details.method):
            return handler
        if handler.unary_stream is None:
            raise RuntimeError(
                f"interceptor only supports unary_stream RPCs; got {handler_call_details.method}"
            )
        original = handler.unary_stream
        method = _method_name(handler_call_details.method)

        async def unary_stream(request, context):
            started = time.perf_counter()
            code = grpc.StatusCode.OK
            try:
                async for item in original(request, context):
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

        return _wrap(handler, unary_stream)


class MetricsInterceptor(grpc.aio.ServerInterceptor):
    def __init__(self, service_name: str) -> None:
        self._service_name = service_name

    async def intercept_service(self, continuation, handler_call_details):
        handler = await continuation(handler_call_details)
        if handler is None or _is_health_method(handler_call_details.method):
            return handler
        if handler.unary_stream is None:
            raise RuntimeError(
                f"interceptor only supports unary_stream RPCs; got {handler_call_details.method}"
            )
        original = handler.unary_stream
        method = _method_name(handler_call_details.method)
        service = self._service_name
        inflight = SERVER_INFLIGHT.labels(service, method)
        duration = SERVER_HANDLING_SECONDS.labels(service, method)

        async def unary_stream(request, context):
            inflight.inc()
            started = time.perf_counter()
            code = grpc.StatusCode.OK
            try:
                async for item in original(request, context):
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

        return _wrap(handler, unary_stream)


__all__ = [
    "AUTH_PRINCIPAL",
    "AuthInterceptor",
    "Authenticator",
    "LoggingInterceptor",
    "MetricsInterceptor",
    "TraceInterceptor",
    "current_principal",
]

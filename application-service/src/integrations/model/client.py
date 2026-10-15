"""Remote implementation of the model management route surface."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from fastapi import Request, Response

from .contracts import ModelCallContext, ModelServiceCallResult
from .errors import ModelServiceTimeoutError, ModelServiceUnavailableError
from .transport import ModelServiceHttpTransport, ModelServiceSyncTransport
from ...config import current_settings

logger = logging.getLogger(__name__)


def http_limits(settings: Any) -> httpx.Limits:
    """Shared connection limits: management-plane and invoke pools use one policy (avoids drift)."""

    return httpx.Limits(
        max_connections=current_settings().model_service_max_connections,
        max_keepalive_connections=current_settings().model_service_max_keepalive_connections,
    )


def _http_params(settings: Any) -> tuple[httpx.Timeout, httpx.Limits]:
    """Async/sync pools share one timeout and connection-limit set (one policy, avoids drift)."""

    timeout = httpx.Timeout(
        connect=current_settings().model_service_connect_timeout_seconds,
        pool=current_settings().model_service_pool_timeout_seconds,
        read=current_settings().model_service_read_timeout_seconds,
        write=current_settings().model_service_write_timeout_seconds,
    )
    return timeout, http_limits(settings)


class ModelServiceClient:
    """HTTP adapter forwarding ``/api/models*`` to the model service."""

    def __init__(self, transport: ModelServiceHttpTransport):
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Any) -> ModelServiceClient:
        timeout, limits = _http_params(settings)
        return cls(
            ModelServiceHttpTransport(
                base_url=current_settings().model_service_base_url,
                timeout=timeout,
                limits=limits,
            )
        )

    @classmethod
    def for_test(
        cls,
        base_url: str,
        transport: httpx.AsyncBaseTransport,
    ) -> ModelServiceClient:
        return cls(
            ModelServiceHttpTransport(
                base_url=base_url,
                timeout=httpx.Timeout(5.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                transport=transport,
            )
        )

    async def forward(self, request: Request, context: ModelCallContext) -> Response:
        """Forward one management-plane request and buffer the upstream response.

        Management-plane response bodies are small and must be preserved verbatim
        (409+impact / 422 envelopes / 201 / pagination / i18n), so the response is
        buffered rather than streamed: status code and body stay consistent, and
        transport failures can still be mapped to 503.
        """
        started_at = time.perf_counter()
        path = self._transport.internal_path(request.url.path)
        url = self._transport.internal_url(path, request.scope.get("query_string", b""))
        headers = self._transport.request_headers(request.headers, context)
        body = await request.body()
        upstream = await self._transport.send(
            method=request.method,
            url=url,
            headers=headers,
            content=body or None,
        )
        raw = await self._read_buffered(upstream)
        logger.info(
            "model_proxy_forwarded method=%s path=%s status=%s bytes=%s source=%s "
            "elapsed_ms=%.2f trace_id=%s",
            request.method,
            path,
            upstream.status_code,
            len(raw),
            context.source,
            (time.perf_counter() - started_at) * 1000,
            context.trace_id,
        )
        return Response(
            content=raw,
            status_code=upstream.status_code,
            headers=self._transport.response_headers(upstream.headers),
        )

    async def call(
        self,
        method: str,
        path: str,
        *,
        context: ModelCallContext,
        payload: dict[str, Any] | None = None,
    ) -> ModelServiceCallResult:
        """Call one internal endpoint with a JSON body and buffer the response envelope.

        Unlike ``forward`` (verbatim management-plane passthrough), the caller
        (the premium platform plane) needs the status code and the parsed envelope
        to reconstruct business semantics, so this variant returns a structured
        result instead of streaming through.
        """
        internal = self._transport.internal_path(path)
        url = self._transport.internal_url(internal)
        headers = self._transport.request_headers(
            {"content-type": "application/json"} if payload is not None else {},
            context,
        )
        content = json.dumps(payload).encode() if payload is not None else None
        started_at = time.perf_counter()
        upstream = await self._transport.send(
            method=method.upper(),
            url=url,
            headers=headers,
            content=content,
        )
        raw = await self._read_buffered(upstream)
        logger.info(
            "model_service_called method=%s path=%s status=%s bytes=%s source=%s "
            "elapsed_ms=%.2f trace_id=%s",
            method.upper(),
            internal,
            upstream.status_code,
            len(raw),
            context.source,
            (time.perf_counter() - started_at) * 1000,
            context.trace_id,
        )
        return ModelServiceCallResult(
            status_code=upstream.status_code,
            payload=self._parse_envelope(raw),
        )

    @staticmethod
    def _parse_envelope(raw: bytes) -> dict[str, Any] | None:
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    async def _read_buffered(upstream: httpx.Response) -> bytes:
        try:
            return await upstream.aread()
        except httpx.TimeoutException as exc:
            raise ModelServiceTimeoutError("Model service request timed out") from exc
        except httpx.RequestError as exc:
            raise ModelServiceUnavailableError("Model service is unavailable") from exc
        finally:
            await upstream.aclose()

    async def aclose(self) -> None:
        await self._transport.aclose()


class ModelServiceSyncClient:
    """Synchronous twin adapter for management-plane callers of internal endpoints.

    Same semantics as ``ModelServiceClient.call``. Host management-plane routes
    are ``def`` handlers (anyio thread pool), and premium compensation flows
    (SSO binding retries, order quota allocation, tenant rollback) commit multiple
    times and cannot introduce async, hence a blocking-IO variant. Envelope
    parsing and error vocabulary reuse the shared implementation.
    """

    def __init__(self, transport: ModelServiceSyncTransport):
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Any) -> ModelServiceSyncClient:
        timeout, limits = _http_params(settings)
        return cls(
            ModelServiceSyncTransport(
                base_url=current_settings().model_service_base_url,
                timeout=timeout,
                limits=limits,
            )
        )

    @classmethod
    def for_test(
        cls,
        base_url: str,
        transport: httpx.BaseTransport,
    ) -> ModelServiceSyncClient:
        return cls(
            ModelServiceSyncTransport(
                base_url=base_url,
                timeout=httpx.Timeout(5.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                transport=transport,
            )
        )

    def call(
        self,
        method: str,
        path: str,
        *,
        context: ModelCallContext,
        payload: dict[str, Any] | None = None,
    ) -> ModelServiceCallResult:
        internal = self._transport.internal_path(path)
        url = self._transport.internal_url(internal)
        headers = self._transport.request_headers(
            {"content-type": "application/json"} if payload is not None else {},
            context,
        )
        content = json.dumps(payload).encode() if payload is not None else None
        started_at = time.perf_counter()
        upstream = self._transport.send(
            method=method.upper(),
            url=url,
            headers=headers,
            content=content,
        )
        raw = upstream.content
        logger.info(
            "model_service_called method=%s path=%s status=%s bytes=%s source=%s "
            "elapsed_ms=%.2f trace_id=%s",
            method.upper(),
            internal,
            upstream.status_code,
            len(raw),
            context.source,
            (time.perf_counter() - started_at) * 1000,
            context.trace_id,
        )
        return ModelServiceCallResult(
            status_code=upstream.status_code,
            payload=ModelServiceClient._parse_envelope(raw),
        )

    def close(self) -> None:
        self._transport.close()

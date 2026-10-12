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

logger = logging.getLogger(__name__)


def http_limits(settings: Any) -> httpx.Limits:
    """连接上限：管理面两个池与 invoke 池共用同一口径（避免漂移）。"""

    return httpx.Limits(
        max_connections=settings.MODEL_SERVICE_MAX_CONNECTIONS,
        max_keepalive_connections=settings.MODEL_SERVICE_MAX_KEEPALIVE_CONNECTIONS,
    )


def _http_params(settings: Any) -> tuple[httpx.Timeout, httpx.Limits]:
    """异步/同步两个池共用同一组超时与连接上限（口径唯一，避免漂移）。"""

    timeout = httpx.Timeout(
        connect=settings.MODEL_SERVICE_CONNECT_TIMEOUT_SECONDS,
        pool=settings.MODEL_SERVICE_POOL_TIMEOUT_SECONDS,
        read=settings.MODEL_SERVICE_READ_TIMEOUT_SECONDS,
        write=settings.MODEL_SERVICE_WRITE_TIMEOUT_SECONDS,
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
                base_url=settings.MODEL_SERVICE_BASE_URL,
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

        管理面响应体小且需原样保真（409+impact / 422 信封 / 201 / 分页 / i18n），
        缓冲返回而非流式：状态码与 body 一致，且传输失败仍可映射 503。
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

        与 ``forward``（管理面原样透传）不同：调用方（premium 平台面）需要 status_code
        与解析后的信封来还原业务语义，故此处不做流式透传，只回结构化结果。
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
    """同步孪生：管理面（sync）调用内部端点的适配器，语义与 ``ModelServiceClient.call`` 一致。

    宿主管理面路由是 ``def``（anyio 线程池），premium 的补偿式编排（SSO 绑定重试、订单派额、
    租户回滚）内部多次提交且不可引入 async，故提供同步 IO 版本；信封解析与错误语汇复用同源实现。
    """

    def __init__(self, transport: ModelServiceSyncTransport):
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Any) -> ModelServiceSyncClient:
        timeout, limits = _http_params(settings)
        return cls(
            ModelServiceSyncTransport(
                base_url=settings.MODEL_SERVICE_BASE_URL,
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

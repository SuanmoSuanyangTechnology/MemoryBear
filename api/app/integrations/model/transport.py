"""Private HTTP transport for the independent model service (async + sync twins)."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from urllib.parse import quote

import httpx

from .contracts import ModelCallContext
from .errors import ModelServiceTimeoutError, ModelServiceUnavailableError

logger = logging.getLogger(__name__)

_REQUEST_HEADER_ALLOWLIST = frozenset({"accept", "accept-language", "content-type"})
# X-Trace-Id 不在白名单：宿主 TraceIdMiddleware 会在外层统一回写本次请求的 trace_id
_RESPONSE_HEADER_ALLOWLIST = frozenset(
    {
        "cache-control",
        "content-language",
        "content-type",
        "etag",
        "last-modified",
    }
)


def _internal_path(external_path: str) -> str:
    for prefix in ("/api",):
        if external_path == prefix or external_path.startswith(prefix + "/"):
            return "/internal/v1" + external_path[len(prefix) :]
    if external_path.startswith("/internal/v1/"):
        return external_path
    raise ValueError("Model route path must start with /api or /internal/v1")


def _request_headers(
    incoming: Mapping[str, str],
    context: ModelCallContext,
) -> dict[str, str]:
    headers = {
        key: value
        for key, value in incoming.items()
        if key.lower() in _REQUEST_HEADER_ALLOWLIST
    }
    # actor 可选（设计 §2.2）：Celery 等无用户身份的调用方省略该头，服务侧按缺省主体处理
    if context.actor_id is not None:
        headers["X-Model-Actor-ID"] = str(context.actor_id)
    if context.actor_name:
        # 头值须为 ASCII（RFC 9110），用户名可为中文：UTF-8 百分号编码承载，服务侧 unquote 还原
        headers["X-Model-Actor-Name"] = quote(context.actor_name, safe="")
    headers["X-Model-Tenant-ID"] = str(context.tenant_id)
    if context.workspace_id is not None:
        headers["X-Model-Workspace-ID"] = str(context.workspace_id)
    headers["X-Model-Source"] = context.source
    if context.trace_id:
        headers["X-Trace-Id"] = context.trace_id
    return headers


def _response_headers(incoming: Mapping[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in incoming.items()
        if key.lower() in _RESPONSE_HEADER_ALLOWLIST
    }


def _log_failure(method: str, path: str, exc: Exception, started_at: float, trace_id: str) -> None:
    logger.warning(
        "model_service_http_failed method=%s path=%s error=%s pool_timeout=%s "
        "elapsed_ms=%.2f trace_id=%s",
        method,
        path,
        type(exc).__name__,
        isinstance(exc, httpx.PoolTimeout),
        (time.perf_counter() - started_at) * 1000,
        trace_id,
    )


def _log_response(
    method: str, path: str, status_code: int, started_at: float, trace_id: str
) -> None:
    logger.info(
        "model_service_http_response method=%s path=%s status=%s elapsed_ms=%.2f trace_id=%s",
        method,
        path,
        status_code,
        (time.perf_counter() - started_at) * 1000,
        trace_id,
    )


class ModelServiceHttpTransport:
    """Own one process-level HTTP pool and all transport normalization."""

    internal_path = staticmethod(_internal_path)
    request_headers = staticmethod(_request_headers)
    response_headers = staticmethod(_response_headers)

    def __init__(
        self,
        *,
        base_url: str,
        timeout: httpx.Timeout,
        limits: httpx.Limits,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout,
            limits=limits,
            transport=transport,
            trust_env=False,
        )
        self._default_timeout = timeout

    @property
    def base_url(self) -> httpx.URL:
        return self._client.base_url

    @property
    def client(self) -> httpx.AsyncClient:
        """借出连接池：invoke 通道在**独立池**上复用同组 socket 参数（每请求自带超时）。"""

        return self._client

    def internal_url(self, path: str, query: bytes = b"") -> httpx.URL:
        # 空查询串归一为「不带 ?」：httpx 的 copy_with(query=None) 为保持原值语义
        return self._client.base_url.copy_with(path=path, query=query or None)

    async def send(
        self,
        *,
        method: str,
        url: httpx.URL,
        headers: Mapping[str, str],
        content: bytes | None = None,
    ) -> httpx.Response:
        started_at = time.perf_counter()
        trace_id = headers.get("X-Trace-Id", "")
        request = self._client.build_request(
            method,
            url,
            headers=headers,
            content=content,
            timeout=self._default_timeout,
        )
        try:
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            _log_failure(method, url.path, exc, started_at, trace_id)
            raise ModelServiceTimeoutError("Model service request timed out") from exc
        except httpx.RequestError as exc:
            _log_failure(method, url.path, exc, started_at, trace_id)
            raise ModelServiceUnavailableError("Model service is unavailable") from exc
        _log_response(method, url.path, response.status_code, started_at, trace_id)
        return response

    async def aclose(self) -> None:
        await self._client.aclose()


class ModelServiceSyncTransport:
    """同步孪生：宿主管理面（sync 路由 / premium 补偿式编排）调用内部端点的唯一出口。

    路径映射与内部头注入复用同一组原语（与异步池行为逐字一致）；独立 ``httpx.Client``
    （httpx 同步客户端可跨线程共享，管理面 ``def`` 路由跑在 anyio 线程池）。
    同步路径不做流式：响应体在本层读完，调用方拿到的即最终信封。
    """

    internal_path = staticmethod(_internal_path)
    request_headers = staticmethod(_request_headers)
    response_headers = staticmethod(_response_headers)

    def __init__(
        self,
        *,
        base_url: str,
        timeout: httpx.Timeout,
        limits: httpx.Limits,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout,
            limits=limits,
            transport=transport,
            trust_env=False,
        )
        self._default_timeout = timeout

    @property
    def base_url(self) -> httpx.URL:
        return self._client.base_url

    @property
    def client(self) -> httpx.Client:
        """借出连接池：同步 invoke 通道在**独立池**上复用同组 socket 参数（每请求自带超时）。"""

        return self._client

    def internal_url(self, path: str, query: bytes = b"") -> httpx.URL:
        return self._client.base_url.copy_with(path=path, query=query or None)

    def send(
        self,
        *,
        method: str,
        url: httpx.URL,
        headers: Mapping[str, str],
        content: bytes | None = None,
    ) -> httpx.Response:
        started_at = time.perf_counter()
        trace_id = headers.get("X-Trace-Id", "")
        request = self._client.build_request(
            method,
            url,
            headers=headers,
            content=content,
            timeout=self._default_timeout,
        )
        try:
            response = self._client.send(request)
        except httpx.TimeoutException as exc:
            _log_failure(method, url.path, exc, started_at, trace_id)
            raise ModelServiceTimeoutError("Model service request timed out") from exc
        except httpx.RequestError as exc:
            _log_failure(method, url.path, exc, started_at, trace_id)
            raise ModelServiceUnavailableError("Model service is unavailable") from exc
        _log_response(method, url.path, response.status_code, started_at, trace_id)
        return response

    def close(self) -> None:
        self._client.close()

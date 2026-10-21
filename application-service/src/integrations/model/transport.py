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
# X-Trace-Id is not allowlisted: the host TraceIdMiddleware rewrites the current request's trace_id at the outer layer
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
    # actor is optional: user-less callers such as Celery omit this header, and the service side treats it as the default principal
    if context.actor_id is not None:
        headers["X-Model-Actor-ID"] = str(context.actor_id)
    if context.actor_name:
        # Header values must be ASCII (RFC 9110) while usernames may be non-ASCII: carried
        # as UTF-8 percent-encoding and unquoted service-side
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
        """Lend the connection pool: the invoke channel reuses the same socket parameters on a **dedicated pool** (each request carries its own timeout)."""

        return self._client

    def internal_url(self, path: str, query: bytes = b"") -> httpx.URL:
        # Normalize an empty query string to "no ?": httpx copy_with(query=None) keeps the original value semantics
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
    """Sync twin: the only egress for host management-plane callers (sync routes / premium compensating orchestration) to internal endpoints.

    Path mapping and internal header injection reuse the same primitives (behavior
    identical to the async pool down to the letter); it owns a dedicated
    ``httpx.Client`` (httpx sync clients can be shared across threads, and
    management-plane ``def`` routes run on the anyio thread pool). The sync path
    never streams: the response body is fully read here, so callers receive the
    final envelope.
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
        """Lend the connection pool: the sync invoke channel reuses the same socket parameters on a **dedicated pool** (each request carries its own timeout)."""

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

"""HTTP client for the independent memory profile service."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

from .contracts import MemoryProfileAuthSource, MemoryProfileRawResponse
from .errors import (
    MemoryProfileProtocolError,
    MemoryProfileTimeoutError,
    MemoryProfileUnavailableError,
)

logger = logging.getLogger(__name__)

_REQUIRED_ENVELOPE_FIELDS = frozenset({"code", "msg", "data", "error", "time"})


def _internal_path(external_path: str) -> str:
    """Map a caller's own route path onto the service's internal face.

    The service answers on one internal prefix, so which of the API's two
    external faces the caller used — ``/api`` (user JWT) or ``/v1`` (API key) —
    survives only as ``x-source``, never in the path.
    """

    for prefix in ("/api", "/v1"):
        if external_path == prefix or external_path.startswith(prefix + "/"):
            return "/internal/v1" + external_path[len(prefix) :]
    if external_path.startswith("/internal/v1/"):
        return external_path
    raise ValueError(
        "Memory profile route path must start with /api, /v1, or /internal/v1"
    )


class MemoryProfileServiceClient:
    """Own a process-level HTTP pool for memory-profile calls."""

    internal_path = staticmethod(_internal_path)

    def __init__(
        self,
        *,
        base_url: str,
        timeout: httpx.Timeout,
        limits: httpx.Limits,
        health_timeout: float,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout,
            limits=limits,
            trust_env=False,
        )
        self._health_timeout = health_timeout

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        base_url: str | None = None,
    ) -> MemoryProfileServiceClient:
        return cls(
            base_url=base_url
            if base_url is not None
            else settings.MEMORY_PROFILE_BASE_URL,
            timeout=httpx.Timeout(
                connect=settings.MEMORY_PROFILE_CONNECT_TIMEOUT_SECONDS,
                pool=settings.MEMORY_PROFILE_POOL_TIMEOUT_SECONDS,
                read=settings.MEMORY_PROFILE_READ_TIMEOUT_SECONDS,
                write=settings.MEMORY_PROFILE_WRITE_TIMEOUT_SECONDS,
            ),
            limits=httpx.Limits(
                max_connections=settings.MEMORY_PROFILE_MAX_CONNECTIONS,
                max_keepalive_connections=(
                    settings.MEMORY_PROFILE_MAX_KEEPALIVE_CONNECTIONS
                ),
            ),
            health_timeout=settings.MEMORY_PROFILE_HEALTH_TIMEOUT_SECONDS,
        )

    def _url(self, path: str) -> httpx.URL:
        return self._client.base_url.copy_with(path=path)

    async def request(
        self,
        *,
        method: str,
        path: str,
        workspace_id: str,
        tenant_id: str,
        actor_id: str,
        source: MemoryProfileAuthSource,
        trace_id: str,
        language_type: str | None = None,
        query: Mapping[str, Any] | None = None,
        payload: Any | None = None,
        raw: bool = False,
    ) -> dict[str, Any] | MemoryProfileRawResponse:
        """Forward one caller route and return its ApiResponse object.

        ``path`` is the caller's own request path; it is mapped onto the service's
        internal prefix and forwarded unchanged after that, so no local route table
        has to grow with the service. The caller reports one fact about itself:
        ``source`` (how it authenticated), carried as the generic ``x-source``.
        It also decides both the name and the meaning of the acting id below.

        With ``raw=True`` the response body is returned as already-serialized
        bytes, after the same envelope validation. Callers that forward the
        payload (the API proxy) then skip a second encode.
        """

        path = _internal_path(path)
        http_method = method.upper()

        # The acting id travels under the header its own face's terminator uses:
        # ``x-api-key-id`` naming the key that authenticated on the API-key face,
        # ``x-user-id`` naming the user everywhere else. Choosing the name here keeps
        # a caller from pairing one face's header with the other face's value — the
        # service resolves ``x-user-id`` first, so a mismatched pair would silently
        # read as a user.
        actor_header = (
            "x-api-key-id" if source == MemoryProfileAuthSource.APIKEY else "x-user-id"
        )
        headers = {
            "Accept": "application/json",
            "x-tenant-id": tenant_id,
            "x-workspace-id": workspace_id,
            "x-source": source.value,
            "X-Trace-Id": trace_id,
            actor_header: actor_id,
        }
        if language_type is not None:
            headers["X-Language-Type"] = language_type

        params = None
        if query:
            params = {key: value for key, value in query.items() if value is not None}

        started_at = time.perf_counter()
        try:
            response = await self._client.request(
                http_method,
                self._url(path),
                headers=headers,
                params=params,
                json=payload,
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "memory_profile_http_failed method=%s path=%s error=%s "
                "elapsed_ms=%.2f trace_id=%s",
                http_method,
                path,
                type(exc).__name__,
                (time.perf_counter() - started_at) * 1000,
                trace_id,
            )
            raise MemoryProfileTimeoutError(
                "Memory profile service request timed out"
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "memory_profile_http_failed method=%s path=%s error=%s "
                "elapsed_ms=%.2f trace_id=%s",
                http_method,
                path,
                type(exc).__name__,
                (time.perf_counter() - started_at) * 1000,
                trace_id,
            )
            raise MemoryProfileUnavailableError(
                "Memory profile service is unavailable"
            ) from exc

        try:
            try:
                envelope = response.json()
            except ValueError as exc:
                raise MemoryProfileProtocolError(
                    "Memory profile service returned invalid JSON"
                ) from exc
            if not isinstance(envelope, dict):
                raise MemoryProfileProtocolError(
                    "Memory profile service response must be a JSON object"
                )
            if not _REQUIRED_ENVELOPE_FIELDS.issubset(envelope) or not isinstance(
                envelope.get("code"), int
            ):
                raise MemoryProfileProtocolError(
                    "Memory profile service returned an invalid ApiResponse envelope"
                )
            logger.info(
                "memory_profile_call_completed method=%s path=%s status=%s "
                "business_code=%s elapsed_ms=%.2f trace_id=%s",
                http_method,
                path,
                response.status_code,
                envelope["code"],
                (time.perf_counter() - started_at) * 1000,
                trace_id,
            )
            # Business failures deliberately retain the legacy envelope even when
            # the internal service uses a matching HTTP 4xx/5xx status. Transport
            # and malformed responses are the only cases mapped to SERVICE_UNAVAILABLE.
            if raw:
                # The envelope was already serialized by the service; forwarding the
                # bytes skips FastAPI's response_model dump and json.dumps (~1.1ms
                # for a 74KB graph payload).
                return MemoryProfileRawResponse(
                    body=response.content,
                    media_type=response.headers.get("content-type", "application/json"),
                )
            return envelope
        finally:
            await response.aclose()

    async def ready(self) -> bool:
        try:
            response = await self._client.get(
                self._url("/internal/v1/health/ready"),
                timeout=self._health_timeout,
            )
        except httpx.HTTPError:
            return False
        try:
            return response.status_code == 200
        finally:
            await response.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

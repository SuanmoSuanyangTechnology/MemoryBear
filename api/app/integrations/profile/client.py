"""HTTP client for the independent memory profile service."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

from .errors import (
    MemoryProfileProtocolError,
    MemoryProfileTimeoutError,
    MemoryProfileUnavailableError,
)

logger = logging.getLogger(__name__)

_ANALYTICS_PATHS = frozenset(
    {
        "memory_insight",
        "user_summary",
        "node_statistics",
        "workspace_statistics",
        "graph_data",
        "community_graph",
    }
)


class MemoryProfileServiceClient:
    """Own a process-level HTTP pool for memory analytics calls."""

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
        operation: str,
        workspace_id: str,
        tenant_id: str,
        actor_id: str,
        source: str,
        trace_id: str,
        language_type: str | None = None,
        query: Mapping[str, Any] | None = None,
        payload: Any | None = None,
    ) -> dict[str, Any]:
        """Call one fixed analytics operation and return its ApiResponse object."""

        if operation not in _ANALYTICS_PATHS:
            raise ValueError(f"Unsupported memory profile operation: {operation}")

        path = f"/internal/v1/memory/analytics/{operation}"
        headers = {
            "Accept": "application/json",
            "X-Memory-Workspace-ID": workspace_id,
            "X-Memory-Tenant-ID": tenant_id,
            "X-Memory-Actor-ID": actor_id,
            "X-Memory-Source": source,
            "X-Trace-Id": trace_id,
        }
        if language_type is not None:
            headers["X-Language-Type"] = language_type

        params = None
        if query:
            params = {key: value for key, value in query.items() if value is not None}

        started_at = time.perf_counter()
        try:
            response = await self._client.request(
                method.upper(),
                self._url(path),
                headers=headers,
                params=params,
                json=payload,
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "memory_profile_http_failed method=%s path=%s error=%s "
                "elapsed_ms=%.2f trace_id=%s",
                method.upper(),
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
                method.upper(),
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
            required_fields = {"code", "msg", "data", "error", "time"}
            if not required_fields.issubset(envelope) or not isinstance(
                envelope.get("code"), int
            ):
                raise MemoryProfileProtocolError(
                    "Memory profile service returned an invalid ApiResponse envelope"
                )
            logger.info(
                "memory_profile_call_completed method=%s path=%s status=%s "
                "business_code=%s elapsed_ms=%.2f trace_id=%s",
                method.upper(),
                path,
                response.status_code,
                envelope["code"],
                (time.perf_counter() - started_at) * 1000,
                trace_id,
            )
            # Business failures deliberately retain the legacy envelope even when
            # the internal service uses a matching HTTP 4xx/5xx status. Transport
            # and malformed responses are the only cases mapped to SERVICE_UNAVAILABLE.
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

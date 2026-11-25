"""Async client for the optional memory-enterprise service.

The open build owns no enterprise tables. An enterprise deployment runs the
closed-source ``memory-enterprise`` service, which owns the plan/subscription
and resource-pack tables, and points this service at it — so this process never
reads a schema it does not own.

This module is transport only: it raises on failure. The fallback policy (use
the community free plan instead of failing the request) lives in
``src/services/quota``.

The HTTP pool is per event loop (httpx ``AsyncClient`` connections are bound to
the loop that created them) and is created lazily on first use, mirroring
``ModelServiceManager``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import httpx

from src.config import ProfileSettings

#: Effective memory-limit endpoint on the enterprise service.
MEMORY_LIMITS_PATH = "/internal/v1/quota/tenants/{tenant_id}/memory-limits"


class MemoryEnterpriseManager:
    """Lazily own the enterprise-service HTTP pool."""

    def __init__(
        self,
        settings: ProfileSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        """Whether an enterprise service is configured at all."""

        return bool(self._settings.memory_enterprise_base_url)

    async def client(self) -> httpx.AsyncClient:
        current_loop = asyncio.get_running_loop()
        if self._client is not None and self._loop is not current_loop:
            raise RuntimeError("Memory enterprise client cannot be shared across event loops")
        async with self._lock:
            if self._client is None:
                headers = {}
                if self._settings.memory_enterprise_token:
                    headers["X-Internal-Token"] = self._settings.memory_enterprise_token
                self._client = httpx.AsyncClient(
                    base_url=self._settings.memory_enterprise_base_url.rstrip("/") + "/",
                    timeout=httpx.Timeout(self._settings.memory_enterprise_timeout_seconds),
                    headers=headers,
                    transport=self._transport,
                    trust_env=False,
                )
                self._loop = current_loop
            return self._client

    async def memory_limits(self, tenant_id: UUID) -> dict[str, Any]:
        """Fetch one tenant's effective limits, split by source.

        Raises on a transport failure or an unusable payload; the caller decides
        how to degrade. The tenant travels in the path because this call is
        service-to-service and the gateway does not inject a tenant header.
        """

        response = await (await self.client()).get(MEMORY_LIMITS_PATH.format(tenant_id=tenant_id))
        response.raise_for_status()
        payload: Any = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError(
                f"memory-enterprise returned an unexpected payload: {type(data).__name__}"
            )
        return data

    async def aclose(self) -> None:
        client, self._client = self._client, None
        self._loop = None
        if client is not None:
            await client.aclose()

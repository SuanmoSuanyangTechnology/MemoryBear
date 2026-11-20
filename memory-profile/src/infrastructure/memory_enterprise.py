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


#: Bounded metadata-scope endpoint (the enterprise service owns the tables).
METADATA_SCOPE_PATH = "/internal/v1/ontology/metadata-scope"


#: Retrieval-failure alerts handed to the enterprise notification center. The
#: enterprise service owns the Redis obligation and the Celery dispatch; this
#: call is transport only.
RETRIEVAL_ALERT_PATH = "/internal/v1/notifications/retrieval-alerts"


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
    async def metadata_scope(
        self, *, app_id: UUID, end_user_id: UUID
    ) -> dict[str, Any] | None:
        """Resolve one app's ontology boundary for an end user.

        Returns ``None`` when the service has no binding for the pair; transport
        or payload failures raise so the caller can fall back to default_only.
        """

        response = await (await self.client()).get(
            METADATA_SCOPE_PATH,
            params={"app_id": str(app_id), "end_user_id": str(end_user_id)},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("memory-enterprise returned an invalid metadata scope")
        scope = payload.get("scope")
        return scope if isinstance(scope, dict) else None

    async def scene_names(
        self, *, tenant_id: UUID, scene_ids: list[str]
    ) -> dict[str, str]:
        """Scene id → name for the tenant's visible scenes (enterprise-owned)."""

        if not scene_ids:
            return {}
        response = await (await self.client()).post(
            "/internal/v1/ontology/scene-names",
            headers={"X-Tenant-Id": str(tenant_id)},
            json={"scene_ids": scene_ids},
        )
        response.raise_for_status()
        payload = response.json()
        names = payload.get("names") if isinstance(payload, dict) else None
        return {str(key): str(value) for key, value in (names or {}).items()}

    async def report_retrieval_alert(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        end_user_id: str,
        operation_id: str,
        error_code: str,
        stage: str,
        impact: str,
        model_type: str,
    ) -> dict[str, Any]:
        """Hand one degraded-retrieval alert to the enterprise service.

        The enterprise service persists the notification obligation and
        dispatches the API worker's emit task. Raises on a transport failure,
        a business rejection (non-zero ``code``) or an unusable payload; the
        caller treats the alert as not delivered.
        """

        response = await (await self.client()).post(
            RETRIEVAL_ALERT_PATH,
            json={
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "end_user_id": end_user_id,
                "operation_id": operation_id,
                "error_code": error_code,
                "stage": stage,
                "impact": impact,
                "model_type": model_type,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise ValueError("memory-enterprise rejected the retrieval alert")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("memory-enterprise returned an unexpected alert payload")
        return data

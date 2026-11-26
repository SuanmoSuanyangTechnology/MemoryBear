"""Async client for the optional memory-enterprise service.

The open build owns no enterprise tables. An enterprise deployment runs the
closed-source ``memory-enterprise`` service for quotas and ontology data.

Calls authenticate with the deployment-wide **shared internal token**: every
request carries ``X-Internal-Token: $MEMORY_INTERNAL_TOKEN`` and the tenant it
acts for in ``X-Tenant-Id`` (memory-enterprise's shared-token branch has no JWT
claims, so the header is the tenant context). memory-profile, memory-jobs and
memory-enterprise all read the same ``MEMORY_INTERNAL_TOKEN``.

This module is transport only. Each use case decides how to handle failures.

The HTTP pool is per event loop (httpx ``AsyncClient`` connections are bound to
the loop that created them) and is created lazily on first use, mirroring
``ModelServiceManager``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

import httpx

from src.common.observability.context import get_trace_id
from src.common.responses import ApiResponse
from src.config import ProfileSettings
from src.schemas.emotion_reply import AppEmotionStrategy, EmotionPolicy

logger = logging.getLogger(__name__)

#: Effective memory-limit endpoint on the enterprise service.
MEMORY_LIMITS_PATH = "/internal/v1/quota/tenants/{tenant_id}/memory-limits"


#: Bounded metadata-scope endpoint (the enterprise service owns the tables).
METADATA_SCOPE_PATH = "/internal/v1/ontology/metadata-scope"

#: System-default emotion policy endpoint.
DEFAULT_EMOTION_POLICY_PATH = "/internal/v1/ontology/default-emotion-policies/{emotion_code}"


#: Retrieval-failure alerts handed to the enterprise notification center. The
#: enterprise service owns the Redis obligation and the Celery dispatch; this
#: call is transport only.
RETRIEVAL_ALERT_PATH = "/internal/v1/notifications/retrieval-alerts"


def _secret_value(value: object) -> str:
    """Read a plain string or a ``SecretStr`` without importing pydantic here."""

    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return str(value or "")


class MemoryEnterpriseManager:
    """Lazily own the enterprise-service HTTP pool and its shared-token credential."""

    def __init__(
        self,
        settings: ProfileSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._token = _secret_value(settings.memory_internal_token)
        if settings.memory_enterprise_base_url and not self._token:
            logger.warning(
                "memory-enterprise is configured but MEMORY_INTERNAL_TOKEN is "
                "missing; enterprise calls are disabled"
            )
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        """Whether the service URL and the shared token are both configured."""

        return bool(self._settings.memory_enterprise_base_url and self._token)

    def _auth_headers(
        self, *, tenant_id: UUID | str, workspace_id: UUID | str | None = None
    ) -> dict[str, str]:
        """Authenticate one call and state the tenant it acts for.

        The shared-token branch has no claims, so memory-enterprise reads the
        tenant from ``X-Tenant-Id`` (and the workspace from ``X-Workspace-Id``
        when the caller knows it).
        """

        if not self._token:
            raise RuntimeError("MEMORY_INTERNAL_TOKEN is not configured")
        headers = {
            "X-Internal-Token": self._token,
            "X-Tenant-Id": str(tenant_id),
        }
        if workspace_id:
            headers["X-Workspace-Id"] = str(workspace_id)
        return headers

    async def client(self) -> httpx.AsyncClient:
        current_loop = asyncio.get_running_loop()
        if self._client is not None and self._loop is not current_loop:
            raise RuntimeError(
                "Memory enterprise client cannot be shared across event loops"
            )
        async with self._lock:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    base_url=(
                        self._settings.memory_enterprise_base_url.rstrip("/") + "/"
                    ),
                    timeout=httpx.Timeout(
                        self._settings.memory_enterprise_timeout_seconds
                    ),
                    transport=self._transport,
                    trust_env=False,
                )
                self._loop = current_loop
            return self._client

    async def memory_limits(self, tenant_id: UUID) -> dict[str, Any]:
        """Fetch one tenant's effective limits, split by source.

        Raises on a transport failure or an unusable payload; the caller decides
        how to degrade. The tenant travels in the path and in ``X-Tenant-Id``.
        """

        response = await (await self.client()).get(
            MEMORY_LIMITS_PATH.format(tenant_id=tenant_id),
            headers=self._auth_headers(tenant_id=tenant_id),
        )
        response.raise_for_status()
        payload: Any = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError(
                f"memory-enterprise returned an unexpected payload: {type(data).__name__}"
            )
        return data

    async def emotion_policy(
        self, emotion_code: str, trace_id: str, tenant_id: UUID, workspace_id: UUID
    ) -> EmotionPolicy | None:
        headers = self._auth_headers(tenant_id=tenant_id, workspace_id=workspace_id)
        headers["X-Trace-Id"] = trace_id
        async with asyncio.timeout(self._settings.memory_enterprise_timeout_seconds):
            response = await (await self.client()).get(
                DEFAULT_EMOTION_POLICY_PATH.format(emotion_code=emotion_code),
                headers=headers,
            )
        response.raise_for_status()
        reply = ApiResponse[EmotionPolicy | None].model_validate(response.json(), strict=True)
        if reply.code != 0:
            raise ValueError("memory-enterprise returned an unsuccessful emotion policy response")
        return reply.data

    async def app_emotion_policy(
        self, app_id: UUID, tenant_id: UUID, workspace_id: UUID, emotion_code: str
    ) -> AppEmotionStrategy | None:
        headers = self._auth_headers(tenant_id=tenant_id, workspace_id=workspace_id)
        headers["X-Trace-Id"] = get_trace_id()
        async with asyncio.timeout(self._settings.memory_enterprise_timeout_seconds):
            response = await (await self.client()).get(
                "/internal/v1/ontology/app-emotion-policy",
                params={"app_id": str(app_id), "emotion_code": emotion_code},
                headers=headers,
            )
        response.raise_for_status()
        reply = ApiResponse[AppEmotionStrategy | None].model_validate(response.json(), strict=True)
        if reply.code != 0:
            raise ValueError("memory-enterprise rejected the application policy lookup")
        return reply.data

    async def metadata_scope(
        self,
        *,
        tenant_id: UUID,
        app_id: UUID,
        end_user_id: UUID,
        workspace_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        """Resolve one app's ontology boundary for an end user.

        Returns ``None`` when the service has no binding for the pair; transport
        or payload failures raise so the caller can fall back to default_only.
        """

        response = await (await self.client()).get(
            METADATA_SCOPE_PATH,
            headers=self._auth_headers(
                tenant_id=tenant_id, workspace_id=workspace_id
            ),
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
        if not self.enabled:
            return {}
        response = await (await self.client()).post(
            "/internal/v1/ontology/scene-names",
            headers=self._auth_headers(tenant_id=tenant_id),
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
            headers=self._auth_headers(
                tenant_id=tenant_id, workspace_id=workspace_id
            ),
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

    async def aclose(self) -> None:
        client, self._client = self._client, None
        self._loop = None
        if client is not None:
            await client.aclose()

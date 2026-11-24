"""Async client for the optional memory-enterprise service.

The open build owns no enterprise tables. An enterprise deployment runs the
closed-source ``memory-enterprise`` service, which owns the plan/subscription
and resource-pack tables, and points this service at it — so this process never
reads a schema it does not own.

Calls authenticate with a **service-to-service internal JWT**, the same mechanism
memory-profile already uses for file-service uploads: `LocalTokenIssuer` signs an
RS256 token with the deployment's shared ``INTERNAL_ISSUER_PRIVATE_KEY``
(``iss=memory-profile``, ``aud=MEMORY_ENTERPRISE_AUDIENCE``), and memory-enterprise
verifies it through identity's JWKS and its caller ACL. No shared
``X-Internal-Token`` is sent: the target rejects two credentials at once, and the
JWT carries the tenant each call acts for.

This module is transport only: it raises on failure. The fallback policy (use
the community free plan instead of failing the request) lives in
``src/services/quota``.

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
from auth_sdk.schema import UserContext
from auth_sdk.token import LocalTokenIssuer

from src.config import ProfileSettings

logger = logging.getLogger(__name__)

#: Effective memory-limit endpoint on the enterprise service.
MEMORY_LIMITS_PATH = "/internal/v1/quota/tenants/{tenant_id}/memory-limits"


#: Bounded metadata-scope endpoint (the enterprise service owns the tables).
METADATA_SCOPE_PATH = "/internal/v1/ontology/metadata-scope"


#: Retrieval-failure alerts handed to the enterprise notification center. The
#: enterprise service owns the Redis obligation and the Celery dispatch; this
#: call is transport only.
RETRIEVAL_ALERT_PATH = "/internal/v1/notifications/retrieval-alerts"


class MemoryEnterpriseManager:
    """Lazily own the enterprise-service HTTP pool and its internal-JWT issuer."""

    def __init__(
        self,
        settings: ProfileSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        issuer: Any | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._issuer = issuer
        if self._issuer is None:
            secret = settings.internal_issuer_private_key
            private_key = (
                secret.get_secret_value()
                if hasattr(secret, "get_secret_value")
                else str(secret or "")
            )
            if private_key:
                self._issuer = LocalTokenIssuer(
                    private_key=private_key,
                    kid=settings.internal_issuer_kid,
                    ttl=settings.internal_token_ttl_seconds,
                    issuer=settings.service_name,
                )
        if settings.memory_enterprise_base_url and self._issuer is None:
            logger.warning(
                "memory-enterprise is configured but INTERNAL_ISSUER_PRIVATE_KEY "
                "is missing; enterprise calls are disabled"
            )
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        """Whether the service URL and the internal issuer are both configured."""

        return bool(self._settings.memory_enterprise_base_url and self._issuer is not None)

    def _auth_headers(
        self, *, tenant_id: UUID | str, workspace_id: UUID | str | None = None
    ) -> dict[str, str]:
        """Sign one call for the tenant it acts for.

        The token ``sub`` is this service, not an end user: the ACL keys on
        ``iss`` and the endpoint, and the target only needs a stable caller
        identity. ``tenant_id`` (and ``workspace_id`` when known) travel as
        claims, which memory-enterprise treats as the authoritative context.
        """

        if self._issuer is None:
            raise RuntimeError("Internal issuer key is not configured")
        identity = UserContext(
            user_id=self._settings.service_name,
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id) if workspace_id else "",
        )
        token = self._issuer.issue_internal_token(
            identity, self._settings.memory_enterprise_audience
        )
        return {"Authorization": f"Bearer {token}"}

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
        how to degrade. The tenant travels in the path because this call is
        service-to-service and the gateway does not inject a tenant header.
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
"""Blocking client for the optional memory-enterprise service.

The open build has no enterprise tables. An enterprise deployment runs the
closed-source ``memory-enterprise`` service, which owns those tables and their
migrations, and points this worker at it — so the worker never reads a schema it
does not own.

Calls authenticate with a **service-to-service internal JWT**, the same mechanism
the other services use: `LocalTokenIssuer` signs an RS256 token with the
deployment's shared ``INTERNAL_ISSUER_PRIVATE_KEY`` (``iss=JOBS_SERVICE_NAME``,
``aud=MEMORY_ENTERPRISE_AUDIENCE``), and memory-enterprise verifies it through
identity's JWKS and its caller ACL. No shared ``X-Internal-Token`` is sent: the
target rejects two credentials at once, and the JWT carries the tenant each call
acts for.

This module is transport and lifecycle only: it raises on failure. The
best-effort policy (degrade instead of failing the task) lives in the adapters
that implement an application port, such as
``MemoryEnterpriseSceneNames``.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

import httpx
from auth_sdk.schema import UserContext
from auth_sdk.token import LocalTokenIssuer, TokenIssuer

from src.config import JobsSettings
from src.domain.shared.ids import TenantId

logger = logging.getLogger(__name__)

#: Scene-name resolution endpoint on the enterprise service.
SCENE_NAMES_PATH = "/internal/v1/ontology/scene-names"


class MemoryEnterpriseManager:
    """Lazily-created, thread-safe HTTP client for the enterprise service."""

    def __init__(
        self,
        settings: JobsSettings,
        *,
        transport: httpx.BaseTransport | None = None,
        issuer: TokenIssuer | None = None,
    ) -> None:
        self.settings = settings
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
                "is missing; enterprise calls will degrade as unavailable"
            )
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """Whether an enterprise service is configured at all.

        A configured URL without the issuer key is still "enabled": calls fail
        with ``ValueError`` so the adapters degrade as *unavailable* (unknown
        quota, stored scene keys kept) instead of mistaking the misconfiguration
        for "no subscription" or "scenes deleted".
        """

        return bool(self.settings.memory_enterprise_base_url)

    @property
    def initialized(self) -> bool:
        return self._client is not None

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
            raise ValueError(
                "INTERNAL_ISSUER_PRIVATE_KEY is required for memory-enterprise calls"
            )
        identity = UserContext(
            user_id=self.settings.service_name,
            tenant_id=str(tenant_id),
            workspace_id=str(workspace_id) if workspace_id else "",
        )
        token = self._issuer.issue_internal_token(
            identity, self.settings.memory_enterprise_audience
        )
        return {"Authorization": f"Bearer {token}"}

    def client(self) -> httpx.Client:
        client = self._client
        if client is not None:
            return client
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(
                    base_url=self.settings.memory_enterprise_base_url.rstrip("/"),
                    timeout=self.settings.memory_enterprise_timeout_seconds,
                    transport=self._transport,
                )
            return self._client

    def scene_names(
        self, scene_ids: Sequence[UUID], tenant_id: TenantId
    ) -> Mapping[str, str]:
        """Resolve ``scene_type_id`` → scene name for one tenant.

        Raises on a transport failure or an unusable payload; the adapter decides
        how a caller should degrade. The tenant travels as the JWT's
        ``tenant_id`` claim, so the service scopes the lookup to that tenant's
        own scenes plus the shared system default; a caller can never enumerate
        another tenant's scenes through this path.
        """

        response = self.client().post(
            SCENE_NAMES_PATH,
            json={"scene_ids": [str(scene_id) for scene_id in scene_ids]},
            headers=self._auth_headers(tenant_id=tenant_id),
        )
        response.raise_for_status()
        payload: Any = response.json()

        names = payload.get("names") if isinstance(payload, dict) else None
        if not isinstance(names, dict):
            raise ValueError(
                "memory-enterprise returned an unexpected payload: "
                f"{type(names).__name__}"
            ) from None
        return {
            key: value
            for key, value in names.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def reset_after_fork(self) -> None:
        """A forked child must not reuse the parent's connection pool."""

        client, self._client = self._client, None
        if client is not None:
            client.close()

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            client.close()

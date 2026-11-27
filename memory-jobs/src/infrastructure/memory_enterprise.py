"""Blocking client for the optional memory-enterprise service.

The open build has no enterprise tables. An enterprise deployment runs the
closed-source ``memory-enterprise`` service, which owns those tables and their
migrations, and points this worker at it — so the worker never reads a schema it
does not own.

Calls authenticate with the deployment-wide **shared internal token**: every
request carries ``X-Internal-Token: $MEMORY_INTERNAL_TOKEN`` and the tenant it
acts for in ``X-Tenant-Id`` (memory-enterprise's shared-token branch has no JWT
claims, so the header is the tenant context). memory-profile, memory-jobs and
memory-enterprise all read the same ``MEMORY_INTERNAL_TOKEN``.

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

from src.config import JobsSettings
from src.domain.shared.ids import TenantId

logger = logging.getLogger(__name__)

#: Scene-name resolution endpoint on the enterprise service.
SCENE_NAMES_PATH = "/internal/v1/ontology/scene-names"

def _secret_value(value: object) -> str:
    """Read a plain string or a ``SecretStr`` without importing pydantic here."""

    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return str(getter())
    return str(value or "")


class MemoryEnterpriseManager:
    """Lazily-created, thread-safe HTTP client for the enterprise service."""

    def __init__(
        self,
        settings: JobsSettings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._token = _secret_value(settings.memory_internal_token)
        if settings.memory_enterprise_base_url and not self._token:
            logger.warning(
                "memory-enterprise is configured but MEMORY_INTERNAL_TOKEN is "
                "missing; enterprise calls will degrade as unavailable"
            )
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """Whether an enterprise service is configured at all.

        A configured URL without the shared token is still "enabled": calls fail
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
        """Authenticate one call and state the tenant it acts for.

        The shared-token branch has no claims, so memory-enterprise reads the
        tenant from ``X-Tenant-Id`` (and the workspace from ``X-Workspace-Id``
        when the caller knows it).
        """

        if not self._token:
            raise ValueError(
                "MEMORY_INTERNAL_TOKEN is required for memory-enterprise calls"
            )
        headers = {
            "X-Internal-Token": self._token,
            "X-Tenant-Id": str(tenant_id),
        }
        if workspace_id:
            headers["X-Workspace-Id"] = str(workspace_id)
        return headers

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
        how a caller should degrade. The tenant travels as ``X-Tenant-Id``, so
        the service scopes the lookup to that tenant's own scenes plus the
        shared system default; a caller can never enumerate another tenant's
        scenes through this path.
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

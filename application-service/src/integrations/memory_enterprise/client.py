"""HTTP client of memory-enterprise's internal faces called by this service.

Ontology tables, their app bindings and the emotion reply policies belong to
the memory domain; this service reaches them over memory-enterprise's internal
routes instead of reading the tables directly. The credential is the scoped
application-service token, which memory-enterprise admits only on the
app-service-scoped routes; this service never holds the shared internal token.

Community deployments run without memory-enterprise: every call raises
``MemoryEnterpriseUnavailableError`` when the channel is unconfigured, and the
callers keep their degradation (the ontology filters report unavailable and
the emotion policy read falls back to the bundled community JSON).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from ...config import AppServiceSettings
from ...trace import get_trace_id

ONTOLOGY_BATCH_PATH = "/internal/v1/apps/ontology-batch"
ONTOLOGY_FILTER_PATH = "/internal/v1/apps/ontology-filter"
ONTOLOGY_BIND_PATH = "/internal/v1/apps/ontology-bind"
ONTOLOGY_UNBIND_PATH = "/internal/v1/apps/ontology-unbind"
APP_EMOTION_POLICY_PATH = "/internal/v1/ontology/app-emotion-policy"
_REQUIRED_ENVELOPE_FIELDS = frozenset({"code", "msg", "data", "error", "time"})


class MemoryEnterpriseUnavailableError(RuntimeError):
    """The memory-enterprise channel is unconfigured or unreachable."""


class MemoryEnterpriseProtocolError(RuntimeError):
    """The service answered outside the ApiResponse envelope."""


class MemoryEnterpriseClient:
    """Process-level sync + async client; transports are test seams."""

    def __init__(
        self,
        base_url: str,
        *,
        application_service_token: str = "",
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 30.0,
        write_timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or "").strip().rstrip("/")
        self._token = (application_service_token or "").strip()
        self._timeout = httpx.Timeout(
            read_timeout_seconds,
            connect=connect_timeout_seconds,
            write=write_timeout_seconds,
        )
        self._transport = transport
        self._async_transport = async_transport
        self._client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None

    @classmethod
    def from_settings(cls, settings: AppServiceSettings) -> MemoryEnterpriseClient:
        return cls(
            settings.memory_enterprise_base_url,
            application_service_token=(
                settings.memory_enterprise_application_service_token.get_secret_value()
            ),
            connect_timeout_seconds=settings.memory_enterprise_connect_timeout_seconds,
            read_timeout_seconds=settings.memory_enterprise_read_timeout_seconds,
            write_timeout_seconds=settings.memory_enterprise_write_timeout_seconds,
        )

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._token)

    @property
    def has_base_url(self) -> bool:
        return bool(self._base_url)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._transport,
                trust_env=False,
            )
        return self._client

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                transport=self._async_transport,
                trust_env=False,
            )
        return self._async_client

    def ontology_batch(
        self, *, app_ids: list[UUID], tenant_id: UUID
    ) -> dict[str, Any]:
        """Fetch the effective ontology sub-object per app; raw envelope."""

        return self._post(
            ONTOLOGY_BATCH_PATH,
            {"app_ids": [str(app_id) for app_id in app_ids]},
            tenant_id=tenant_id,
        )

    def ontology_filter(
        self,
        *,
        app_ids: list[UUID],
        tenant_id: UUID,
        ontology_status: str | None,
        field_search: str | None,
    ) -> dict[str, Any]:
        """Filter candidate apps by ontology status / field name; raw envelope."""

        return self._post(
            ONTOLOGY_FILTER_PATH,
            {
                "app_ids": [str(app_id) for app_id in app_ids],
                "ontology_status": ontology_status,
                "field_search": field_search,
            },
            tenant_id=tenant_id,
        )

    def ontology_bind(
        self,
        *,
        app_id: UUID,
        tenant_id: UUID,
        ontology_id: UUID,
    ) -> dict[str, Any]:
        """Write the first binding for a just-created app; raw envelope."""

        return self._post(
            ONTOLOGY_BIND_PATH,
            {
                "app_id": str(app_id),
                "tenant_id": str(tenant_id),
                "ontology_id": str(ontology_id),
            },
            tenant_id=tenant_id,
        )

    def ontology_unbind(self, *, app_id: UUID, tenant_id: UUID) -> dict[str, Any]:
        """Soft-delete the app's active binding after the local delete; raw envelope."""

        return self._post(
            ONTOLOGY_UNBIND_PATH,
            {"app_id": str(app_id), "tenant_id": str(tenant_id)},
            tenant_id=tenant_id,
        )

    async def app_emotion_policy(
        self,
        *,
        app_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        emotion_code: str,
    ) -> dict[str, Any]:
        """Resolve this app's strategy for one emotion; ``data`` is the strategy."""

        return await self._aget(
            APP_EMOTION_POLICY_PATH,
            params={"app_id": str(app_id), "emotion_code": emotion_code},
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

    def _headers(
        self, *, tenant_id: UUID, workspace_id: UUID | None = None
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-Internal-Token": self._token,
            "X-Tenant-Id": str(tenant_id),
        }
        if workspace_id is not None:
            headers["X-Workspace-Id"] = str(workspace_id)
        trace_id = get_trace_id()
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        return headers

    def _post(
        self, path: str, payload: dict[str, Any], *, tenant_id: UUID
    ) -> dict[str, Any]:
        """POST one app-service route and return its envelope; failures raise."""

        if not self.configured:
            raise MemoryEnterpriseUnavailableError(
                "memory enterprise service is not configured"
            )
        try:
            response = self._get_client().post(
                path, json=payload, headers=self._headers(tenant_id=tenant_id)
            )
        except httpx.HTTPError as error:
            raise MemoryEnterpriseUnavailableError(
                f"memory enterprise call failed: {error}"
            ) from error
        return _parse_envelope(response)

    async def _aget(
        self,
        path: str,
        *,
        params: dict[str, Any],
        tenant_id: UUID,
        workspace_id: UUID,
    ) -> dict[str, Any]:
        """GET one internal route and return its envelope; failures raise."""

        if not self.configured:
            raise MemoryEnterpriseUnavailableError(
                "memory enterprise service is not configured"
            )
        try:
            response = await self._get_async_client().get(
                path,
                params=params,
                headers=self._headers(
                    tenant_id=tenant_id, workspace_id=workspace_id
                ),
            )
        except httpx.HTTPError as error:
            raise MemoryEnterpriseUnavailableError(
                f"memory enterprise call failed: {error}"
            ) from error
        return _parse_envelope(response)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    async def aclose(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None


def _parse_envelope(response: httpx.Response) -> dict[str, Any]:
    """Validate the ApiResponse envelope; transport failures raise."""

    try:
        envelope = response.json()
    except ValueError as error:
        raise MemoryEnterpriseProtocolError(
            "memory enterprise returned invalid JSON"
        ) from error
    if (
        not isinstance(envelope, dict)
        or not _REQUIRED_ENVELOPE_FIELDS.issubset(envelope)
        or not isinstance(envelope.get("code"), int)
    ):
        raise MemoryEnterpriseProtocolError(
            "memory enterprise returned an invalid ApiResponse envelope"
        )
    return envelope


__all__ = [
    "APP_EMOTION_POLICY_PATH",
    "ONTOLOGY_BATCH_PATH",
    "ONTOLOGY_BIND_PATH",
    "ONTOLOGY_FILTER_PATH",
    "ONTOLOGY_UNBIND_PATH",
    "MemoryEnterpriseClient",
    "MemoryEnterpriseProtocolError",
    "MemoryEnterpriseUnavailableError",
]

"""HTTP client of memory-profile's internal faces called by this service.

The end-user memory-side effects execute inside memory-profile: it owns the
memory write locks (``memory_write:<end_user_id>``, shared with the monolith),
every derived memory artifact (graph reassignment, RAG migration, reflection
dispatch), the alias graph sync, the assembled domain display projection and
the long-term memory read pipeline. The end-user callbacks run on the
management plane (sync); the read callback runs inside the agent runtime
(async), so this client carries one sync and one async transport. Every failure
is raised so the caller decides the posture (the merge and info-update paths
fail closed, the projection read degrades to empty, the memory read fails the
tool call visibly).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from ...config import AppServiceSettings

MERGE_PATH = "/internal/v1/memory/merge"
READ_APP_SERVICE_PATH = "/internal/v1/memory/read/app-service"
DOMAIN_PROFILE_PATH = "/internal/v1/end-user/domain-profile"
INFO_UPDATE_PATH = "/internal/v1/end-user/info-update"
_SOURCE_INTERNAL = "in_api"
_REQUIRED_ENVELOPE_FIELDS = frozenset({"code", "msg", "data", "error", "time"})


class MemoryProfileUnavailableError(RuntimeError):
    """The profile channel is unconfigured or the call failed (fail-closed)."""


class MemoryProfileProtocolError(RuntimeError):
    """The profile service answered outside the ApiResponse envelope."""


class MemoryProfileClient:
    """Process-level sync client; ``transport`` is a test seam."""

    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout_seconds: float = 5.0,
        pool_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 120.0,
        write_timeout_seconds: float = 600.0,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or "").strip().rstrip("/")
        # Read stays the default phase; connect/pool keep their own short
        # budgets so an unreachable host fails fast, while write covers the
        # long merge transaction on the service side.
        self._timeout = httpx.Timeout(
            read_timeout_seconds,
            connect=connect_timeout_seconds,
            pool=pool_timeout_seconds,
            write=write_timeout_seconds,
        )
        self._transport = transport
        self._async_transport = async_transport
        self._client: httpx.Client | None = None
        self._async_client: httpx.AsyncClient | None = None

    @classmethod
    def from_settings(cls, settings: AppServiceSettings) -> MemoryProfileClient:
        return cls(
            settings.memory_profile_base_url,
            connect_timeout_seconds=settings.memory_profile_connect_timeout_seconds,
            pool_timeout_seconds=settings.memory_profile_pool_timeout_seconds,
            read_timeout_seconds=settings.memory_profile_read_timeout_seconds,
            write_timeout_seconds=settings.memory_profile_write_timeout_seconds,
        )

    @property
    def configured(self) -> bool:
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

    def merge_users(
        self,
        *,
        source_end_user_id: UUID,
        target_end_user_id: UUID,
        workspace_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
        trace_id: str,
    ) -> dict[str, Any]:
        """Merge one source end user into the target; returns the raw envelope.

        Business failures keep the legacy envelope (HTTP 200 + non-zero
        ``code``); only transport and malformed responses raise here.
        """

        payload = {
            "end_user_ids": [str(source_end_user_id)],
            "target": str(target_end_user_id),
        }
        return self._post(
            MERGE_PATH,
            payload,
            self._internal_headers(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                trace_id=trace_id,
            ),
        )

    async def read_app_service_memory(
        self,
        *,
        payload: dict[str, Any],
        workspace_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
        trace_id: str,
    ) -> dict[str, Any]:
        """Run the long-term memory read callback; returns the raw envelope.

        ``payload`` is the host ``MemoryReadRequest`` the read stores carried
        (``end_user_id`` / ``query`` / ``search_switch`` / caller-owned
        options); the face honours every field. Business failures keep the
        legacy envelope (HTTP 200 + non-zero ``code``); only transport and
        malformed responses raise here.
        """

        return await self._apost(
            READ_APP_SERVICE_PATH,
            payload,
            self._internal_headers(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                trace_id=trace_id,
            ),
        )

    def fetch_domain_profile(
        self,
        *,
        end_user_id: UUID,
        workspace_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
        trace_id: str,
    ) -> dict[str, Any]:
        """Fetch the assembled ``domain_specific_info`` projection; raw envelope."""

        return self._post(
            DOMAIN_PROFILE_PATH,
            {"end_user_id": str(end_user_id)},
            self._internal_headers(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                trace_id=trace_id,
            ),
        )

    def update_end_user_info(
        self,
        *,
        end_user_id: str,
        update_data: dict[str, Any],
        workspace_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
        trace_id: str,
    ) -> dict[str, Any]:
        """Apply an info update and its memory-side effect; raw envelope.

        ``update_data`` carries the writable fields only (``other_name`` /
        ``aliases``); memory-profile owns the PG write and the alias graph sync.
        """

        payload = {"end_user_id": str(end_user_id), **update_data}
        return self._post(
            INFO_UPDATE_PATH,
            payload,
            self._internal_headers(
                workspace_id=workspace_id,
                tenant_id=tenant_id,
                actor_id=actor_id,
                trace_id=trace_id,
            ),
        )

    def _internal_headers(
        self,
        *,
        workspace_id: UUID,
        tenant_id: UUID,
        actor_id: UUID,
        trace_id: str,
    ) -> dict[str, str]:
        """Identity headers memory-profile's direct/gateway face resolves."""

        return {
            "Accept": "application/json",
            "x-source": _SOURCE_INTERNAL,
            "x-user-id": str(actor_id),
            "x-tenant-id": str(tenant_id),
            "x-workspace-id": str(workspace_id),
            "X-Trace-Id": trace_id,
        }

    def _post(self, path: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        """POST one callback and return its envelope; every failure raises."""

        if not self.configured:
            raise MemoryProfileUnavailableError("memory profile service is not configured")

        try:
            response = self._get_client().post(path, json=payload, headers=headers)
        except httpx.HTTPError as error:
            raise MemoryProfileUnavailableError(
                f"memory profile call failed: {error}"
            ) from error
        return _parse_envelope(response)

    async def _apost(
        self, path: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        """Async twin of :meth:`_post` for the runtime-plane read callback."""

        if not self.configured:
            raise MemoryProfileUnavailableError("memory profile service is not configured")

        try:
            response = await self._get_async_client().post(
                path, json=payload, headers=headers
            )
        except httpx.HTTPError as error:
            raise MemoryProfileUnavailableError(
                f"memory profile call failed: {error}"
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
        raise MemoryProfileProtocolError(
            "memory profile returned invalid JSON"
        ) from error
    if (
        not isinstance(envelope, dict)
        or not _REQUIRED_ENVELOPE_FIELDS.issubset(envelope)
        or not isinstance(envelope.get("code"), int)
    ):
        raise MemoryProfileProtocolError(
            "memory profile returned an invalid ApiResponse envelope"
        )
    return envelope


def get_memory_profile_client() -> MemoryProfileClient | None:
    """Runtime-owned memory-profile client; ``None`` when the runtime is not assembled."""

    from ...runtime import get_process_runtime

    try:
        return get_process_runtime().memory_profile
    except Exception:
        return None


__all__ = [
    "DOMAIN_PROFILE_PATH",
    "INFO_UPDATE_PATH",
    "MERGE_PATH",
    "READ_APP_SERVICE_PATH",
    "MemoryProfileClient",
    "MemoryProfileProtocolError",
    "MemoryProfileUnavailableError",
    "get_memory_profile_client",
]

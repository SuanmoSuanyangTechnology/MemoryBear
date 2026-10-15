"""Outbound monolith upstream for cross-domain writes (coexistence period).

Some application-domain writes carry memory-side effects (Neo4j merge /
alias sync, ``memory_*`` reference migration, reflection dispatch) that this
service must not execute directly while the memory service contract is still
open. Those requests are forwarded verbatim to the monolith's API-key ``/v1``
face over the internal network (never through the gateway) and the response is
passed back unchanged. The transport is injectable so tests and the parity
battery can bridge the monolith app in-process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import AppServiceSettings

logger = logging.getLogger(__name__)


class UpstreamUnavailableError(RuntimeError):
    """The upstream channel is unconfigured or the call failed (fail-closed)."""


@dataclass(frozen=True)
class UpstreamResponse:
    status_code: int
    body: Any


class MonolithUpstream:
    """Lazy sync client for the monolith ``/v1`` face.

    Callers must end their own database transaction before forwarding: the
    forwarded request re-runs the full logic on the monolith side (including
    the identity advisory lock), and holding the same lock here would deadlock.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._client: httpx.Client | None = None

    @classmethod
    def from_settings(cls, settings: AppServiceSettings) -> MonolithUpstream:
        return cls(
            base_url=settings.application_service_upstream_base_url or "",
            api_key=settings.application_service_upstream_api_key.get_secret_value(),
            timeout_seconds=settings.application_service_upstream_timeout_seconds,
        )

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._api_key)

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._base_url,
                timeout=httpx.Timeout(self._timeout_seconds),
                transport=self._transport,
                headers={"X-API-Key": self._api_key},
            )
        return self._client

    def forward(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> UpstreamResponse:
        if not self.configured:
            raise UpstreamUnavailableError("application service upstream is not configured")
        try:
            response = self._get_client().request(method, path, json=json_body)
        except httpx.HTTPError as error:
            raise UpstreamUnavailableError(f"forward to monolith failed: {error}") from error
        try:
            body = response.json()
        except ValueError:
            body = {}
        return UpstreamResponse(status_code=response.status_code, body=body)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


__all__ = ["MonolithUpstream", "UpstreamResponse", "UpstreamUnavailableError"]

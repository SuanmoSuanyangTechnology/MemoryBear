"""Outbound port to the community market (market-service).

The market lives in a separate service with its own database and Alembic chain;
we only speak to it over its internal face (`/internal/v1/catalog/*`, contract in
`docs/应用共享与市场/接口文档.md` §6). That dependency is optional: the community
edition deploys without a market, so the port has an explicitly disabled
implementation that fails the switch-3 path closed with `6020` instead of
producing a half-wired feature.

Two rules the callers must respect (both come from the market's own contract):

1. **Never call the market inside a database transaction** (§9.2). Market calls
   time out at 10s; holding a connection/lock for that long starves the pool.
   Local state is committed first, then the HTTP call runs, and its outcome is
   recorded in `market_sync`.
2. **Retries reuse the exact bundle that was reviewed.** `content_hash` is part
   of the publication payload, so a re-export after the source release changed
   would produce a different hash and be rejected as a different submission.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class MarketUnavailableError(RuntimeError):
    """The market is disabled or unreachable (fail-closed for switch 3)."""


# Plugin-registry keys for the enterprise implementations. The community market's
# HTTP client and orchestration services live in enterprise-extensions (not in this
# repository), so core resolves them by name and never imports them.
MARKET_GATEWAY_FACTORY = "market.gateway_factory"
MARKET_SUBMISSION_FACTORY = "market.submission_service_factory"


@runtime_checkable
class MarketGateway(Protocol):
    """Internal-face operations we need from market-service."""

    @property
    def enabled(self) -> bool:
        """Whether a usable market endpoint is configured."""

    def submit_publication(
            self,
            *,
            tenant_id: str,
            actor_id: str,
            resource_type: str,
            local_resource_id: str,
            release_id: str,
            version: str,
            display: dict[str, Any],
            bundle: dict[str, Any],
    ) -> dict[str, Any]:
        """`POST /internal/v1/catalog/publications` → `{id, resource_id, version, status}`."""

    def withdraw_publication(
            self, *, tenant_id: str, actor_id: str, publication_id: str
    ) -> dict[str, Any]:
        """`POST /internal/v1/catalog/publications/{id}/withdraw`."""

    def get_publication_status(
            self, *, tenant_id: str, resource_type: str, local_resource_ids: list[str]
    ) -> list[dict[str, Any]]:
        """`GET /internal/v1/catalog/publications/status` (batch, ≤100 ids)."""

    def fetch_bundle(
            self, *, tenant_id: str, resource_type: str, resource_id: str, version: str
    ) -> dict[str, Any]:
        """`GET /internal/v1/catalog/resources/{id}/versions/{version}/bundle`."""

    def approve_submission(
            self,
            *,
            tenant_id: str,
            actor_id: str,
            submission_id: str,
            review_note: str | None = None,
            category_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        """`POST /internal/v1/admin/submissions/{id}/approve` — one version only.

        His endpoint cannot batch (§5.3), so the whole-package approve is an
        orchestration in our process (§6.3-7-1): dependencies first, app last.
        """

    def register_installation(
            self, *, tenant_id: str, actor_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """`POST /internal/v1/catalog/installations` — best-effort, never blocking."""

    def remove_installation(
            self, *, tenant_id: str, actor_id: str, resource_type: str, local_resource_id: str
    ) -> None:
        """`DELETE /internal/v1/catalog/installations/{resource_type}/{local_resource_id}`."""

    def close(self) -> None:
        """Release transport resources."""


class DisabledMarketGateway:
    """Community-edition default: every market call fails closed.

    Only switch 3 (community market) is affected; the tenant resource center
    (switches 1/2) works without a market.
    """

    enabled = False

    def _reject(self) -> None:
        raise MarketUnavailableError(
            "社区市场未在当前部署中启用（APPLICATION_SERVICE_MARKET_MODE=disabled）"
        )

    def submit_publication(self, **_kwargs: Any) -> dict[str, Any]:
        self._reject()

    def withdraw_publication(self, **_kwargs: Any) -> dict[str, Any]:
        self._reject()

    def get_publication_status(self, **_kwargs: Any) -> list[dict[str, Any]]:
        self._reject()

    def fetch_bundle(self, **_kwargs: Any) -> dict[str, Any]:
        self._reject()

    def approve_submission(self, **_kwargs: Any) -> dict[str, Any]:
        self._reject()

    def register_installation(self, **_kwargs: Any) -> dict[str, Any]:
        self._reject()

    def remove_installation(self, **_kwargs: Any) -> None:
        self._reject()

    def close(self) -> None:
        return None


__all__ = [
    "DisabledMarketGateway",
    "MARKET_GATEWAY_FACTORY",
    "MARKET_SUBMISSION_FACTORY",
    "MarketGateway",
    "MarketUnavailableError",
]
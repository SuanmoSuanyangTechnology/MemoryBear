"""Cross-service market contract. The community build only ships the disabled
implementation; the enterprise HTTP implementation lives in the private
enterprise-extensions package and is loaded lazily by ``main.create_app``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..errors import BizCode
from ..external_catalog.errors import ExternalCatalogError

# Plugin-registry keys for the enterprise implementations (enterprise-extensions
# registers them by name so core never imports the HTTP client).
MARKET_GATEWAY_FACTORY = "market.gateway_factory"
MARKET_SUBMISSION_FACTORY = "market.submission_service_factory"


@dataclass(frozen=True)
class MarketPublicationRequest:
    tenant_id: str
    actor_id: str
    resource_type: str
    subtype: str
    local_resource_id: str
    release_id: str | None
    version: str
    display: dict[str, Any]
    bundle: dict[str, Any]


@dataclass(frozen=True)
class MarketPublicationResult:
    publication_id: str
    resource_id: str
    version: str
    status: str
    submitted_at: int


@dataclass(frozen=True)
class MarketWithdrawResult:
    publication_id: str
    status: str
    withdrawn_at: int


@dataclass(frozen=True)
class MarketPublicationStatus:
    local_resource_id: str
    resource_id: str | None
    status: str
    current_version: str | None
    latest_version: str | None
    latest_publication_status: str | None
    updated_at: int | None


@dataclass(frozen=True)
class MarketBundle:
    resource_id: str
    version: str
    content_hash: str
    bundle: dict[str, Any]


@dataclass(frozen=True)
class MarketInstallationRequest:
    tenant_id: str
    actor_id: str
    resource_id: str
    version: str
    resource_type: str
    local_resource_id: str
    workspace_id: str | None
    installed_by: str


@dataclass(frozen=True)
class MarketInstallationResult:
    id: str
    install_count: int


class MarketGateway(Protocol):
    async def submit_publication(
        self, request: MarketPublicationRequest
    ) -> MarketPublicationResult: ...

    async def withdraw_publication(
        self, publication_id: str, tenant_id: str, actor_id: str
    ) -> MarketWithdrawResult: ...

    async def publication_status(
        self,
        resource_type: str,
        local_resource_ids: list[str],
        tenant_id: str,
    ) -> list[MarketPublicationStatus]: ...

    async def download_bundle(
        self,
        resource_id: str,
        version: str,
        tenant_id: str,
        actor_id: str,
    ) -> MarketBundle: ...

    async def register_installation(
        self, request: MarketInstallationRequest
    ) -> MarketInstallationResult: ...

    async def deregister_installation(
        self,
        resource_type: str,
        local_resource_id: str,
        tenant_id: str,
        actor_id: str,
    ) -> None: ...


def _disabled() -> None:
    raise ExternalCatalogError(
        BizCode.FEATURE_NOT_AVAILABLE,
        "社区市场未启用",
        data={"reason": "MARKET_DISABLED"},
    )


class DisabledMarketGateway:
    """Community build: every market call fails with 6020."""

    async def submit_publication(self, request: MarketPublicationRequest):
        _disabled()

    async def withdraw_publication(
        self, publication_id: str, tenant_id: str, actor_id: str
    ):
        _disabled()

    async def publication_status(
        self, resource_type: str, local_resource_ids: list[str], tenant_id: str
    ):
        _disabled()

    async def download_bundle(
        self, resource_id: str, version: str, tenant_id: str, actor_id: str
    ):
        _disabled()

    async def register_installation(self, request: MarketInstallationRequest):
        _disabled()

    async def deregister_installation(
        self,
        resource_type: str,
        local_resource_id: str,
        tenant_id: str,
        actor_id: str,
    ):
        _disabled()

"""Cross-service market contract. The community build only ships the disabled
implementation; the enterprise HTTP implementation lives in the private
enterprise-extensions package and is loaded lazily by ``main.create_app``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from ..errors import BizCode
from ..external_catalog.errors import ExternalCatalogError

# Plugin-registry keys for the enterprise implementations (enterprise-extensions
# registers them by name so core never imports the HTTP client).
MARKET_GATEWAY_FACTORY = "market.gateway_factory"
MARKET_SUBMISSION_FACTORY = "market.submission_service_factory"

# 契约 v0.13 §6.6：`dependency` 只要求版本 `approved`，不看资源的
# `listing_status`；`top_level` 仍要求资源在架。用 Literal 收敛取值——拼写差异
# （"top-level"、"top_level "）会让市场侧的判定静默失效，表现成「依赖已下架却
# 装不上」这种很难联想的原因。
InstallContext = Literal["top_level", "dependency"]


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
    # v0.10 起市场侧的精确状态口径；`status` 只用于推导 review/未上架等旧语义，
    # 回填到本地 `external` 的只有这两个字段。
    listing_status: str | None = None
    delist_actor: str | None = None
    # 契约 §6.4 的批量状态接口还带这些字段，`external_catalog_service` 会读它们：
    # 下架时间/原因、能否下架/重新上架、本次申请类型。缺了就只能靠
    # `getattr(..., None)` 静默兜底，协议与读取方不一致却没有任何提示。
    delisted_at: int | None = None
    delist_reason: str | None = None
    can_delist: bool | None = None
    can_relist: bool | None = None
    latest_request_type: str | None = None


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
    # 契约 v0.13 §6.6：`dependency` 只要求版本 `approved`，不看 `listing_status`；
    # `top_level` 仍要求资源在架。
    context: InstallContext = "top_level"


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
        context: InstallContext = "top_level",
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
        self,
        resource_id: str,
        version: str,
        tenant_id: str,
        actor_id: str,
        context: InstallContext = "top_level",
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

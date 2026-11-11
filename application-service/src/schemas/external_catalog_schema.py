"""Tenant-facing external catalog DTOs (contract §1–§3)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..external_catalog.types import ResourceType, VERSION_PATTERN


class ExternalRef(BaseModel):
    source: Literal["community_market"] = "community_market"
    resource_id: str
    version: str
    # 唯一状态口径（契约 v0.10 §1.1）：`external.status` 摘要字段已删除。
    # 两个字段同一回填条件（`include_external_status=true`），默认都是 `null`；
    # 市场查询失败时整批 `null`，与「未请求回填」不可区分。
    listing_status: Literal[
        "not_listed", "listed", "pending_listing", "delisted"
    ] | None = None
    delist_actor: Literal["publisher", "operator"] | None = None


class DisplayPayload(BaseModel):
    subtype: str | None = Field(None, max_length=32)
    name: str = Field(..., min_length=1, max_length=80)
    summary: str = Field("", max_length=500)
    icon: str | None = Field(None, max_length=1000)
    publisher_name: str = Field(..., min_length=1, max_length=64)
    categories: list[str] = Field(default_factory=list, max_length=5)
    tags: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags: list[str]) -> list[str]:
        for tag in tags:
            if not 1 <= len(tag) <= 32:
                raise ValueError("tag length must be 1-32")
        return tags

    @field_validator("categories", "tags")
    @classmethod
    def dedupe(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class PublicationRequest(BaseModel):
    resource_type: ResourceType
    subtype: str | None = None
    resource_id: str = Field(..., min_length=1, max_length=64)
    release_id: str | None = Field(None, max_length=64)
    version: str | None = Field(None, max_length=64)
    display: DisplayPayload

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str | None) -> str | None:
        if value is not None and not VERSION_PATTERN.match(value):
            raise ValueError("invalid version format")
        return value

    @model_validator(mode="after")
    def app_requires_release(self) -> PublicationRequest:
        if self.resource_type == ResourceType.APP and not self.release_id:
            raise ValueError("release_id is required for app publications")
        return self


class PublicationResponse(BaseModel):
    publication_id: str
    resource_id: str
    local_resource_id: str
    resource_type: str
    subtype: str
    version: str
    release_id: str | None
    status: Literal["pending"]
    content_hash: str
    submitted_at: int


class WithdrawResponse(BaseModel):
    publication_id: str
    status: Literal["withdrawn"]
    withdrawn_at: int


class PublicationResourceState(BaseModel):
    """市场侧资源的上架状态（v0.5 起 `status` → `listing_status`）。

    `status` 是旧枚举，已从契约移除：它把「在架 / 待上架 / 平台下架」压成
    published / unlisted 两档，前端据此分不清「发布方自己下架、可自助上架」和
    「平台下架、只能等平台恢复」。
    """

    id: str
    listing_status: Literal[
        "not_listed", "listed", "pending_listing", "delisted"
    ] | None = None
    delist_actor: Literal["publisher", "operator"] | None = None
    delisted_at: int | None = None
    delist_reason: str | None = None
    current_version: str | None
    install_count: int
    updated_at: int


class LatestPublication(BaseModel):
    id: str
    # `first_publish`（首次上架）/ `version_update`（新版本）：审核通过后的上架
    # 效果不同——前者把资源置为在架，后者按资源当前状态切换版本（§5.3）。
    request_type: Literal["first_publish", "version_update"] | None = None
    version: str
    status: Literal["pending", "approved", "rejected", "withdrawn"]
    submitted_at: int
    reviewed_at: int | None
    review_note: str | None
    reject_reason: str | None


class PublicationStatusResponse(BaseModel):
    resource_type: str
    local_resource_id: str
    resource: PublicationResourceState | None
    latest_publication: LatestPublication | None
    can_publish: bool
    # §3.3：两个自助动作的可用性由市场侧判定（发布者身份 + 上架状态），
    # 本地不重复推导，否则前端会显示一个点了必然 6029/6031 的按钮。
    can_delist: bool = False
    can_relist: bool = False
    block_reason: Literal["PENDING_REVIEW", "NO_RELEASE", "NOT_ELIGIBLE"] | None


class InstallRequest(BaseModel):
    resource_type: ResourceType
    resource_id: str = Field(..., min_length=1, max_length=64)
    version: str = Field(..., max_length=64)
    workspace_id: str | None = Field(None, max_length=64)

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not VERSION_PATTERN.match(value):
            raise ValueError("invalid version format")
        return value

    @model_validator(mode="after")
    def app_requires_workspace(self) -> InstallRequest:
        if self.resource_type == ResourceType.APP and not self.workspace_id:
            raise ValueError("workspace_id is required for app installations")
        return self


class PendingBinding(BaseModel):
    kind: str
    name: str
    ref: str
    required: bool = True


class ResolvedDependency(BaseModel):
    """本次安装解析到的一条直接依赖（§3.4 `dependencies[]`）。"""

    resource_type: str
    market_resource_id: str
    version: str
    local_resource_id: str
    # 本租户已有同一市场副本，复用而未重新落地
    reused: bool = False
    # 本地版本与引用版本不一致：复用本地版本、不自动升级
    version_mismatch: bool = False


class PendingAgent(BaseModel):
    """集群子 Agent 的落地结果：草稿状态 + 用户需要补的绑定（§3.4）。"""

    local_resource_id: str
    name: str
    # 子 Agent 没有 release，必须先发布它，集群才能发布
    publish_required: bool = True
    pending_bindings: list[PendingBinding] = Field(default_factory=list)


class InstallResponse(BaseModel):
    resource_type: str
    resource_id: str
    version: str
    local_resource_id: str
    local_name: str
    workspace_id: str | None
    installed_at: int
    pending_bindings: list[PendingBinding] = Field(default_factory=list)
    dependencies: list[ResolvedDependency] = Field(default_factory=list)
    pending_agents: list[PendingAgent] = Field(default_factory=list)
    idempotent_replay: bool = False


class UninstallResponse(BaseModel):
    resource_type: str
    local_resource_id: str
    uninstalled: bool
    registration_synced: bool


def canonical_request_fingerprint(body: dict[str, Any]) -> str:
    from ..external_catalog.bundle import canonical_request_fingerprint as _fingerprint

    return _fingerprint(body)

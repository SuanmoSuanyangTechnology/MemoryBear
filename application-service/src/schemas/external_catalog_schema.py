"""Tenant-facing external catalog DTOs (contract §1–§3)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..external_catalog.types import ResourceType, VERSION_PATTERN


class ExternalRef(BaseModel):
    source: Literal["community_market"] = "community_market"
    resource_id: str
    version: str
    # 摘要状态，保留给只认旧枚举的调用方（§1.1）
    status: Literal["published", "unlisted", "unknown"] | None = None
    # 精确上架状态与下架主体：与 `status` 同一回填条件（`include_external_status=true`），
    # 旧客户端忽略即可。`status` 的映射固定为 listed→published、其余→unlisted。
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
    id: str
    status: Literal["draft", "published", "unlisted"]
    current_version: str | None
    install_count: int
    updated_at: int


class LatestPublication(BaseModel):
    id: str
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


class InstallResponse(BaseModel):
    resource_type: str
    resource_id: str
    version: str
    local_resource_id: str
    local_name: str
    workspace_id: str | None
    installed_at: int
    pending_bindings: list[PendingBinding] = Field(default_factory=list)
    idempotent_replay: bool = False


class UninstallResponse(BaseModel):
    resource_type: str
    local_resource_id: str
    uninstalled: bool
    registration_synced: bool


def canonical_request_fingerprint(body: dict[str, Any]) -> str:
    from ..external_catalog.bundle import canonical_request_fingerprint as _fingerprint

    return _fingerprint(body)

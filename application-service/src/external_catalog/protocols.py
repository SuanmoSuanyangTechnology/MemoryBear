"""Resource-type adapter contracts. The orchestration layer depends only on
these protocols; adding a resource type means adding one adapter and one
registration line, never changing publish/install flows."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class PublishContext:
    db: Session | None
    tenant_id: uuid.UUID
    local_resource_id: str
    release_id: str | None
    version: str | None
    display: dict[str, Any]
    visited: frozenset[tuple[str, str]] = frozenset()


@dataclass(frozen=True)
class DependencySpec:
    kind: str  # tools | skills | plugins
    resource_type: str  # tool | skill_package
    ref: str  # 发布侧本地资源 ID，写入 manifest 的 ref
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    tool_type: str | None = None
    display_name: str | None = None
    files: list[dict[str, Any]] = field(default_factory=list)
    member_refs: list[str] = field(default_factory=list)

    def to_manifest_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Serialize to the exact contract shape (接口文档 §8.3).

        Tools carry ``tool_type``, skills carry ``display_name`` + ``files``,
        plugins carry ``member_refs``; unknown keys are never emitted.
        """
        if self.kind == "tools":
            return {
                "ref": self.ref,
                "name": self.name,
                "tool_type": self.tool_type,
                "payload": payload,
            }
        if self.kind == "skills":
            return {
                "ref": self.ref,
                "name": self.name,
                "display_name": self.display_name,
                "payload": payload,
                "files": self.files,
            }
        return {
            "ref": self.ref,
            "name": self.name,
            "payload": payload,
            "member_refs": self.member_refs,
        }


@dataclass(frozen=True)
class PublishPayload:
    subtype: str
    version: str
    payload: dict[str, Any] = field(default_factory=dict)
    dependencies: list[DependencySpec] = field(default_factory=list)
    bindings: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    redacted_secrets: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class InstallContext:
    db: Session
    tenant_id: uuid.UUID
    actor_id: uuid.UUID
    workspace_id: uuid.UUID | None
    installed_by: str
    bundle: dict[str, Any]
    dependency: dict[str, Any] | None
    id_map: dict[tuple[str, str], str]
    market_resource_id: str | None = None
    redacted_secrets: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class InstalledResource:
    local_resource_id: str
    local_name: str
    pending_bindings: list[dict[str, Any]] = field(default_factory=list)


class ResourcePublishAdapter(Protocol):
    resource_type: str

    def build_payload(self, ctx: PublishContext) -> PublishPayload: ...

    def build_dependency(self, ctx: PublishContext) -> DependencySpec: ...

    def publish_block_reason(self, ctx: PublishContext) -> str | None: ...


class ResourceInstallAdapter(Protocol):
    resource_type: str

    def install(self, ctx: InstallContext) -> InstalledResource: ...

    def install_dependency(self, ctx: InstallContext) -> InstalledResource: ...

    def uninstall(
        self, db: Session, tenant_id: uuid.UUID, local_resource_id: str
    ) -> None: ...

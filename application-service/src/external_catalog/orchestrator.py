"""Bundle assembly shared by every resource type."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..errors import BizCode
from .bundle import finalize_bundle, redact_secrets
from .errors import ExternalCatalogError
from .protocols import InstallContext, InstalledResource, PublishPayload
from .types import VERSION_PATTERN, is_supported_subtype

logger = logging.getLogger(__name__)

_DEPENDENCY_ORDER = (
    ("plugins", "skill_package"),
    ("skills", "skill_package"),
    ("tools", "tool"),
)


def build_manifest(
    *,
    resource_type: str,
    local_resource_id: str,
    release_id: str | None,
    display: dict[str, Any],
    built: PublishPayload,
) -> dict[str, Any]:
    if not is_supported_subtype(resource_type, built.subtype):
        raise ExternalCatalogError(
            BizCode.EXTERNAL_CATALOG_INVALID,
            "资源子类型不合法",
            data={"reason": "SUBTYPE_INVALID", "subtype": built.subtype},
        )
    if not built.version or not VERSION_PATTERN.match(built.version):
        raise ExternalCatalogError(
            BizCode.EXTERNAL_CATALOG_INVALID,
            "版本号格式不合法",
            data={"reason": "VERSION_INVALID", "version": built.version},
        )

    payload, payload_paths = redact_secrets(built.payload, "payload")
    dependencies: dict[str, list[dict[str, Any]]] = {
        "tools": [],
        "skills": [],
        "plugins": [],
    }
    redacted_paths = list(built.redacted_secrets) + payload_paths
    for index, dependency in enumerate(built.dependencies):
        if dependency.kind not in dependencies:
            raise ExternalCatalogError(
                BizCode.EXTERNAL_CATALOG_INVALID,
                "依赖类型不合法",
                data={"reason": "DEPENDENCY_INVALID", "kind": dependency.kind},
            )
        if dependency.kind == "tools" and not dependency.tool_type:
            raise ExternalCatalogError(
                BizCode.EXTERNAL_CATALOG_INVALID,
                "工具依赖缺少 tool_type",
                data={"reason": "DEPENDENCY_INVALID", "ref": dependency.ref},
            )
        cleaned, paths = redact_secrets(
            dependency.payload,
            f"dependencies.{dependency.kind}[{index}].payload",
        )
        redacted_paths.extend(paths)
        dependencies[dependency.kind].append(dependency.to_manifest_item(cleaned))

    manifest = {
        "schema_version": 1,
        "resource_type": resource_type,
        "source": {
            "local_resource_id": local_resource_id,
            "release_id": release_id,
            "version": built.version,
        },
        "display": {**display, "subtype": built.subtype},
        "payload": payload,
        "dependencies": dependencies,
        "bindings": built.bindings,
        "files": built.files,
        "redacted_secrets": sorted(set(redacted_paths)),
    }
    return finalize_bundle(manifest)


def install_dependencies(
    *,
    db: Session,
    registry,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    workspace_id: uuid.UUID | None,
    installed_by: str,
    bundle: dict[str, Any],
    id_map: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    """Install bundled dependencies in plugins → skills → tools order.

    Items use the contract shape (``ref`` / ``name`` / ``payload`` …). The
    orchestrator adds ``kind`` before handing the item to the adapter, so the
    shared skill adapter can distinguish ``skills`` from ``plugins``.
    Builtin tools are not skipped here: the tool adapter maps them to the
    tenant's installed builtin instance by name. Bundled copies carry no
    market resource ID, so they are created with ``origin=local``.
    """
    pending: list[dict[str, Any]] = []
    dependencies = bundle.get("dependencies", {})
    for kind, resource_type in _DEPENDENCY_ORDER:
        adapter = registry.get_install(resource_type)
        for item in dependencies.get(kind, []):
            ctx = InstallContext(
                db=db,
                tenant_id=tenant_id,
                actor_id=actor_id,
                workspace_id=workspace_id,
                installed_by=installed_by,
                bundle=bundle,
                dependency={**item, "kind": kind},
                id_map=id_map,
                market_resource_id=None,
                redacted_secrets=bundle.get("redacted_secrets", []),
            )
            installed: InstalledResource = adapter.install_dependency(ctx)
            id_map[(resource_type, item["ref"])] = installed.local_resource_id
            pending.extend(installed.pending_bindings)
            logger.info(
                "installed market dependency",
                extra={
                    "resource_type": resource_type,
                    "dependency_kind": kind,
                    "dependency_ref": item["ref"],
                    "local_id": installed.local_resource_id,
                },
            )
    return pending

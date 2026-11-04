"""Tool adapter: MCP publication plus tool dependency build/install.

Standalone publication is limited to MCP tools (contract §3.1). App bundles
may also carry MCP or custom tool definitions as dependencies; builtin tools
travel as bindings and workflow tools are never bundled. Installation copies
the definition into ``ToolConfig`` + the type-specific table in the same
transaction; name collisions get a `` (2)`` suffix instead of overwriting.
"""

from __future__ import annotations

import uuid
from typing import Any

from ...errors import BizCode
from ...models.tool_model import (
    CustomToolConfig,
    MCPToolConfig,
    ToolConfig,
    ToolStatus,
    ToolType,
)
from ...repositories.tool_repository import (
    CustomToolRepository,
    MCPToolRepository,
    ToolRepository,
)
from ..errors import ExternalCatalogError
from ..protocols import (
    DependencySpec,
    InstallContext,
    InstalledResource,
    PublishContext,
    PublishPayload,
)
from ..registry import default_registry
from ..types import ResourceType

_NAME_MAX_LENGTH = 255
_CREDENTIAL_CONFIG_KEYS = ("connection_config", "auth_config")


def _as_uuid(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


def _invalid(message: str, reason: str) -> ExternalCatalogError:
    return ExternalCatalogError(
        BizCode.EXTERNAL_CATALOG_INVALID, message, data={"reason": reason}
    )


def _redacted_paths_for_tool(
    ctx: InstallContext, dependency: dict[str, Any] | None
) -> list[str]:
    """Redacted paths that belong to this tool's payload.

    Standalone bundles use a ``payload.`` prefix; dependencies embed the index
    in the path, which is resolved by matching ``ref``.
    """
    paths = list(ctx.redacted_secrets)
    if dependency is None:
        return [path for path in paths if path == "payload" or path.startswith("payload.")]
    items = (ctx.bundle.get("dependencies") or {}).get("tools") or []
    ref = dependency.get("ref")
    for index, item in enumerate(items):
        if item.get("ref") == ref:
            prefix = f"dependencies.tools[{index}].payload"
            return [
                path for path in paths if path == prefix or path.startswith(f"{prefix}.")
            ]
    return []


def _needs_reconfiguration(paths: list[str]) -> bool:
    return any(key in path for path in paths for key in _CREDENTIAL_CONFIG_KEYS)


class ToolAdapter:
    resource_type = ResourceType.TOOL.value

    # ---------- publish ----------

    def publish_block_reason(self, ctx: PublishContext) -> str | None:
        config = self._load_tool(ctx)
        if config.tool_type != ToolType.MCP.value or not config.is_active:
            return "NOT_ELIGIBLE"
        return None

    def build_payload(self, ctx: PublishContext) -> PublishPayload:
        config = self._require_publishable(ctx)
        mcp_config = self._require_mcp_config(ctx, config)
        return PublishPayload(
            subtype="mcp",
            version=ctx.version or config.version or "1.0.0",
            payload=self._mcp_payload(config, mcp_config),
        )

    def build_dependency(self, ctx: PublishContext) -> DependencySpec:
        config = self._load_tool(ctx)
        if not config.is_active:
            raise ExternalCatalogError(
                BizCode.TOOL_SELECTION_INACTIVE, "工具已停用或删除"
            )
        if config.tool_type == ToolType.MCP.value:
            payload = self._mcp_payload(config, self._require_mcp_config(ctx, config))
        elif config.tool_type == ToolType.CUSTOM.value:
            payload = self._custom_payload(config, self._require_custom_config(ctx, config))
        elif config.tool_type == ToolType.WORKFLOW.value:
            raise _invalid("工作流工具不能随包", "TOOL_WORKFLOW_OUT_OF_BUNDLE")
        else:
            raise _invalid("内置工具不随包", "DEPENDENCY_NOT_BUNDLED")
        return DependencySpec(
            kind="tools",
            resource_type=ResourceType.TOOL.value,
            ref=str(config.id),
            name=config.name,
            tool_type=config.tool_type,
            payload=payload,
        )

    # ---------- install ----------

    def install(self, ctx: InstallContext) -> InstalledResource:
        bundle = ctx.bundle or {}
        display = bundle.get("display") or {}
        payload = bundle.get("payload") or {}
        if display.get("subtype") != "mcp":
            raise _invalid("工具子类型不合法", "SUBTYPE_INVALID")
        if payload.get("tool_type") != ToolType.MCP.value:
            raise _invalid("payload 缺少 MCP 工具定义", "INCOMPLETE_BUNDLE")
        source = bundle.get("source") or {}
        return self._create_local_copy(
            ctx,
            payload=payload,
            external=True,
            redacted=_redacted_paths_for_tool(ctx, None),
            external_version=source.get("version"),
        )

    def install_dependency(self, ctx: InstallContext) -> InstalledResource:
        dependency = ctx.dependency or {}
        payload = dependency.get("payload") or {}
        tool_type = dependency.get("tool_type") or payload.get("tool_type") or ""
        if tool_type == ToolType.WORKFLOW.value:
            raise _invalid("工作流工具不能随包", "TOOL_WORKFLOW_OUT_OF_BUNDLE")
        if tool_type not in (ToolType.MCP.value, ToolType.CUSTOM.value):
            raise _invalid("工具依赖缺失", "DEPENDENCY_NOT_BUNDLED")
        payload = {**payload, "tool_type": tool_type}
        return self._create_local_copy(
            ctx,
            payload=payload,
            external=False,
            redacted=_redacted_paths_for_tool(ctx, dependency),
            external_version=None,
        )

    def uninstall(
        self, db, tenant_id: uuid.UUID, local_resource_id: str
    ) -> None:
        tool_id = _as_uuid(local_resource_id)
        if tool_id is None or db is None:
            return
        config = ToolRepository(db).find_by_id_and_tenant_all(tool_id, tenant_id)
        if config is None:
            return
        if config.tool_type == ToolType.BUILTIN.value:
            raise _invalid("内置工具不可卸载", "NOT_ELIGIBLE")
        config.is_active = False

    # ---------- helpers ----------

    def _load_tool(self, ctx: PublishContext) -> ToolConfig:
        if ctx.db is None:
            raise RuntimeError("publish context requires a database session")
        tool_id = _as_uuid(ctx.local_resource_id)
        config = (
            ToolRepository(ctx.db).find_by_id_and_tenant_all(tool_id, ctx.tenant_id)
            if tool_id is not None
            else None
        )
        if config is None:
            raise ExternalCatalogError(
                BizCode.EXTERNAL_CATALOG_NOT_FOUND, "工具不存在"
            )
        return config

    def _require_publishable(self, ctx: PublishContext) -> ToolConfig:
        config = self._load_tool(ctx)
        if config.tool_type != ToolType.MCP.value:
            raise _invalid("仅 MCP 工具允许独立发布", "NOT_ELIGIBLE")
        if not config.is_active:
            raise ExternalCatalogError(
                BizCode.TOOL_SELECTION_INACTIVE, "工具已停用或删除"
            )
        return config

    @staticmethod
    def _require_mcp_config(
        ctx: PublishContext, config: ToolConfig
    ) -> MCPToolConfig:
        mcp_config = MCPToolRepository(ctx.db).find_by_tool_id(config.id)
        if mcp_config is None or not mcp_config.server_url:
            raise _invalid("MCP 配置不完整", "INCOMPLETE_BUNDLE")
        return mcp_config

    @staticmethod
    def _require_custom_config(
        ctx: PublishContext, config: ToolConfig
    ) -> CustomToolConfig:
        custom_config = CustomToolRepository(ctx.db).find_by_tool_id(config.id)
        if custom_config is None:
            raise _invalid("自定义工具配置不完整", "INCOMPLETE_BUNDLE")
        return custom_config

    @staticmethod
    def _mcp_payload(config: ToolConfig, mcp_config: MCPToolConfig) -> dict[str, Any]:
        return {
            "tool_type": ToolType.MCP.value,
            "name": config.name,
            "description": config.description,
            "icon": config.icon,
            "config": {
                "server_url": mcp_config.server_url,
                "connection_config": mcp_config.connection_config or {},
                "available_tools": mcp_config.available_tools or [],
            },
        }

    @staticmethod
    def _custom_payload(
        config: ToolConfig, custom_config: CustomToolConfig
    ) -> dict[str, Any]:
        return {
            "tool_type": ToolType.CUSTOM.value,
            "name": config.name,
            "description": config.description,
            "icon": config.icon,
            "config": {
                "schema_url": custom_config.schema_url,
                "schema_content": custom_config.schema_content,
                "auth_type": custom_config.auth_type,
                "auth_config": custom_config.auth_config or {},
                "base_url": custom_config.base_url,
                "timeout": custom_config.timeout,
            },
        }

    def _create_local_copy(
        self,
        ctx: InstallContext,
        *,
        payload: dict[str, Any],
        external: bool,
        redacted: list[str],
        external_version: str | None,
    ) -> InstalledResource:
        tool_type = payload.get("tool_type")
        config = payload.get("config")
        if tool_type not in (ToolType.MCP.value, ToolType.CUSTOM.value):
            raise _invalid("工具类型不合法", "SUBTYPE_INVALID")
        if not isinstance(config, dict):
            raise _invalid("工具配置不合法", "INCOMPLETE_BUNDLE")
        if tool_type == ToolType.MCP.value and not config.get("server_url"):
            raise _invalid("MCP 配置不完整", "INCOMPLETE_BUNDLE")

        name = self._unique_name(
            ctx,
            payload.get("name")
            or (ctx.dependency or {}).get("name")
            or f"{tool_type}-tool",
            tool_type,
        )
        status = (
            ToolStatus.UNCONFIGURED.value
            if _needs_reconfiguration(redacted)
            else ToolStatus.AVAILABLE.value
        )
        tool_config = ToolConfig(
            id=uuid.uuid4(),
            name=name,
            description=payload.get("description"),
            icon=payload.get("icon"),
            tool_type=tool_type,
            tenant_id=ctx.tenant_id,
            status=status,
            config_data=config,
            version="1.0.0",
            tags=[],
            origin="external" if external else "local",
            external_source="community_market"
            if external and ctx.market_resource_id
            else None,
            external_resource_id=ctx.market_resource_id if external else None,
            external_version=external_version if external else None,
        )
        ctx.db.add(tool_config)
        if tool_type == ToolType.MCP.value:
            ctx.db.add(
                MCPToolConfig(
                    id=tool_config.id,
                    server_url=config["server_url"],
                    connection_config=config.get("connection_config") or {},
                    available_tools=config.get("available_tools") or [],
                )
            )
        else:
            ctx.db.add(
                CustomToolConfig(
                    id=tool_config.id,
                    schema_url=config.get("schema_url"),
                    schema_content=config.get("schema_content"),
                    auth_type=config.get("auth_type") or "none",
                    auth_config=config.get("auth_config") or {},
                    base_url=config.get("base_url"),
                    timeout=config.get("timeout") or 30,
                )
            )
        ctx.db.flush()
        return InstalledResource(
            local_resource_id=str(tool_config.id), local_name=name
        )

    @staticmethod
    def _unique_name(ctx: InstallContext, base: str, tool_type: str) -> str:
        if ctx.db is None:
            raise RuntimeError("install context requires a database session")
        name = str(base).strip() or f"{tool_type}-tool"
        existing = {
            row[0]
            for row in ctx.db.query(ToolConfig.name)
            .filter(
                ToolConfig.tenant_id == ctx.tenant_id,
                ToolConfig.tool_type == tool_type,
            )
            .all()
        }
        name = name[:_NAME_MAX_LENGTH]
        if name not in existing:
            return name
        suffix = 2
        while True:
            tail = f" ({suffix})"
            candidate = f"{name[:_NAME_MAX_LENGTH - len(tail)]}{tail}"
            if candidate not in existing:
                return candidate
            suffix += 1


default_registry.register(ToolAdapter())

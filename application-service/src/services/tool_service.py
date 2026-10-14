"""Tool definition-plane service.

Port of the monolith ``ToolService`` management reads and writes: list, detail,
CRUD, builtin bootstrap, method discovery, statistics and OpenAPI schema
parsing. Execution-plane paths (execute, connection test, MCP tool sync) stay in
the monolith until the runtime-plane batch. Repository access becomes
instance-based (the host uses repository static methods); the OpenAPI parser is
synchronous here, so call sites that awaited it in the host call it directly.
"""

from __future__ import annotations

import importlib
import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..errors import BizCode, BusinessException
from ..models.app_model import App
from ..models.tool_model import (
    BuiltinToolConfig,
    CustomToolConfig,
    MCPSourceChannel,
    MCPToolConfig,
    ToolConfig,
    ToolStatus,
    ToolType,
)
from ..repositories.tool_repository import (
    BuiltinToolRepository,
    CustomToolRepository,
    MCPToolRepository,
    ToolRepository,
    WorkflowToolRepository,
)
from ..schemas.tool_schema import (
    BuiltinToolConfigSchema,
    CustomToolConfigSchema,
    MCPToolConfigSchema,
    ToolDetailSchema,
    ToolInfo,
    WorkflowToolConfigSchema,
)
from ..tools.base import BaseTool
from ..tools.custom.base import CustomTool
from ..tools.mcp import MCPToolManager
from ..tools.mcp.base import MCPTool
from ..tools.serialization import serialize_tool_parameter
from ..tools.workflow.base import WorkflowAsTool
from ..utils.datetime_utils import to_timestamp_ms

logger = logging.getLogger(__name__)

# Builtin tool mapping
BUILTIN_TOOLS = {
    "DateTimeTool": "src.tools.builtin.datetime_tool",
    "JsonTool": "src.tools.builtin.json_tool",
    "BaiduSearchTool": "src.tools.builtin.baidu_search_tool",
    "MinerUTool": "src.tools.builtin.mineru_tool",
    "TextInTool": "src.tools.builtin.textin_tool",
    "OpenClawTool": "src.tools.builtin.openclaw_tool",
    "DatabaseTool": "src.tools.builtin.database_tool",
}


class ToolService:
    """Unified tool service managing the full tool lifecycle."""

    def __init__(self, db: Session):
        self.db = db
        self._tool_cache: dict[str, BaseTool] = {}

        # MCP manager
        self.mcp_tool_manager = MCPToolManager(db)

        # Initialize repositories
        self.tool_repo = ToolRepository(db)
        self.builtin_repo = BuiltinToolRepository(db)
        self.custom_repo = CustomToolRepository(db)
        self.mcp_repo = MCPToolRepository(db)
        self.workflow_repo = WorkflowToolRepository(db)

    def list_tools(
            self,
            tenant_id: uuid.UUID,
            name: str | None = None,
            tool_type: ToolType | None = None,
            status: ToolStatus | None = None
    ) -> list[ToolInfo]:
        """List tools."""
        try:
            configs = self.tool_repo.find_by_tenant(
                tenant_id=tenant_id,
                name=name,
                tool_type=tool_type,
                status=status
            )
            return [self._config_to_info(config) for config in configs]
        except Exception as e:
            logger.error(f"获取工具列表失败: {e}")
            return []

    def get_tool_summary(self, tool_id: str, tenant_id: uuid.UUID) -> ToolInfo | None:
        """Get basic tool info."""
        config = self.tool_repo.find_by_id_and_tenant_all(uuid.UUID(tool_id), tenant_id)
        return self._config_to_info(config) if config else None

    def get_tool_detail(self, tool_id: str, tenant_id: uuid.UUID) -> ToolDetailSchema | None:
        """Get tool detail (including type-specific config)."""
        config = self.tool_repo.find_by_id_and_tenant_all(uuid.UUID(tool_id), tenant_id)
        return self._config_to_detail(config) if config else None

    def _check_name_duplicate(
            self,
            name: str,
            tool_type: ToolType,
            tenant_id: uuid.UUID,
            exclude_id: uuid.UUID | None = None
    ) -> None:
        """Check for a duplicate tool name."""
        query = self.db.query(ToolConfig).filter(
            ToolConfig.name == name,
            ToolConfig.tool_type == tool_type,
            ToolConfig.tenant_id == tenant_id
        )
        if exclude_id:
            query = query.filter(ToolConfig.id != exclude_id)
        if query.first():
            raise BusinessException(f"工具名称 '{name}' 已存在", BizCode.DUPLICATE_NAME)

    def _check_mcp_duplicate(
            self,
            name: str,
            tool_type: ToolType,
            tenant_id: uuid.UUID,
            config: dict[str, Any]
    ) -> None:
        """Check for a duplicate MCP tool.

        Market-sourced tools are matched on market_id + market_config_id +
        mcp_service_id (name-independent); self-hosted tools on name + tool_type.
        """
        source_channel = config.get("source_channel")
        is_market_source = (
            source_channel is not None
            and source_channel != MCPSourceChannel.SELF_HOSTED
        )
        if is_market_source:
            exists = (
                self.db.query(ToolConfig)
                .join(MCPToolConfig, MCPToolConfig.id == ToolConfig.id)
                .filter(
                    ToolConfig.tenant_id == tenant_id,
                    ToolConfig.tool_type == tool_type,
                    MCPToolConfig.source_channel == source_channel,
                    MCPToolConfig.market_id == config.get("market_id"),
                    MCPToolConfig.market_config_id == config.get("market_config_id"),
                    MCPToolConfig.mcp_service_id == config.get("mcp_service_id"),
                )
                .first()
            )
            if exists:
                raise BusinessException("该MCP服务已添加", BizCode.DUPLICATE_NAME)
        else:
            exists = (
                self.db.query(ToolConfig)
                .filter(
                    ToolConfig.name == name,
                    ToolConfig.tool_type == tool_type,
                    ToolConfig.tenant_id == tenant_id,
                )
                .first()
            )
            if exists:
                raise BusinessException(f"工具 '{name}' 已存在", BizCode.DUPLICATE_NAME)

    async def create_tool(
            self,
            name: str,
            tool_type: ToolType,
            tenant_id: uuid.UUID,
            icon: str | None = None,
            description: str | None = None,
            config: dict[str, Any] | None = None,
            tags: list[str] | None = None
    ) -> str:
        """Create a tool."""
        if tool_type == ToolType.BUILTIN:
            raise ValueError("内置工具不允许创建")

        cfg = config or {}
        if tool_type == ToolType.MCP:
            self._check_mcp_duplicate(name, tool_type, tenant_id, cfg)
            # Test the connection before creating
            test_result = await self._test_mcp_connection_by_config(cfg)
            if not test_result["success"]:
                raise BusinessException(
                    f"MCP连接测试失败: {test_result['message']}", BizCode.INVALID_PARAMETER
                )
            # Write the discovered tool list back to config
            if "available_tools" in test_result:
                cfg["available_tools"] = test_result["available_tools"]
        else:
            self._check_name_duplicate(name, tool_type, tenant_id)

        try:
            # Create the base config
            tool_config = ToolConfig(
                name=name,
                description=description,
                icon=icon,
                tool_type=tool_type.value,
                tenant_id=tenant_id,
                status=ToolStatus.AVAILABLE.value,
                config_data=cfg,
                tags=tags
            )
            self.db.add(tool_config)
            self.db.flush()

            # Create type-specific config
            self._create_type_config(tool_config, cfg)

            self.db.commit()
            logger.info(f"工具创建成功: {tool_config.id}")
            return str(tool_config.id)

        except BusinessException:
            self.db.rollback()
            raise
        except Exception as e:
            self.db.rollback()
            logger.error(f"创建工具失败: {e}")
            raise

    def update_tool(
            self,
            tool_id: str,
            tenant_id: uuid.UUID,
            name: str | None = None,
            description: str | None = None,
            icon: str | None = None,
            config: dict[str, Any] | None = None,
            is_enabled: bool | None = None,
            tags: list[str] | None = None
    ) -> bool:
        """Update a tool."""
        config_obj = self._get_tool_config(tool_id, tenant_id)
        if not config_obj:
            return False

        if config_obj.tool_type == ToolType.BUILTIN.value:
            if name or description or icon:
                raise ValueError("内置工具不允许修改名称、描述和图标")
        try:
            if name:
                self._check_name_duplicate(
                    name, config_obj.tool_type, tenant_id, exclude_id=config_obj.id
                )
                config_obj.name = name
            if description:
                config_obj.description = description
            if icon:
                config_obj.icon = icon
            if tags:
                config_obj.tags = tags
            if config:
                config_obj.config_data = config.copy()

                # Sync to the type-specific table
                self._sync_type_config(config_obj, config, is_enabled)

                # Update status logic
                self._update_tool_status(config_obj)

            # Clear cache
            self._clear_tool_cache(tool_id)

            self.db.commit()
            return True

        except Exception as e:
            self.db.rollback()
            logger.error(f"更新工具失败: {tool_id}, {e}")
            return False

    def delete_tool(self, tool_id: str, tenant_id: uuid.UUID) -> bool:
        """Delete a tool (soft delete)."""
        config = self._get_tool_config(tool_id, tenant_id)
        if not config:
            return False

        if config.tool_type == ToolType.BUILTIN.value:
            raise ValueError("内置工具不允许删除")

        try:
            config.is_active = False
            self._clear_tool_cache(tool_id)
            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"删除工具失败: {tool_id}, {e}")
            return False

    def set_tool_active(self, tool_id: str, tenant_id: uuid.UUID, is_active: bool) -> bool:
        """Set tool availability (enable/disable)."""
        # Query directly, including records with is_active=False
        config = self.db.query(ToolConfig).filter(
            ToolConfig.id == uuid.UUID(tool_id),
            ToolConfig.tenant_id == tenant_id
        ).first()
        if not config:
            return False
        if config.tool_type == ToolType.BUILTIN.value:
            raise ValueError("内置工具不允许修改可用状态")
        try:
            config.is_active = is_active
            self._clear_tool_cache(tool_id)
            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"设置工具状态失败: {tool_id}, {e}")
            return False

    def ensure_builtin_tools_initialized(self, tenant_id: uuid.UUID) -> None:
        """Ensure builtin tools are initialized (supports incremental additions)."""
        builtin_config = self._load_builtin_config()
        if not builtin_config:
            return

        existing_classes = self.builtin_repo.get_existing_tool_classes(tenant_id)

        added = False
        for tool_key, tool_info in builtin_config.items():
            if tool_info['tool_class'] in existing_classes:
                continue
            try:
                initial_status = self._determine_initial_status(tool_info)
                tool_config = ToolConfig(
                    name=tool_info['name'],
                    description=tool_info['description'],
                    tool_type=ToolType.BUILTIN.value,
                    tenant_id=tenant_id,
                    status=initial_status,
                    config_data={"tool_class": tool_info['tool_class'],
                                 "requires_config": tool_info.get('requires_config', False),
                                 "is_enabled": False},
                    version=tool_info["version"]
                )
                self.db.add(tool_config)
                self.db.flush()

                builtin_config_obj = BuiltinToolConfig(
                    id=tool_config.id,
                    tool_class=tool_info['tool_class'],
                    parameters={},
                    requires_config=tool_info.get('requires_config', False)
                )
                self.db.add(builtin_config_obj)
                added = True

            except Exception as e:
                logger.error(f"初始化内置工具失败: {tool_key}, {e}")

        if added:
            self.db.commit()
            logger.info(f"租户 {tenant_id} 内置工具增量初始化完成")

    async def get_tool_methods(
            self,
            tool_id: str,
            tenant_id: uuid.UUID
    ) -> list[dict[str, Any]] | None:
        """Get all methods of a tool.

        Args:
            tool_id: Tool ID
            tenant_id: Tenant ID

        Returns:
            Method list, or None.
        """
        config = self._get_tool_config_all(tool_id, tenant_id)
        if not config:
            return None

        try:
            if config.tool_type == ToolType.BUILTIN.value:
                return await self._get_builtin_tool_methods(config)
            elif config.tool_type == ToolType.CUSTOM.value:
                return await self._get_custom_tool_methods(config)
            elif config.tool_type == ToolType.MCP.value:
                return await self._get_mcp_tool_methods(config)
            elif config.tool_type == ToolType.WORKFLOW.value:
                return await self._get_workflow_tool_methods(config)
            else:
                return []

        except Exception as e:
            logger.error(f"获取工具方法失败: {tool_id}, {e}")
            return []

    async def _get_builtin_tool_methods(self, config: ToolConfig) -> list[dict[str, Any]]:
        """Get the methods of a builtin tool."""
        builtin_config = self.builtin_repo.find_by_tool_id(config.id)
        if not builtin_config or builtin_config.tool_class not in BUILTIN_TOOLS:
            return []

        # Get the tool instance
        tool_instance = self.get_tool_instance(str(config.id), config.tenant_id)
        if not tool_instance:
            return []

        # Check whether an operation parameter exists
        operation_param = None
        for param in tool_instance.parameters:
            if param.name == "operation" and param.enum:
                operation_param = param
                break

        if operation_param:
            # Multiple operations: generate concrete parameters for each
            methods = []
            for operation in operation_param.enum:
                # Get the parameters specific to this operation
                operation_params = self._get_operation_specific_params(tool_instance, operation)
                methods.append({
                    "method_id": f"{config.name}_{operation}",
                    "name": operation,
                    "description": f"{config.description} - {operation}",
                    "parameters": operation_params
                })
            return methods
        else:
            # Only one method
            return [{
                "method_id": config.name,
                "name": config.name,
                "description": config.description,
                "parameters": [p for p in tool_instance.parameters if p.name != "operation"]
            }]

    def _get_operation_specific_params(
            self, tool_instance: BaseTool, operation: str
    ) -> list[dict[str, Any]]:
        """Get the parameter list for a specific operation."""
        # For datetime_tool, return the parameters relevant to the operation
        if hasattr(tool_instance, 'name') and tool_instance.name == 'datetime_tool':
            return self._get_datetime_tool_params(operation)
        # For json_tool, return the parameters relevant to the operation
        elif hasattr(tool_instance, 'name') and tool_instance.name == 'json_tool':
            return self._get_json_tool_params(operation)
        # For openclaw_tool, return parameters with per-operation descriptions
        elif hasattr(tool_instance, 'name') and tool_instance.name == 'openclaw_tool':
            return self._get_openclaw_tool_params(operation)

        # Default handling for other tools: return all parameters except operation
        return [
            serialize_tool_parameter(param)
            for param in tool_instance.parameters
            if param.name != "operation"
        ]

    def _get_datetime_tool_params(self, operation: str) -> list[dict[str, Any]]:
        """Get parameters for a specific datetime_tool operation."""
        if operation == "now":
            return [
                {
                    "name": "to_timezone",
                    "type": "string",
                    "description": "目标时区（如：UTC, Asia/Shanghai）",
                    "required": False,
                    "default": "Asia/Shanghai"
                },
                {
                    "name": "output_format",
                    "type": "string",
                    "description": "输出时间格式（如：%Y-%m-%d %H:%M:%S）",
                    "required": False,
                    "default": "%Y-%m-%d %H:%M:%S"
                }
            ]
        elif operation == "format":
            return [
                {
                    "name": "input_value",
                    "type": "string",
                    "description": "输入值（时间字符串或时间戳）",
                    "required": True
                },
                {
                    "name": "input_format",
                    "type": "string",
                    "description": "输入时间格式（如：%Y-%m-%d %H:%M:%S）",
                    "required": False,
                    "default": "%Y-%m-%d %H:%M:%S"
                },
                {
                    "name": "output_format",
                    "type": "string",
                    "description": "输出时间格式（如：%Y-%m-%d %H:%M:%S）",
                    "required": False,
                    "default": "%Y-%m-%d %H:%M:%S"
                }
            ]
        elif operation == "convert_timezone":
            return [
                {
                    "name": "input_value",
                    "type": "string",
                    "description": "输入值（时间字符串或时间戳）",
                    "required": True
                },
                {
                    "name": "input_format",
                    "type": "string",
                    "description": "输入时间格式（如：%Y-%m-%d %H:%M:%S）",
                    "required": False,
                    "default": "%Y-%m-%d %H:%M:%S"
                },
                {
                    "name": "output_format",
                    "type": "string",
                    "description": "输出时间格式（如：%Y-%m-%d %H:%M:%S）",
                    "required": False,
                    "default": "%Y-%m-%d %H:%M:%S"
                },
                {
                    "name": "from_timezone",
                    "type": "string",
                    "description": "源时区（如：UTC, Asia/Shanghai）",
                    "required": False,
                    "default": "Asia/Shanghai"
                },
                {
                    "name": "to_timezone",
                    "type": "string",
                    "description": "目标时区（如：UTC, Asia/Shanghai）",
                    "required": False,
                    "default": "Asia/Shanghai"
                }
            ]
        elif operation == "timestamp_to_datetime":
            return [
                {
                    "name": "input_value",
                    "type": "string",
                    "description": "输入值（时间字符串或时间戳）",
                    "required": True
                },
                {
                    "name": "output_format",
                    "type": "string",
                    "description": "输出时间格式（如：%Y-%m-%d %H:%M:%S）",
                    "required": False,
                    "default": "%Y-%m-%d %H:%M:%S"
                },
                {
                    "name": "to_timezone",
                    "type": "string",
                    "description": "目标时区（如：UTC, Asia/Shanghai）",
                    "required": False,
                    "default": "Asia/Shanghai"
                }
            ]
        elif operation == "datetime_to_timestamp":
            return [
                {
                    "name": "input_value",
                    "type": "string",
                    "description": "输入值（时间字符串，如：2026-04-07 10:30:25）",
                    "required": True
                },
                {
                    "name": "input_format",
                    "type": "string",
                    "description": "输入时间格式（如：%Y-%m-%d %H:%M:%S）",
                    "required": False,
                    "default": "%Y-%m-%d %H:%M:%S"
                },
                {
                    "name": "from_timezone",
                    "type": "string",
                    "description": "源时区（如：UTC, Asia/Shanghai）",
                    "required": False,
                    "default": "Asia/Shanghai"
                }
            ]
        else:
            # Default: return all parameters except operation
            return [
                {
                    "name": "input_value",
                    "type": "string",
                    "description": "输入值（时间字符串或时间戳）",
                    "required": False
                },
                {
                    "name": "input_format",
                    "type": "string",
                    "description": "输入时间格式（如：%Y-%m-%d %H:%M:%S）",
                    "required": False,
                    "default": "%Y-%m-%d %H:%M:%S"
                },
                {
                    "name": "output_format",
                    "type": "string",
                    "description": "输出时间格式（如：%Y-%m-%d %H:%M:%S）",
                    "required": False,
                    "default": "%Y-%m-%d %H:%M:%S"
                },
                {
                    "name": "from_timezone",
                    "type": "string",
                    "description": "源时区（如：UTC, Asia/Shanghai）",
                    "required": False,
                    "default": "Asia/Shanghai"
                },
                {
                    "name": "to_timezone",
                    "type": "string",
                    "description": "目标时区（如：UTC, Asia/Shanghai）",
                    "required": False,
                    "default": "Asia/Shanghai"
                },
                {
                    "name": "calculation",
                    "type": "string",
                    "description": "时间计算表达式（如：+1d, -2h, +30m）",
                    "required": False
                }
            ]

    def _get_json_tool_params(self, operation: str) -> list[dict[str, Any]]:
        """Get parameters for a specific json_tool operation."""
        base_params = [
            {
                "name": "input_data",
                "type": "string",
                "description": "输入数据（JSON字符串、YAML字符串或XML字符串）",
                "required": True
            }
        ]

        if operation == "insert":
            return base_params + [
                {
                    "name": "json_path",
                    "type": "string",
                    "description": "JSON路径表达式（如：$.user.name或users[0].name）",
                    "required": True
                },
                {
                    "name": "new_value",
                    "type": "string",
                    "description": "新值（用于insert操作）",
                    "required": True
                }
            ]
        elif operation == "replace":
            return base_params + [
                {
                    "name": "json_path",
                    "type": "string",
                    "description": "JSON路径表达式（如：$.user.name或users[0].name）",
                    "required": True
                },
                {
                    "name": "old_text",
                    "type": "string",
                    "description": "要替换的原文本（用于replace操作）",
                    "required": True
                },
                {
                    "name": "new_text",
                    "type": "string",
                    "description": "替换后的新文本（用于replace操作）",
                    "required": True
                }
            ]
        elif operation == "delete":
            return base_params + [
                {
                    "name": "json_path",
                    "type": "string",
                    "description": "JSON路径表达式（如：$.user.name或users[0].name）",
                    "required": True
                }
            ]
        elif operation == "parse":
            return base_params + [
                {
                    "name": "json_path",
                    "type": "string",
                    "description": "JSON路径表达式（如：$.user.name或users[0].name）",
                    "required": True
                }
            ]

        return base_params

    @staticmethod
    def _get_openclaw_tool_params(operation: str) -> list[dict[str, Any]]:
        """Get parameters for a specific openclaw_tool operation."""
        if operation == "print_task":
            return [
                {
                    "name": "message",
                    "type": "string",
                    "description": (
                        "发送给 OpenClaw 的打印任务描述，将用户的原始消息原封不动地传递给 "
                        "OpenClaw，禁止改写、补充或润色用户的原文"
                    ),
                    "required": True
                },
                {
                    "name": "image_url",
                    "type": "string",
                    "description": "可选，附带的设计图片或参考图，OpenClaw 可据此生成 3D 模型",
                    "required": False
                }
            ]
        elif operation == "device_query":
            return [
                {
                    "name": "message",
                    "type": "string",
                    "description": "发送给 OpenClaw 的设备查询指令",
                    "required": True
                }
            ]
        elif operation == "image_understand":
            return [
                {
                    "name": "message",
                    "type": "string",
                    "description": (
                        "发送给 OpenClaw 的图片理解任务，应描述需要对图片做什么"
                        "（如描述内容、提取文字、分析信息）"
                    ),
                    "required": True
                },
                {
                    "name": "image_url",
                    "type": "string",
                    "description": "要分析的图片 URL 或 base64 data URI",
                    "required": False
                }
            ]
        else:
            # general and others
            return [
                {
                    "name": "message",
                    "type": "string",
                    "description": "发送给 OpenClaw Agent 的任务描述，应包含完整的任务需求",
                    "required": True
                },
                {
                    "name": "image_url",
                    "type": "string",
                    "description": "可选，附带的图片 URL 或 base64 data URI",
                    "required": False
                }
            ]

    async def _get_custom_tool_methods(self, config: ToolConfig) -> list[dict[str, Any]]:
        """Get the methods of a custom tool."""
        custom_config = self.custom_repo.find_by_tool_id(config.id)
        if not custom_config:
            return []

        try:
            from ..tools.custom.schema_parser import OpenAPISchemaParser
            parser = OpenAPISchemaParser()

            # Parse the schema
            if custom_config.schema_content:
                success, schema, error = parser.parse_from_content(
                    custom_config.schema_content, "application/json"
                )
            elif custom_config.schema_url:
                success, schema, error = parser.parse_from_url(custom_config.schema_url)
            else:
                return []

            if not success:
                return []

            # Extract operations
            tool_info = parser.extract_tool_info(schema)
            operations = tool_info.get("operations", {})

            methods = []
            for operation_id, operation in operations.items():
                # Build the parameter list
                parameters = []

                # Path and query parameters
                for param_name, param_info in operation.get("parameters", {}).items():
                    parameters.append({
                        "name": param_name,
                        "type": param_info.get("type", "string"),
                        "description": param_info.get("description", ""),
                        "required": param_info.get("required", False),
                        "enum": param_info.get("enum"),
                        "default": param_info.get("default")
                    })

                # Body params — _extract_request_body returns {"schema": ..., "required": ...}
                request_body = operation.get("request_body")
                if request_body:
                    body_schema = request_body.get("schema", {})
                    schema_props = body_schema.get("properties", {})
                    required_props = body_schema.get("required", [])

                    for prop_name, prop_schema in schema_props.items():
                        parameters.append({
                            "name": prop_name,
                            "type": prop_schema.get("type", "string"),
                            "description": prop_schema.get("description", ""),
                            "required": prop_name in required_props,
                            "enum": prop_schema.get("enum"),
                            "default": prop_schema.get("default")
                        })

                methods.append({
                    "method_id": operation_id,
                    "name": operation.get("summary", operation_id),
                    "description": operation.get("description", ""),
                    "method": operation.get("method", "GET"),
                    "path": operation.get("path", "/"),
                    "parameters": parameters
                })

            return methods

        except Exception as e:
            logger.error(f"解析自定义工具schema失败: {e}")
            return []

    async def _get_mcp_tool_methods(self, config: ToolConfig) -> list[dict[str, Any]]:
        """Get the methods and parameters of an MCP tool."""
        mcp_config = self.mcp_repo.find_by_tool_id(config.id)
        if not mcp_config:
            return []

        available_tools = mcp_config.available_tools or []
        if not available_tools:
            # If no tool list is present, try syncing
            try:
                success, tools, _ = await self.mcp_tool_manager.discover_tools(
                    mcp_config.server_url, mcp_config.connection_config or {}
                )
                if success:
                    # Convert to the new format
                    tool_list = []
                    for tool in tools:
                        if tool.get("name"):
                            tool_list.append({
                                tool["name"]: {
                                    "description": tool.get("description", ""),
                                    "inputSchema": tool.get("inputSchema", {})
                                }
                            })
                    mcp_config.available_tools = tool_list
                    self.db.commit()
                    available_tools = tool_list
            except Exception as e:
                logger.error(f"同步MCP工具列表失败: {e}")
                return []

        methods = []

        # Handle available_tools in the new format
        for tool_item in available_tools:
            if isinstance(tool_item, dict):
                for tool_name, tool_data in tool_item.items():
                    # Parse tool parameters
                    parameters = []
                    input_schema = tool_data.get("inputSchema", {})
                    properties = input_schema.get("properties", {})
                    required_fields = input_schema.get("required", [])

                    for param_name, param_def in properties.items():
                        parameters.append({
                            "name": param_name,
                            "type": param_def.get("type", "string"),
                            "description": param_def.get("description", ""),
                            "required": param_name in required_fields,
                            "default": param_def.get("default"),
                            "enum": param_def.get("enum"),
                            "minimum": param_def.get("minimum"),
                            "maximum": param_def.get("maximum")
                        })

                    methods.append({
                        "method_id": tool_name,
                        "name": tool_name,
                        "description": tool_data.get("description", f"MCP工具: {tool_name}"),
                        "parameters": parameters
                    })
            else:
                # Legacy format compatibility (plain strings)
                tool_name = str(tool_item)
                methods.append({
                    "method_id": tool_name,
                    "name": tool_name,
                    "description": f"MCP工具: {tool_name}",
                    "parameters": []
                })

        return methods

    def get_tool_statistics(self, tenant_id: uuid.UUID) -> dict[str, Any]:
        """Get tool statistics."""
        try:
            # Total count
            total_tools = self.tool_repo.count_by_tenant(tenant_id)

            # Status statistics
            status_counts = self.tool_repo.get_status_statistics(tenant_id)

            # Type statistics
            type_counts = self.tool_repo.get_type_statistics(tenant_id)

            # Enabled/disabled counts
            enabled_count = self.tool_repo.count_enabled_by_tenant(tenant_id)
            disabled_count = total_tools - enabled_count

            return {
                "total_tools": total_tools,
                "status_counts": [
                    {"status": status, "count": count}
                    for status, count in status_counts
                ],
                "type_counts": {
                    tool_type: count for tool_type, count in type_counts
                },
                "enabled_count": enabled_count,
                "disabled_count": disabled_count
            }
        except Exception as e:
            logger.error(f"获取工具统计失败: {e}")
            return {
                "total_tools": 0,
                "status_counts": [],
                "type_counts": {},
                "enabled_count": 0,
                "disabled_count": 0
            }

    def _get_tool_config(self, tool_id: str, tenant_id: uuid.UUID) -> ToolConfig | None:
        """Get tool config (only is_active=True records)."""
        return self.tool_repo.find_by_id_and_tenant(uuid.UUID(tool_id), tenant_id)

    def _get_tool_config_all(self, tool_id: str, tenant_id: uuid.UUID) -> ToolConfig | None:
        """Get tool config (all records)."""
        return self.tool_repo.find_by_id_and_tenant_all(uuid.UUID(tool_id), tenant_id)

    def get_tool_instance(self, tool_id: str, tenant_id: uuid.UUID) -> BaseTool | None:
        """Get a tool instance (only for is_active=True tools)."""
        if tool_id in self._tool_cache:
            return self._tool_cache[tool_id]

        config = self._get_tool_config(tool_id, tenant_id)
        if not config or not config.is_active:
            return None

        try:
            tool = self._create_tool_instance(config)
            if tool:
                self._tool_cache[tool_id] = tool
            return tool
        except Exception as e:
            logger.error(f"创建工具实例失败: {tool_id}, {e}")
            return None

    def _create_tool_instance(self, config: ToolConfig) -> BaseTool | None:
        """Create a tool instance."""
        if config.tool_type == ToolType.BUILTIN.value:
            return self._create_builtin_instance(config)
        elif config.tool_type == ToolType.CUSTOM.value:
            return self._create_custom_instance(config)
        elif config.tool_type == ToolType.MCP.value:
            return self._create_mcp_instance(config)
        elif config.tool_type == ToolType.WORKFLOW.value:
            return self._create_workflow_instance(config)
        return None

    def _create_builtin_instance(self, config: ToolConfig) -> BaseTool | None:
        """Create a builtin tool instance."""
        builtin_config = self.builtin_repo.find_by_tool_id(config.id)

        if not builtin_config or builtin_config.tool_class not in BUILTIN_TOOLS:
            return None

        try:
            module_path = BUILTIN_TOOLS[builtin_config.tool_class]
            module = importlib.import_module(module_path)
            tool_class = getattr(module, builtin_config.tool_class)

            tool_config = {
                **config.config_data,
                "parameters": builtin_config.parameters,
            }

            return tool_class(str(config.id), tool_config)
        except Exception as e:
            logger.error(f"创建内置工具实例失败: {builtin_config.tool_class}, {e}")
            return None

    def _create_custom_instance(self, config: ToolConfig) -> CustomTool | None:
        """Create a custom tool instance."""
        custom_config = self.custom_repo.find_by_tool_id(config.id)

        if not custom_config:
            return None

        tool_config = {
            "base_url": custom_config.base_url,
            "auth_type": custom_config.auth_type,
            "auth_config": custom_config.auth_config or {},
            "timeout": custom_config.timeout or 30,
            "schema_content": custom_config.schema_content,
            "schema_url": custom_config.schema_url
        }

        return CustomTool(str(config.id), tool_config)

    def _create_mcp_instance(self, config: ToolConfig) -> MCPTool | None:
        """Create an MCP tool instance."""
        mcp_config = self.mcp_repo.find_by_tool_id(config.id)

        if not mcp_config:
            return None

        tool_config = {
            "server_url": mcp_config.server_url,
            "connection_config": mcp_config.connection_config or {},
            "available_tools": mcp_config.available_tools or []
        }

        return MCPTool(str(config.id), tool_config)

    def _create_workflow_instance(self, config: ToolConfig) -> WorkflowAsTool | None:
        """Create a workflow tool instance."""
        workflow_config = self.workflow_repo.find_by_tool_id(config.id)

        if not workflow_config:
            return None

        return WorkflowAsTool(
            db=self.db,
            tool_id=str(config.id),
            workflow_app_id=workflow_config.app_id,
            release_id=workflow_config.release_id,
            tool_name=config.name,
            tool_description=config.description or "",
            input_parameters=workflow_config.input_parameters or [],
            output_schema=workflow_config.output_schema or {},
            timeout=workflow_config.timeout or 300,
        )

    def _config_to_info(self, config: ToolConfig) -> ToolInfo:
        """Convert a config row to a ToolInfo object."""
        config_data = config.config_data or {}

        # For MCP tools, fetch extra info from MCPToolConfig
        if config.tool_type == ToolType.MCP.value:
            mcp_config = self.mcp_repo.find_by_tool_id(config.id)
            if mcp_config:
                # Normalize the available_tools display format
                available_tools_display = []
                for tool_item in (mcp_config.available_tools or []):
                    if isinstance(tool_item, dict):
                        available_tools_display.extend(list(tool_item.keys()))
                    else:
                        available_tools_display.append(str(tool_item))

                config_data.update({
                    "last_health_check": to_timestamp_ms(mcp_config.last_health_check),
                    "health_status": mcp_config.health_status,
                    "available_tools": available_tools_display,
                    "source_channel": mcp_config.source_channel,
                    "market_id": mcp_config.market_id,
                    "market_config_id": mcp_config.market_config_id,
                    "mcp_service_id": mcp_config.mcp_service_id
                })
        elif config.tool_type == ToolType.WORKFLOW.value:
            workflow_config = self.workflow_repo.find_by_tool_id(config.id)
            if workflow_config:
                config_data.update({
                    "app_id": str(workflow_config.app_id),
                    "workflow_config_id": str(workflow_config.workflow_config_id),
                    "release_id": (
                        str(workflow_config.release_id) if workflow_config.release_id else None
                    ),
                    "input_parameters": workflow_config.input_parameters or [],
                    "output_schema": workflow_config.output_schema or {},
                    "timeout": workflow_config.timeout or 300,
                })

        return ToolInfo(
            id=str(config.id),
            name=config.name,
            description=config.description or "",
            icon=config.icon,
            tool_type=ToolType(config.tool_type),
            version=config.version or "1.0.0",
            status=ToolStatus(config.status),
            tags=config.tags or [],
            tenant_id=str(config.tenant_id) if config.tenant_id else None,
            config_data=config_data,
            is_active=config.is_active,
            created_at=config.created_at
        )

    def _config_to_detail(self, config: ToolConfig) -> ToolDetailSchema:
        """Convert a config row to a ToolDetailSchema object."""
        info = self._config_to_info(config)
        detail_data = info.model_dump()
        detail_data["updated_at"] = config.updated_at or config.created_at

        builtin_config = None
        custom_config = None
        mcp_config = None
        workflow_config = None

        if config.tool_type == ToolType.BUILTIN.value:
            cfg = self.builtin_repo.find_by_tool_id(config.id)
            if cfg:
                builtin_config = BuiltinToolConfigSchema.model_validate(cfg)
        elif config.tool_type == ToolType.CUSTOM.value:
            cfg = self.custom_repo.find_by_tool_id(config.id)
            if cfg:
                custom_config = CustomToolConfigSchema.model_validate(cfg)
        elif config.tool_type == ToolType.MCP.value:
            cfg = self.mcp_repo.find_by_tool_id(config.id)
            if cfg:
                mcp_config = MCPToolConfigSchema.model_validate({
                    "server_url": cfg.server_url,
                    "connection_config": cfg.connection_config or {},
                    "last_health_check": cfg.last_health_check,
                    "health_status": cfg.health_status,
                    "error_message": cfg.error_message,
                    "available_tools": cfg.available_tools or [],
                    "source_channel": cfg.source_channel,
                    "market_id": str(cfg.market_id) if cfg.market_id else None,
                    "market_config_id": str(cfg.market_config_id) if cfg.market_config_id else None,
                    "mcp_service_id": cfg.mcp_service_id,
                })
        elif config.tool_type == ToolType.WORKFLOW.value:
            cfg = self.workflow_repo.find_by_tool_id(config.id)
            if cfg:
                workspace_id = None
                app = self.db.query(App).filter(App.id == cfg.app_id).first()
                if app:
                    workspace_id = str(app.workspace_id)
                workflow_config = WorkflowToolConfigSchema.model_validate({
                    "app_id": str(cfg.app_id),
                    "workflow_config_id": str(cfg.workflow_config_id),
                    "release_id": str(cfg.release_id) if cfg.release_id else None,
                    "input_parameters": cfg.input_parameters or [],
                    "output_schema": cfg.output_schema or {},
                    "timeout": cfg.timeout or 300,
                    "workspace_id": workspace_id,
                })

        detail_data.update({
            "builtin_config": builtin_config,
            "custom_config": custom_config,
            "mcp_config": mcp_config,
            "workflow_config": workflow_config,
        })
        detail_data["config_data"] = self._strip_type_specific_config_data_for_detail(
            config.tool_type,
            detail_data.get("config_data", {}),
        )
        return ToolDetailSchema.model_validate(detail_data)

    @staticmethod
    def _strip_type_specific_config_data_for_detail(
        tool_type: str,
        config_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Strip fields already carried by type-specific config from the detail response."""
        if not config_data:
            return {}

        duplicate_fields_map = {
            ToolType.BUILTIN.value: {"tool_class", "requires_config", "is_enabled", "parameters"},
            ToolType.CUSTOM.value: {
                "base_url", "auth_type", "auth_config", "timeout", "schema_content", "schema_url",
            },
            ToolType.MCP.value: {
                "last_health_check", "health_status", "available_tools",
                "source_channel", "market_id", "market_config_id", "mcp_service_id",
                "server_url", "connection_config", "error_message",
            },
            ToolType.WORKFLOW.value: {
                "app_id", "workflow_config_id", "release_id", "input_parameters", "output_schema",
                "timeout", "workspace_id"
            },
        }
        duplicate_fields = duplicate_fields_map.get(tool_type, set())
        return {k: v for k, v in config_data.items() if k not in duplicate_fields}

    async def _get_workflow_tool_methods(self, config: ToolConfig) -> list[dict[str, Any]]:
        """Get the method list of a workflow tool."""
        tool_instance = self.get_tool_instance(str(config.id), config.tenant_id)
        if not tool_instance:
            return []

        return [{
            "method_id": config.name,
            "name": config.name,
            "description": config.description,
            "parameters": [serialize_tool_parameter(param) for param in tool_instance.parameters]
        }]

    @staticmethod
    def _normalize_schema_content(value: Any) -> dict[str, Any] | None:
        """Normalize a JSON string submitted from the frontend textarea into a dict."""
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            value = json.loads(value)
        return value

    def _create_type_config(self, tool_config: ToolConfig, config: dict[str, Any]) -> None:
        """Create the type-specific config."""
        if tool_config.tool_type == ToolType.CUSTOM.value:
            # Derive base_url from the schema
            base_url = config.get("base_url")
            if not base_url and (config.get("schema_content") or config.get("schema_url")):
                try:
                    from ..tools.custom.schema_parser import OpenAPISchemaParser
                    parser = OpenAPISchemaParser()

                    if config.get("schema_content"):
                        success, schema, _ = parser.parse_from_content(
                            config["schema_content"], "application/json"
                        )
                    else:
                        # The host called an async parser here without awaiting, so
                        # this branch silently failed there; with the sync parser it
                        # now actually derives base_url for schema_url-only payloads.
                        success, schema, _ = parser.parse_from_url(config["schema_url"])

                    if success:
                        tool_info = parser.extract_tool_info(schema)
                        servers = tool_info.get("servers", [])
                        base_url = servers[0].get("url") if servers else ""
                except Exception as e:
                    logger.error(f"解析schema获取base_url失败: {e}")

            custom_config = CustomToolConfig(
                id=tool_config.id,
                base_url=base_url,
                auth_type=config.get("auth_type", "none"),
                auth_config=config.get("auth_config", {}),
                timeout=config.get("timeout", 30),
                schema_content=self._normalize_schema_content(config.get("schema_content")),
                schema_url=config.get("schema_url")
            )
            self.db.add(custom_config)

        elif tool_config.tool_type == ToolType.MCP.value:
            mcp_config = MCPToolConfig(
                id=tool_config.id,
                server_url=config.get("server_url"),
                connection_config=config.get("connection_config", {}),
                available_tools=config.get("available_tools", []),
                source_channel=config.get("source_channel", "self_hosted"),
                market_id=config.get("market_id"),
                market_config_id=config.get("market_config_id"),
                mcp_service_id=config.get("mcp_service_id"),
            )
            self.db.add(mcp_config)

    def _sync_type_config(
            self,
            tool_config: ToolConfig,
            config: dict[str, Any],
            is_enabled: bool | None
    ) -> None:
        """Sync to the type-specific table."""
        if tool_config.tool_type == ToolType.BUILTIN.value:
            builtin_config = self.db.query(BuiltinToolConfig).filter(
                BuiltinToolConfig.id == tool_config.id
            ).first()
            if builtin_config:
                builtin_config.parameters = config.get("parameters", {})
                if is_enabled is not None:
                    builtin_config.is_enabled = is_enabled

        elif tool_config.tool_type == ToolType.CUSTOM.value:
            custom_config = self.db.query(CustomToolConfig).filter(
                CustomToolConfig.id == tool_config.id
            ).first()
            if custom_config:
                base_url = config.get("base_url")
                if not base_url and (config.get("schema_content") or config.get("schema_url")):
                    try:
                        from ..tools.custom.schema_parser import OpenAPISchemaParser
                        parser = OpenAPISchemaParser()

                        if config.get("schema_content"):
                            success, schema, _ = parser.parse_from_content(
                                config["schema_content"], "application/json"
                            )
                        else:
                            success, schema, _ = parser.parse_from_url(config["schema_url"])

                        if success:
                            tool_info = parser.extract_tool_info(schema)
                            servers = tool_info.get("servers", [])
                            base_url = servers[0].get("url") if servers else ""
                    except Exception as e:
                        logger.error(f"解析schema获取base_url失败: {e}")
                custom_config.base_url = base_url
                custom_config.auth_type = config.get("auth_type", "none")
                custom_config.auth_config = config.get("auth_config", {})
                custom_config.timeout = config.get("timeout", 30)
                custom_config.schema_content = self._normalize_schema_content(
                    config.get("schema_content")
                )
                custom_config.schema_url = config.get("schema_url")

        elif tool_config.tool_type == ToolType.MCP.value:
            mcp_config = self.db.query(MCPToolConfig).filter(
                MCPToolConfig.id == tool_config.id
            ).first()
            if mcp_config:
                mcp_config.server_url = config.get("server_url")
                mcp_config.connection_config = config.get("connection_config", {})
                mcp_config.available_tools = config.get("available_tools", [])
                if config.get("source_channel") is not None:
                    mcp_config.source_channel = config.get("source_channel")
                if config.get("market_id") is not None:
                    mcp_config.market_id = config.get("market_id")
                if config.get("market_config_id") is not None:
                    mcp_config.market_config_id = config.get("market_config_id")
                if config.get("mcp_service_id") is not None:
                    mcp_config.mcp_service_id = config.get("mcp_service_id")

    @staticmethod
    def _determine_initial_status(tool_info: dict[str, Any]) -> str:
        """Determine the initial tool status."""
        if tool_info.get('requires_config', False):
            return ToolStatus.UNCONFIGURED
        else:
            return ToolStatus.AVAILABLE

    def _update_tool_status(self, tool_config: ToolConfig) -> None:
        """Update the tool status logic."""
        if tool_config.tool_type == ToolType.BUILTIN.value:
            builtin_config = self.db.query(BuiltinToolConfig).filter(
                BuiltinToolConfig.id == tool_config.id
            ).first()

            if builtin_config:
                if builtin_config.requires_config:
                    # Tool that requires configuration
                    if self._is_tool_configured(builtin_config):
                        if builtin_config.is_enabled:
                            tool_config.status = ToolStatus.AVAILABLE.value
                        else:
                            tool_config.status = ToolStatus.CONFIGURED_DISABLED.value
                    else:
                        tool_config.status = ToolStatus.UNCONFIGURED.value
                else:
                    # Tool that needs no configuration
                    tool_config.status = ToolStatus.AVAILABLE.value

        elif tool_config.tool_type == ToolType.CUSTOM.value:
            custom_config = self.db.query(CustomToolConfig).filter(
                CustomToolConfig.id == tool_config.id
            ).first()

            if custom_config and tool_config.name and (
                    custom_config.schema_content or custom_config.schema_url):
                tool_config.status = ToolStatus.AVAILABLE.value
            else:
                tool_config.status = ToolStatus.UNCONFIGURED.value

        elif tool_config.tool_type == ToolType.MCP.value:
            mcp_config = self.db.query(MCPToolConfig).filter(
                MCPToolConfig.id == tool_config.id
            ).first()

            if mcp_config:
                if mcp_config.health_status == "healthy":
                    tool_config.status = ToolStatus.AVAILABLE.value
                elif mcp_config.health_status == "error":
                    tool_config.status = ToolStatus.ERROR.value
                else:
                    tool_config.status = ToolStatus.UNCONFIGURED.value

    def _is_tool_configured(self, builtin_config: BuiltinToolConfig) -> bool:
        """Check whether the tool is configured."""
        # Load required parameters from the builtin config file
        builtin_config_data = self._load_builtin_config()
        required_params = {}
        for _key, value in builtin_config_data.items():
            if builtin_config.tool_class == value["tool_class"]:
                required_params = value.get('parameters', {})
                break

        # Check that every required parameter is configured
        for param_name, param_info in required_params.items():
            if param_info.get('required', False):
                if not builtin_config.parameters.get(param_name):
                    return False
        return True

    def _clear_tool_cache(self, tool_id: str) -> None:
        """Clear the tool cache."""
        if tool_id in self._tool_cache:
            del self._tool_cache[tool_id]

    @staticmethod
    def _load_builtin_config() -> dict[str, Any]:
        """Load the builtin tool config."""
        import json
        from pathlib import Path

        config_file = Path(__file__).parent.parent / "tools" / "configs" / "builtin_tools.json"
        try:
            with open(config_file, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载内置工具配置失败: {e}")
            return {}

    async def _test_mcp_connection_by_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Test an MCP connection directly from config (called before creating the tool record)."""
        server_url = config.get("server_url")
        if not server_url:
            return {"success": False, "message": "server_url不能为空"}
        connection_config = config.get("connection_config") or {}
        try:
            test_result = await self.mcp_tool_manager.test_tool_connection(
                server_url, connection_config
            )
            if not test_result["success"]:
                return test_result
            success_flag, tools, error = await self.mcp_tool_manager.discover_tools(
                server_url, connection_config
            )
            if not success_flag:
                return {"success": False, "message": f"获取工具列表失败: {error}"}
            tool_list = [
                {
                    tool["name"]: {
                        "description": tool.get("description", ""),
                        "inputSchema": tool.get("inputSchema", {})
                    }
                }
                for tool in tools if tool.get("name")
            ]
            return {"success": True, "message": "MCP连接测试成功", "available_tools": tool_list}
        except Exception as e:
            return {"success": False, "message": f"连接测试异常: {str(e)}"}

    @staticmethod
    def parse_openapi_schema(
        schema_data: str | None = None,
        schema_url: str | None = None,
    ) -> dict[str, Any]:
        """Parse an OpenAPI schema to obtain interface info."""
        try:
            from ..tools.custom.schema_parser import OpenAPISchemaParser

            parser = OpenAPISchemaParser()

            if schema_data:
                success, schema, error = parser.parse_from_content(schema_data, "application/json")
            elif schema_url:
                success, schema, error = parser.parse_from_url(schema_url)
            else:
                return {"success": False, "message": "schema_data或schema_url必须提供一个"}

            if not success:
                return {"success": False, "message": error}

            # Extract tool info
            tool_info = parser.extract_tool_info(schema)

            # Resolve base_url
            servers = tool_info.get("servers", [])
            base_url = servers[0].get("url") if servers else ""

            return {
                "success": True,
                "data": {
                    "title": tool_info["name"],
                    "description": tool_info["description"],
                    "version": tool_info["version"],
                    "base_url": base_url,
                    "operations": list(tool_info["operations"].values())
                }
            }

        except Exception as e:
            logger.error(f"解析OpenAPI schema失败: {e}")
            return {"success": False, "message": f"解析失败: {str(e)}"}


__all__ = ["ToolService"]

"""工具服务 - 统一的工具管理和执行服务"""
import json
import uuid
import time
import importlib
from typing import Dict, Any, List, Optional
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.tools.mcp import MCPClient
from app.repositories.tool_repository import (
    ToolRepository, BuiltinToolRepository, CustomToolRepository,
    MCPToolRepository, ToolExecutionRepository
)

from app.models.tool_model import (
    ToolConfig, BuiltinToolConfig, CustomToolConfig, MCPToolConfig,
    ToolExecution, ToolType, ToolStatus, ExecutionStatus, AuthType
)
from app.schemas.tool_schema import ToolInfo, ToolResult
from app.core.logging_config import get_business_logger
from app.core.tools.base import BaseTool
from app.core.tools.custom.base import CustomTool
from app.core.tools.mcp.base import MCPTool

logger = get_business_logger()

# 内置工具映射
BUILTIN_TOOLS = {
    "DateTimeTool": "app.core.tools.builtin.datetime_tool",
    "JsonTool": "app.core.tools.builtin.json_tool",
    "BaiduSearchTool": "app.core.tools.builtin.baidu_search_tool",
    "MinerUTool": "app.core.tools.builtin.mineru_tool",
    "TextInTool": "app.core.tools.builtin.textin_tool"
}


class ToolService:
    """统一工具服务 - 管理工具的完整生命周期"""

    def __init__(self, db: Session):
        self.db = db
        self._tool_cache: Dict[str, BaseTool] = {}

        # 初始化仓储
        self.tool_repo = ToolRepository()
        self.builtin_repo = BuiltinToolRepository()
        self.custom_repo = CustomToolRepository()
        self.mcp_repo = MCPToolRepository()
        self.execution_repo = ToolExecutionRepository()

    def list_tools(
            self,
            tenant_id: uuid.UUID,
            name: Optional[str] = None,
            tool_type: Optional[ToolType] = None,
            status: Optional[ToolStatus] = None
    ) -> List[ToolInfo]:
        """获取工具列表"""
        try:
            configs = self.tool_repo.find_by_tenant(
                db=self.db,
                tenant_id=tenant_id,
                name=name,
                tool_type=tool_type,
                status=status
            )
            return [self._config_to_info(config) for config in configs]
        except Exception as e:
            logger.error(f"获取工具列表失败: {e}")
            return []

    def get_tool_info(self, tool_id: str, tenant_id: uuid.UUID) -> Optional[ToolInfo]:
        """获取工具详情"""
        config = self.tool_repo.find_by_id_and_tenant(self.db, uuid.UUID(tool_id), tenant_id)
        return self._config_to_info(config) if config else None

    def create_tool(
            self,
            name: str,
            tool_type: ToolType,
            tenant_id: uuid.UUID,
            icon: Optional[str] = None,
            description: Optional[str] = None,
            config: Optional[Dict[str, Any]] = None,
            tags: Optional[List[str]] = None
    ) -> str:
        """创建工具"""
        if tool_type == ToolType.BUILTIN:
            raise ValueError("内置工具不允许创建")

        try:
            # 创建基础配置
            tool_config = ToolConfig(
                name=name,
                description=description,
                icon=icon,
                tool_type=tool_type.value,
                tenant_id=tenant_id,
                status=ToolStatus.AVAILABLE.value,
                config_data=config or {},
                tags=tags
            )
            self.db.add(tool_config)
            self.db.flush()

            # 创建类型特定配置
            self._create_type_config(tool_config, config or {})

            self.db.commit()
            logger.info(f"工具创建成功: {tool_config.id}")
            return str(tool_config.id)

        except Exception as e:
            self.db.rollback()
            logger.error(f"创建工具失败: {e}")
            raise

    def update_tool(
            self,
            tool_id: str,
            tenant_id: uuid.UUID,
            name: Optional[str] = None,
            description: Optional[str] = None,
            icon: Optional[str] = None,
            config: Optional[Dict[str, Any]] = None,
            is_enabled: Optional[bool] = None,
            tags: Optional[List[str]] = None
    ) -> bool:
        """更新工具"""
        config_obj = self._get_tool_config(tool_id, tenant_id)
        if not config_obj:
            return False

        if config_obj.tool_type == ToolType.BUILTIN.value:
            if name or description or icon:
                raise ValueError("内置工具不允许修改名称、描述和图标")
        try:
            if name:
                config_obj.name = name
            if description:
                config_obj.description = description
            if icon:
                config_obj.icon = icon
            if tags:
                config_obj.tags = tags
            if config:
                config_obj.config_data = config.copy()

                # 同步到类型表
                self._sync_type_config(config_obj, config, is_enabled)

                # 更新状态逻辑
                self._update_tool_status(config_obj)

            # 清除缓存
            self._clear_tool_cache(tool_id)

            self.db.commit()
            return True

        except Exception as e:
            self.db.rollback()
            logger.error(f"更新工具失败: {tool_id}, {e}")
            return False

    def delete_tool(self, tool_id: str, tenant_id: uuid.UUID) -> bool:
        """删除工具"""
        config = self._get_tool_config(tool_id, tenant_id)
        if not config:
            return False

        if config.tool_type == ToolType.BUILTIN.value:
            raise ValueError("内置工具不允许删除")

        try:
            # 删除关联表记录
            if config.tool_type == ToolType.CUSTOM.value:
                self.db.query(CustomToolConfig).filter(CustomToolConfig.id == config.id).delete()
            elif config.tool_type == ToolType.MCP.value:
                self.db.query(MCPToolConfig).filter(MCPToolConfig.id == config.id).delete()
            
            # 删除主表记录（ToolExecution会通过cascade自动删除）
            self.db.delete(config)
            self._clear_tool_cache(tool_id)
            self.db.commit()
            return True
        except Exception as e:
            self.db.rollback()
            logger.error(f"删除工具失败: {tool_id}, {e}")
            return False

    async def execute_tool(
            self,
            tool_id: str,
            parameters: Dict[str, Any],
            tenant_id: uuid.UUID,
            user_id: Optional[uuid.UUID] = None,
            workspace_id: Optional[uuid.UUID] = None,
            timeout: float = 60.0
    ) -> ToolResult:
        """执行工具"""
        execution_id = f"exec_{uuid.uuid4().hex[:16]}"
        start_time = time.time()

        try:
            # 获取工具实例
            tool = self._get_tool_instance(tool_id, tenant_id)
            if not tool:
                return ToolResult.error_result(
                    error=f"工具不存在: {tool_id}",
                    execution_time=time.time() - start_time
                )

            # 记录执行开始
            self._record_execution_start(
                execution_id, tool_id, parameters, user_id, workspace_id
            )

            # 执行工具
            result = await tool.safe_execute(**parameters)

            # 记录执行完成
            self._record_execution_complete(execution_id, result)

            return result

        except Exception as e:
            execution_time = time.time() - start_time
            error_result = ToolResult.error_result(
                error=str(e),
                execution_time=execution_time
            )
            self._record_execution_complete(execution_id, error_result)
            return error_result

    async def test_connection(self, tool_id: str, tenant_id: uuid.UUID) -> Dict[str, Any]:
        """测试工具连接"""
        try:
            config = self._get_tool_config(tool_id, tenant_id)
            if not config:
                return {"success": False, "message": "工具不存在"}

            if config.tool_type == ToolType.MCP.value:
                return await self._test_mcp_connection(config)
            elif config.tool_type == ToolType.CUSTOM.value:
                return await self._test_custom_connection(config)
            elif config.tool_type == ToolType.BUILTIN.value:
                return await self._test_builtin_connection(config)
            else:
                return {"success": True, "message": "未知工具类型"}

        except Exception as e:
            return {"success": False, "message": f"测试失败: {str(e)}"}

    def ensure_builtin_tools_initialized(self, tenant_id: uuid.UUID):
        """确保内置工具已初始化"""
        existing = self.tool_repo.exists_builtin_for_tenant(self.db, tenant_id)

        if existing:
            return

        # 从配置文件加载内置工具定义
        builtin_config = self._load_builtin_config()

        for tool_key, tool_info in builtin_config.items():
            try:
                # 创建工具配置
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

                # 创建内置工具配置
                builtin_config_obj = BuiltinToolConfig(
                    id=tool_config.id,
                    tool_class=tool_info['tool_class'],
                    parameters={},
                    requires_config=tool_info.get('requires_config', False)
                )
                self.db.add(builtin_config_obj)

            except Exception as e:
                logger.error(f"初始化内置工具失败: {tool_key}, {e}")

        self.db.commit()
        logger.info(f"租户 {tenant_id} 内置工具初始化完成")

    def get_tool_statistics(self, tenant_id: uuid.UUID) -> Dict[str, Any]:
        """获取工具统计信息"""
        try:
            # 总数统计
            total_tools = self.tool_repo.count_by_tenant(self.db, tenant_id)

            # 状态统计
            status_counts = self.tool_repo.get_status_statistics(self.db, tenant_id)

            # 类型统计
            type_counts = self.tool_repo.get_type_statistics(self.db, tenant_id)

            # 启用/禁用统计
            enabled_count = self.tool_repo.count_enabled_by_tenant(self.db, tenant_id)
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

    def _get_tool_config(self, tool_id: str, tenant_id: uuid.UUID) -> Optional[ToolConfig]:
        """获取工具配置"""
        return self.tool_repo.find_by_id_and_tenant(self.db, uuid.UUID(tool_id), tenant_id)

    def _get_tool_instance(self, tool_id: str, tenant_id: uuid.UUID) -> Optional[BaseTool]:
        """获取工具实例"""
        if tool_id in self._tool_cache:
            return self._tool_cache[tool_id]

        config = self._get_tool_config(tool_id, tenant_id)
        if not config:
            return None

        try:
            tool = self._create_tool_instance(config)
            if tool:
                self._tool_cache[tool_id] = tool
            return tool
        except Exception as e:
            logger.error(f"创建工具实例失败: {tool_id}, {e}")
            return None

    def _create_tool_instance(self, config: ToolConfig) -> Optional[BaseTool]:
        """创建工具实例"""
        if config.tool_type == ToolType.BUILTIN.value:
            return self._create_builtin_instance(config)
        elif config.tool_type == ToolType.CUSTOM.value:
            return self._create_custom_instance(config)
        elif config.tool_type == ToolType.MCP.value:
            return self._create_mcp_instance(config)
        return None

    def _create_builtin_instance(self, config: ToolConfig) -> Optional[BaseTool]:
        """创建内置工具实例"""
        builtin_config = self.builtin_repo.find_by_tool_id(self.db, config.id)

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

    def _create_custom_instance(self, config: ToolConfig) -> Optional[CustomTool]:
        """创建自定义工具实例"""
        custom_config = self.custom_repo.find_by_tool_id(self.db, config.id)

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

    def _create_mcp_instance(self, config: ToolConfig) -> Optional[MCPTool]:
        """创建MCP工具实例"""
        mcp_config = self.mcp_repo.find_by_tool_id(self.db, config.id)

        if not mcp_config:
            return None

        tool_config = {
            "server_url": mcp_config.server_url,
            "connection_config": mcp_config.connection_config or {},
            "available_tools": mcp_config.available_tools or []
        }

        return MCPTool(str(config.id), tool_config)

    def _config_to_info(self, config: ToolConfig) -> ToolInfo:
        """配置转换为信息对象"""
        config_data = config.config_data or {}
        
        # 对于MCP工具，从MCPToolConfig获取额外信息
        if config.tool_type == ToolType.MCP.value:
            mcp_config = self.mcp_repo.find_by_tool_id(self.db, config.id)
            if mcp_config:
                config_data.update({
                    "last_health_check": int(mcp_config.last_health_check.timestamp() * 1000) if mcp_config.last_health_check else None,
                    "health_status": mcp_config.health_status,
                    "available_tools": mcp_config.available_tools or []
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
            created_at=config.created_at
        )

    def _create_type_config(self, tool_config: ToolConfig, config: Dict[str, Any]):
        """创建类型特定配置"""
        if tool_config.tool_type == ToolType.CUSTOM.value:
            # 从 schema 中解析 base_url
            base_url = config.get("base_url")
            if not base_url and (config.get("schema_content") or config.get("schema_url")):
                try:
                    from app.core.tools.custom.schema_parser import OpenAPISchemaParser
                    parser = OpenAPISchemaParser()
                    
                    if config.get("schema_content"):
                        success, schema, _ = parser.parse_from_content(config["schema_content"], "application/json")
                    else:
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
                schema_content=config.get("schema_content"),
                schema_url=config.get("schema_url")
            )
            self.db.add(custom_config)

        elif tool_config.tool_type == ToolType.MCP.value:
            mcp_config = MCPToolConfig(
                id=tool_config.id,
                server_url=config.get("server_url"),
                connection_config=config.get("connection_config", {}),
                available_tools=config.get("available_tools", [])
            )
            self.db.add(mcp_config)

    def _sync_type_config(self, tool_config: ToolConfig, config: Dict[str, Any], is_enabled: bool):
        """同步到类型特定表"""
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
                        from app.core.tools.custom.schema_parser import OpenAPISchemaParser
                        parser = OpenAPISchemaParser()

                        if config.get("schema_content"):
                            success, schema, _ = parser.parse_from_content(config["schema_content"],
                                                                           "application/json")
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
                custom_config.schema_content = config.get("schema_content")
                custom_config.schema_url = config.get("schema_url")

        elif tool_config.tool_type == ToolType.MCP.value:
            mcp_config = self.db.query(MCPToolConfig).filter(
                MCPToolConfig.id == tool_config.id
            ).first()
            if mcp_config:
                mcp_config.server_url = config.get("server_url")
                mcp_config.connection_config = config.get("connection_config", {})
                mcp_config.available_tools = config.get("available_tools", [])

    @staticmethod
    def _determine_initial_status(tool_info: Dict[str, Any]) -> str:
        """确定工具初始状态"""
        if tool_info.get('requires_config', False):
            return ToolStatus.UNCONFIGURED
        else:
            return ToolStatus.AVAILABLE

    def _update_tool_status(self, tool_config: ToolConfig):
        """更新工具状态逻辑"""
        if tool_config.tool_type == ToolType.BUILTIN.value:
            builtin_config = self.db.query(BuiltinToolConfig).filter(
                BuiltinToolConfig.id == tool_config.id
            ).first()

            if builtin_config:
                if builtin_config.requires_config:
                    # 需要配置的工具
                    if self._is_tool_configured(builtin_config):
                        if tool_config.config_data.get("is_enabled", None):
                            tool_config.status = ToolStatus.AVAILABLE.value
                        else:
                            tool_config.status = ToolStatus.CONFIGURED_DISABLED.value
                    else:
                        tool_config.status = ToolStatus.UNCONFIGURED.value
                else:
                    # 不需要配置的工具
                    tool_config.status = ToolStatus.AVAILABLE.value

        elif tool_config.tool_type == ToolType.CUSTOM.value:
            custom_config = self.db.query(CustomToolConfig).filter(
                CustomToolConfig.id == tool_config.id
            ).first()

            if custom_config and tool_config.name and (custom_config.schema_content or custom_config.schema_url):
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
        """检查工具是否已配置"""
        # 从配置文件获取必需参数
        builtin_config_data = self._load_builtin_config()
        required_params = {}
        for key, value in builtin_config_data.items():
            if builtin_config.tool_class == value["tool_class"]:
                required_params = value.get('parameters', {})
                break

        # 检查所有必需参数是否已配置
        for param_name, param_info in required_params.items():
            if param_info.get('required', False):
                if not builtin_config.parameters.get(param_name):
                    return False
        return True

    def _clear_tool_cache(self, tool_id: str):
        """清除工具缓存"""
        if tool_id in self._tool_cache:
            del self._tool_cache[tool_id]

    def _record_execution_start(
            self,
            execution_id: str,
            tool_id: str,
            parameters: Dict[str, Any],
            user_id: Optional[uuid.UUID],
            workspace_id: Optional[uuid.UUID]
    ):
        """记录执行开始"""
        try:
            execution = ToolExecution(
                execution_id=execution_id,
                tool_config_id=uuid.UUID(tool_id),
                status=ExecutionStatus.RUNNING.value,
                input_data=parameters,
                started_at=datetime.now(),
                user_id=user_id,
                workspace_id=workspace_id
            )
            self.db.add(execution)
            self.db.commit()
        except Exception as e:
            logger.error(f"记录执行开始失败: {execution_id}, {e}")

    def _record_execution_complete(self, execution_id: str, result: ToolResult):
        """记录执行完成"""
        try:
            execution = self.db.query(ToolExecution).filter(
                ToolExecution.execution_id == execution_id
            ).first()

            if execution:
                execution.status = ExecutionStatus.COMPLETED.value if result.success else ExecutionStatus.FAILED.value
                execution.output_data = result.data if result.success else None
                execution.error_message = result.error if not result.success else None
                execution.completed_at = datetime.now()
                execution.execution_time = result.execution_time
                execution.token_usage = result.token_usage
                self.db.commit()
        except Exception as e:
            logger.error(f"记录执行完成失败: {execution_id}, {e}")

    @staticmethod
    def _load_builtin_config() -> Dict[str, Any]:
        """加载内置工具配置"""
        import json
        from pathlib import Path

        config_file = Path(__file__).parent.parent / "core" / "tools" / "configs" / "builtin_tools.json"
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载内置工具配置失败: {e}")
            return {}

    async def _test_mcp_connection(self, config: ToolConfig) -> Dict[str, Any]:
        """测试MCP连接"""
        try:
            mcp_config = self.db.query(MCPToolConfig).filter(
                MCPToolConfig.id == config.id
            ).first()

            if not mcp_config:
                return {"success": False, "message": "MCP配置不存在"}

            client = MCPClient(mcp_config.server_url, mcp_config.connection_config or {})

            if await client.connect():
                try:
                    # tools = await client.list_tools()
                    await client.disconnect()

                    # 更新连接状态
                    mcp_config.last_health_check = datetime.now()
                    mcp_config.health_status = "healthy"
                    mcp_config.error_message = None

                    # 更新工具状态
                    self._update_tool_status(config)
                    self.db.commit()

                    return {
                        "success": True,
                        "message": "MCP连接成功",
                        # "details": {"server_url": mcp_config.server_url, "tools_count": len(tools)}
                        "details": {"server_url": mcp_config.server_url}
                    }
                except Exception as e:
                    await client.disconnect()

                    # 更新错误状态
                    mcp_config.last_health_check = datetime.now()
                    mcp_config.health_status = "error"
                    mcp_config.error_message = str(e)
                    self._update_tool_status(config)
                    self.db.commit()

                    return {"success": False, "message": f"MCP功能测试失败: {str(e)}"}
            else:
                # 更新连接失败状态
                mcp_config.last_health_check = datetime.now()
                mcp_config.health_status = "error"
                mcp_config.error_message = "连接失败"
                self._update_tool_status(config)
                self.db.commit()

                return {"success": False, "message": "MCP连接失败"}

        except Exception as e:
            # 更新异常状态
            mcp_config = self.db.query(MCPToolConfig).filter(
                MCPToolConfig.id == config.id
            ).first()
            if mcp_config:
                mcp_config.last_health_check = datetime.now()
                mcp_config.health_status = "error"
                mcp_config.error_message = str(e)
                self._update_tool_status(config)
                self.db.commit()

            return {"success": False, "message": f"MCP测试异常: {str(e)}"}

    @staticmethod
    async def parse_openapi_schema(schema_data: str = None, schema_url: str = None) -> Dict[str, Any]:
        """解析OpenAPI schema获取接口信息"""
        try:
            from app.core.tools.custom.schema_parser import OpenAPISchemaParser
            
            parser = OpenAPISchemaParser()

            if schema_data:
                success, schema, error = parser.parse_from_content(schema_data, "application/json")
            elif schema_url:
                success, schema, error = await parser.parse_from_url(schema_url)
            else:
                return {"success": False, "message": "schema_data或schema_url必须提供一个"}
            
            if not success:
                return {"success": False, "message": error}
            
            # 提取工具信息
            tool_info = parser.extract_tool_info(schema)
            
            # 获取base_url
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

    async def sync_mcp_tools(self, tool_id: str, tenant_id: uuid.UUID) -> Dict[str, Any]:
        """同步MCP工具列表到数据库"""
        try:
            config = self._get_tool_config(tool_id, tenant_id)
            if not config or config.tool_type != ToolType.MCP.value:
                return {"success": False, "message": "工具不存在或不是MCP工具"}
            
            mcp_config = self.mcp_repo.find_by_tool_id(self.db, config.id)
            if not mcp_config:
                return {"success": False, "message": "MCP配置不存在"}
            
            # 创建MCP客户端
            connection_config = mcp_config.connection_config or {}
            
            client = MCPClient(mcp_config.server_url, connection_config)
            
            if await client.connect():
                try:
                    # 获取工具列表
                    tools = await client.list_tools()
                    tool_names = [tool.get("name") for tool in tools if tool.get("name")]
                    
                    # 更新数据库
                    mcp_config.available_tools = tool_names
                    mcp_config.last_health_check = datetime.now()
                    mcp_config.health_status = "healthy"
                    mcp_config.error_message = None
                    
                    # 更新工具状态
                    config.status = ToolStatus.AVAILABLE.value
                    
                    self.db.commit()
                    
                    await client.disconnect()
                    
                    return {
                        "success": True,
                        "message": "工具列表同步成功",
                        "tools_count": len(tool_names),
                        "tools": tool_names
                    }
                    
                except Exception as e:
                    await client.disconnect()
                    
                    # 更新错误状态
                    mcp_config.last_health_check = datetime.now()
                    mcp_config.health_status = "error"
                    mcp_config.error_message = str(e)
                    config.status = ToolStatus.ERROR.value
                    self.db.commit()
                    
                    return {"success": False, "message": f"获取工具列表失败: {str(e)}"}
            else:
                # 连接失败
                mcp_config.last_health_check = datetime.now()
                mcp_config.health_status = "error"
                mcp_config.error_message = "连接失败"
                config.status = ToolStatus.ERROR.value
                self.db.commit()
                
                return {"success": False, "message": "MCP连接失败"}
                
        except Exception as e:
            logger.error(f"同步MCP工具列表失败: {tool_id}, 错误: {e}")
            return {"success": False, "message": f"同步失败: {str(e)}"}

    async def _test_custom_connection(self, config: ToolConfig) -> Dict[str, Any]:
        """测试自定义工具连接（基础连接测试）"""
        try:
            custom_config = self.db.query(CustomToolConfig).filter(
                CustomToolConfig.id == config.id
            ).first()

            if not custom_config or not custom_config.base_url:
                return {"success": False, "message": "自定义工具配置不完整"}

            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                        custom_config.base_url,
                        timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        return {"success": True, "message": "自定义工具连接成功"}
                    else:
                        return {"success": False, "message": f"连接失败，状态码: {response.status}"}

        except Exception as e:
            return {"success": False, "message": f"自定义工具测试失败: {str(e)}"}

    async def test_custom_tool(
            self, 
            tool_id: str, 
            tenant_id: uuid.UUID, 
            method: str, 
            path: str, 
            parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """测试自定义工具API调用"""
        try:
            config = self._get_tool_config(tool_id, tenant_id)
            if not config or config.tool_type != ToolType.CUSTOM.value:
                return {"success": False, "message": "工具不存在或不是自定义工具"}

            custom_config = self.db.query(CustomToolConfig).filter(
                CustomToolConfig.id == config.id
            ).first()

            if not custom_config or not custom_config.base_url:
                return {"success": False, "message": "自定义工具配置不完整"}

            # 构建完整URL
            url = custom_config.base_url.rstrip('/') + '/' + path.lstrip('/')
            
            # 构建请求头
            headers = {"Content-Type": "application/json"}
            
            # 添加认证头
            if custom_config.auth_type != AuthType.NONE.value:
                auth_config = custom_config.auth_config or {}
                if custom_config.auth_type == AuthType.API_KEY.value:
                    key_name = auth_config.get("key_name", "X-API-Key")
                    api_key = auth_config.get("api_key")
                    if api_key:
                        headers[key_name] = api_key
                elif custom_config.auth_type == AuthType.BEARER_TOKEN.value:
                    token = auth_config.get("token")
                    if token:
                        headers["Authorization"] = f"Bearer {token}"
                elif custom_config.auth_type == AuthType.BASIC_AUTH.value:
                    import base64
                    username = auth_config.get("username", "")
                    password = auth_config.get("password", "")
                    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
                    headers["Authorization"] = f"Basic {credentials}"

            import aiohttp
            async with aiohttp.ClientSession() as session:
                # 根据方法发送请求
                if method.upper() == "GET":
                    async with session.get(
                        url, 
                        params=parameters, 
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=custom_config.timeout or 30)
                    ) as response:
                        result_data = await response.text()
                        return {
                            "success": True,
                            "message": "测试成功",
                            "status_code": response.status,
                            "response_data": result_data[:1000]  # 限制返回数据长度
                        }
                else:
                    async with session.request(
                        method.upper(),
                        url,
                        json=parameters,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=custom_config.timeout or 30)
                    ) as response:
                        result_data = await response.text()
                        return {
                            "success": True,
                            "message": "测试成功",
                            "status_code": response.status,
                            "response_data": result_data[:1000]  # 限制返回数据长度
                        }

        except Exception as e:
            logger.error(f"测试自定义工具API失败: {tool_id}, 错误: {e}")
            return {"success": False, "message": f"测试失败: {str(e)}"}

    async def _test_builtin_connection(self, config: ToolConfig) -> Dict[str, Any]:
        """测试内置工具连接"""
        try:
            # 获取工具实例
            tool_instance = self._get_tool_instance(str(config.id), config.tenant_id)
            if not tool_instance:
                return {"success": False, "message": "无法创建工具实例"}
            
            # 检查工具是否有test_connection方法
            if hasattr(tool_instance, 'test_connection'):
                result = await tool_instance.test_connection()
                return result
            else:
                # 检查是否需要配置
                builtin_config = self.builtin_repo.find_by_tool_id(self.db, config.id)
                if builtin_config and builtin_config.requires_config:
                    # 检查必需参数是否已配置
                    if self._is_tool_configured(builtin_config):
                        return {"success": True, "message": "内置工具已正确配置"}
                    else:
                        return {"success": False, "message": "工具缺少必需配置参数"}
                else:
                    return {"success": True, "message": "内置工具无需连接测试"}
                    
        except Exception as e:
            logger.error(f"测试内置工具失败: {config.id}, 错误: {e}")
            return {"success": False, "message": f"测试失败: {str(e)}"}

"""工具配置管理器 - 管理工具配置的加载和验证"""
import json
from pathlib import Path
from typing import Dict, Any, Optional
from pydantic import BaseModel, ValidationError

from app.core.logging_config import get_business_logger

logger = get_business_logger()


class ToolConfigSchema(BaseModel):
    """工具配置基础Schema"""
    name: str
    description: str
    tool_type: str
    version: str = "1.0.0"
    enabled: bool = True
    parameters: Dict[str, Any] = {}
    tags: list[str] = []
    
    class Config:
        extra = "allow"


class BuiltinToolConfigSchema(ToolConfigSchema):
    """内置工具配置Schema"""
    tool_class: str
    tool_type: str = "builtin"


class CustomToolConfigSchema(ToolConfigSchema):
    """自定义工具配置Schema"""
    schema_url: Optional[str] = None
    schema_content: Optional[Dict[str, Any]] = None
    auth_type: str = "none"
    auth_config: Dict[str, Any] = {}
    base_url: Optional[str] = None
    timeout: int = 30
    tool_type: str = "custom"


class MCPToolConfigSchema(ToolConfigSchema):
    """MCP工具配置Schema"""
    server_url: str
    connection_config: Dict[str, Any] = {}
    available_tools: list[str] = []
    tool_type: str = "mcp"


class ConfigManager:
    """工具配置管理器"""
    
    def __init__(self, config_dir: Optional[str] = None):
        """初始化配置管理器
        
        Args:
            config_dir: 配置文件目录，默认使用系统配置
        """
        self.config_dir = Path(config_dir or self._get_default_config_dir())
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"配置管理器初始化完成，配置目录: {self.config_dir}")
    
    def _get_default_config_dir(self) -> str:
        """获取默认配置目录"""
        # 获取tools目录下的configs子目录
        tools_dir = Path(__file__).parent
        return str(tools_dir / "configs")
    
    def load_builtin_tool_configs(self) -> Dict[str, BuiltinToolConfigSchema]:
        """加载内置工具配置
        
        Returns:
            内置工具配置字典
        """
        configs = {}
        builtin_dir = self.config_dir / "builtin"
        
        if not builtin_dir.exists():
            logger.info("内置工具配置目录不存在，创建默认配置")
            self._create_default_builtin_configs(builtin_dir)
        
        for config_file in builtin_dir.glob("*.json"):
            try:
                config_data = self._load_config_file(config_file)
                config = BuiltinToolConfigSchema(**config_data)
                configs[config.name] = config
                logger.debug(f"加载内置工具配置: {config.name}")
            except Exception as e:
                logger.error(f"加载内置工具配置失败: {config_file}, 错误: {e}")
        
        return configs
    
    def load_builtin_tools_config(self) -> Dict[str, Any]:
        """加载全局内置工具配置（兼容原有接口）
        
        Returns:
            内置工具配置字典
        """
        config_file = self.config_dir / "builtin_tools.json"
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载内置工具配置失败: {e}")
            return {}
    
    def ensure_builtin_tools_initialized(self, tenant_id, db_session, tool_config_model, builtin_tool_config_model, tool_type_enum, tool_status_enum):
        """确保内置工具已初始化到数据库
        
        Args:
            tenant_id: 租户ID
            db_session: 数据库会话
            tool_config_model: ToolConfig模型类
            builtin_tool_config_model: BuiltinToolConfig模型类
            tool_type_enum: ToolType枚举
            tool_status_enum: ToolStatus枚举
        """
        # 检查是否已初始化
        existing_count = db_session.query(tool_config_model).filter(
            tool_config_model.tenant_id == tenant_id,
            tool_config_model.tool_type == tool_type_enum.BUILTIN
        ).count()
        
        if existing_count > 0:
            return  # 已初始化
        
        # 加载全局配置
        builtin_tools = self.load_builtin_tools_config()
        
        # 为租户创建内置工具记录
        for tool_key, tool_info in builtin_tools.items():
            # 设置初始状态
            initial_status = tool_status_enum.ACTIVE.value if not tool_info['requires_config'] else tool_status_enum.INACTIVE.value
            
            tool_config = tool_config_model(
                name=tool_info['name'],
                description=tool_info['description'],
                tool_type=tool_type_enum.BUILTIN,
                tenant_id=tenant_id,
                status=initial_status
            )
            db_session.add(tool_config)
            db_session.flush()
            
            builtin_config = builtin_tool_config_model(
                id=tool_config.id,
                tool_class=tool_info['tool_class'],
                parameters={}
            )
            db_session.add(builtin_config)
        
        db_session.commit()
        logger.info(f"租户 {tenant_id} 的内置工具初始化完成")

    def save_tool_config(self, config: ToolConfigSchema, tool_type: str) -> bool:
        """保存工具配置
        
        Args:
            config: 工具配置
            tool_type: 工具类型
            
        Returns:
            保存是否成功
        """
        try:
            config_dir = self.config_dir / tool_type
            config_dir.mkdir(parents=True, exist_ok=True)
            
            config_file = config_dir / f"{config.name}.json"
            config_data = config.model_dump()
            
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"工具配置保存成功: {config.name} ({tool_type})")
            return True
            
        except Exception as e:
            logger.error(f"工具配置保存失败: {config.name}, 错误: {e}")
            return False
    
    def delete_tool_config(self, tool_name: str, tool_type: str) -> bool:
        """删除工具配置
        
        Args:
            tool_name: 工具名称
            tool_type: 工具类型
            
        Returns:
            删除是否成功
        """
        try:
            config_file = self.config_dir / tool_type / f"{tool_name}.json"
            
            if config_file.exists():
                config_file.unlink()
                logger.info(f"工具配置删除成功: {tool_name} ({tool_type})")
                return True
            else:
                logger.warning(f"工具配置文件不存在: {tool_name} ({tool_type})")
                return False
                
        except Exception as e:
            logger.error(f"工具配置删除失败: {tool_name}, 错误: {e}")
            return False
    
    def validate_config(self, config_data: Dict[str, Any], tool_type: str) -> tuple[bool, Optional[str]]:
        """验证工具配置
        
        Args:
            config_data: 配置数据
            tool_type: 工具类型
            
        Returns:
            (是否有效, 错误信息)
        """
        try:
            schema_map = {
                "builtin": BuiltinToolConfigSchema,
                "custom": CustomToolConfigSchema,
                "mcp": MCPToolConfigSchema
            }
            
            schema_class = schema_map.get(tool_type)
            if not schema_class:
                return False, f"不支持的工具类型: {tool_type}"
            
            # 验证配置
            schema_class(**config_data)
            return True, None
            
        except ValidationError as e:
            error_msg = "; ".join([f"{err['loc'][0]}: {err['msg']}" for err in e.errors()])
            return False, f"配置验证失败: {error_msg}"
        except Exception as e:
            return False, f"配置验证异常: {str(e)}"
    
    def _load_config_file(self, config_file: Path) -> Dict[str, Any]:
        """加载配置文件
        
        Args:
            config_file: 配置文件路径
            
        Returns:
            配置数据字典
        """
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载配置文件失败: {config_file}, 错误: {e}")
            raise
    
    def _create_default_builtin_configs(self, builtin_dir: Path):
        """创建默认内置工具配置
        
        Args:
            builtin_dir: 内置工具配置目录
        """
        builtin_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"内置工具配置目录已创建: {builtin_dir}")
        # 配置文件已经通过其他方式创建，这里只需要确保目录存在
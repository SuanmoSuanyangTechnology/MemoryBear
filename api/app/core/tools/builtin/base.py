"""内置工具基类"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List

from app.models.tool_model import ToolType
from app.core.tools.base import BaseTool
from app.schemas.tool_schema import ToolResult, ToolParameter


class BuiltinTool(BaseTool, ABC):
    """内置工具基类"""
    
    def __init__(self, tool_id: str, config: Dict[str, Any]):
        """初始化内置工具
        
        Args:
            tool_id: 工具ID
            config: 工具配置
        """
        super().__init__(tool_id, config)
        self.parameters_config = config.get("parameters", {})

    @property
    def tool_type(self) -> ToolType:
        """工具类型"""
        return ToolType.BUILTIN
    
    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称 - 子类必须实现"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述 - 子类必须实现"""
        pass
    
    @property
    @abstractmethod
    def parameters(self) -> List[ToolParameter]:
        """工具参数定义 - 子类必须实现"""
        pass
    
    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """执行工具 - 子类必须实现
        
        Args:
            **kwargs: 工具参数
            
        Returns:
            执行结果
        """
        pass
    
    @property
    def is_configured(self) -> bool:
        """检查工具是否已正确配置"""
        required_params = self.get_required_config_parameters()
        for param in required_params:
            if not self.parameters_config.get(param):
                return False
        return True
    
    def get_required_config_parameters(self) -> List[str]:
        """获取必需的配置参数列表
        
        Returns:
            必需配置参数名称列表
        """
        return []
    
    def get_config_parameter(self, name: str, default: Any = None) -> Any:
        """获取配置参数值
        
        Args:
            name: 参数名称
            default: 默认值
            
        Returns:
            参数值
        """
        return self.parameters_config.get(name, default)
    
    def validate_configuration(self) -> tuple[bool, str]:
        """验证工具配置
        
        Returns:
            (是否有效, 错误信息)
        """
        if not self.is_configured:
            required_params = self.get_required_config_parameters()
            missing_params = [p for p in required_params if not self.parameters_config.get(p)]
            return False, f"缺少必需的配置参数: {', '.join(missing_params)}"
        
        return True, ""
    
    async def safe_execute(self, **kwargs) -> ToolResult:
        """安全执行工具（包含配置验证）
        
        Args:
            **kwargs: 工具参数
            
        Returns:
            执行结果
        """
        # 首先验证配置
        is_valid, error_msg = self.validate_configuration()
        if not is_valid:
            return ToolResult.error_result(
                error=f"工具配置无效: {error_msg}",
                error_code="CONFIGURATION_ERROR",
                execution_time=0.0
            )
        
        # 调用父类的安全执行
        return await super().safe_execute(**kwargs)
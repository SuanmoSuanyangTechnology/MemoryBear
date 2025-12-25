"""工具基础接口定义"""
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from app.models.tool_model import ToolType, ToolStatus
from app.schemas.tool_schema import ToolParameter, ParameterType, ToolResult


class BaseTool(ABC):
    """所有工具的基础抽象类"""
    
    def __init__(self, tool_id: str, config: Dict[str, Any]):
        """初始化工具
        
        Args:
            tool_id: 工具ID
            config: 工具配置
        """
        self.tool_id = tool_id
        self.config = config
        self._status = ToolStatus.AVAILABLE
    
    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称"""
        pass
    
    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述"""
        pass
    
    @property
    @abstractmethod
    def tool_type(self) -> ToolType:
        """工具类型"""
        pass
    
    @property
    def version(self) -> str:
        """工具版本"""
        return self.config.get("version", "1.0.0")
    
    @property
    def status(self) -> ToolStatus:
        """工具状态"""
        return self._status
    
    @status.setter
    def status(self, value: ToolStatus):
        """设置工具状态"""
        self._status = value
    
    @property
    @abstractmethod
    def parameters(self) -> List[ToolParameter]:
        """工具参数定义"""
        pass
    
    @property
    def tags(self) -> List[str]:
        """工具标签"""
        return self.config.get("tags", [])
    
    def validate_parameters(self, parameters: Dict[str, Any]) -> Dict[str, str]:
        """验证参数
        
        Args:
            parameters: 输入参数
            
        Returns:
            验证错误字典，空字典表示验证通过
        """
        errors = {}
        param_definitions = {p.name: p for p in self.parameters}
        
        # 检查必需参数
        for param_def in self.parameters:
            if param_def.required and param_def.name not in parameters:
                errors[param_def.name] = f"Required parameter '{param_def.name}' is missing"
        
        # 检查参数类型和约束
        for param_name, param_value in parameters.items():
            if param_name not in param_definitions:
                continue
                
            param_def = param_definitions[param_name]
            
            # 类型检查
            if not self._validate_parameter_type(param_value, param_def):
                errors[param_name] = f"Parameter '{param_name}' has invalid type, expected {param_def.type}"
            
            # 约束检查
            constraint_error = self._validate_parameter_constraints(param_value, param_def)
            if constraint_error:
                errors[param_name] = constraint_error
        
        return errors
    
    def _validate_parameter_type(self, value: Any, param_def: ToolParameter) -> bool:
        """验证参数类型"""
        if value is None:
            return not param_def.required
        
        type_mapping = {
            ParameterType.STRING: str,
            ParameterType.INTEGER: int,
            ParameterType.NUMBER: (int, float),
            ParameterType.BOOLEAN: bool,
            ParameterType.ARRAY: list,
            ParameterType.OBJECT: dict
        }
        
        expected_type = type_mapping.get(param_def.type)
        if expected_type:
            return isinstance(value, expected_type)
        
        return True
    
    def _validate_parameter_constraints(self, value: Any, param_def: ToolParameter) -> Optional[str]:
        """验证参数约束"""
        if value is None:
            return None
        
        # 枚举值检查
        if param_def.enum and value not in param_def.enum:
            return f"Value must be one of {param_def.enum}"
        
        # 数值范围检查
        if param_def.type in [ParameterType.INTEGER, ParameterType.NUMBER]:
            if param_def.minimum is not None and value < param_def.minimum:
                return f"Value must be >= {param_def.minimum}"
            if param_def.maximum is not None and value > param_def.maximum:
                return f"Value must be <= {param_def.maximum}"
        
        # 字符串模式检查
        if param_def.type == ParameterType.STRING and param_def.pattern:
            import re
            if not re.match(param_def.pattern, str(value)):
                return f"Value must match pattern: {param_def.pattern}"
        
        return None
    
    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """执行工具
        
        Args:
            **kwargs: 工具参数
            
        Returns:
            执行结果
        """
        pass
    
    async def safe_execute(self, **kwargs) -> ToolResult:
        """安全执行工具（包含参数验证和异常处理）
        
        Args:
            **kwargs: 工具参数
            
        Returns:
            执行结果
        """
        start_time = time.time()
        
        try:
            # 参数验证
            validation_errors = self.validate_parameters(kwargs)
            if validation_errors:
                execution_time = time.time() - start_time
                error_msg = "; ".join([f"{k}: {v}" for k, v in validation_errors.items()])
                return ToolResult.error_result(
                    error=f"Parameter validation failed: {error_msg}",
                    error_code="VALIDATION_ERROR",
                    execution_time=execution_time
                )
            
            # 执行工具
            result = await self.execute(**kwargs)
            return result
            
        except Exception as e:
            execution_time = time.time() - start_time
            return ToolResult.error_result(
                error=str(e),
                error_code="EXECUTION_ERROR",
                execution_time=execution_time
            )
    
    def to_langchain_tool(self):
        """转换为Langchain工具格式"""
        from .langchain_adapter import LangchainAdapter
        return LangchainAdapter.convert_tool(self)
    
    def __repr__(self):
        return f"<{self.__class__.__name__}(id={self.tool_id}, name={self.name})>"
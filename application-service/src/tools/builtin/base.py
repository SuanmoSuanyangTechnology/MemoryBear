"""Builtin tool base module."""
from abc import ABC, abstractmethod
from typing import Dict, Any, List

from ...models.tool_model import ToolType
from ..base import BaseTool
from ...schemas.tool_schema import ToolParameter, ToolResult


class BuiltinTool(BaseTool, ABC):
    """Base class for builtin tools."""

    def __init__(self, tool_id: str, config: Dict[str, Any]):
        """Initialize the builtin tool

        Args:
            tool_id: Tool ID
            config: Tool configuration
        """
        super().__init__(tool_id, config)
        self.parameters_config = config.get("parameters", {})

    @property
    def tool_type(self) -> ToolType:
        """Tool type."""
        return ToolType.BUILTIN

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name - must be implemented by subclasses."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description - must be implemented by subclasses."""
        pass

    @property
    @abstractmethod
    def parameters(self) -> List[ToolParameter]:
        """Tool parameter definitions - must be implemented by subclasses."""
        pass

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool - must be implemented by subclasses.

        Args:
            **kwargs: Tool parameters

        Returns:
            Execution result
        """
        pass

    @property
    def is_configured(self) -> bool:
        """Check whether the tool is properly configured."""
        required_params = self.get_required_config_parameters()
        for param in required_params:
            if not self.parameters_config.get(param):
                return False
        return True

    def get_required_config_parameters(self) -> List[str]:
        """Get the list of required configuration parameters

        Returns:
            List of required configuration parameter names
        """
        return []

    def get_config_parameter(self, name: str, default: Any = None) -> Any:
        """Get a configuration parameter value

        Args:
            name: Parameter name
            default: Default value

        Returns:
            Parameter value
        """
        return self.parameters_config.get(name, default)

    def validate_configuration(self) -> tuple[bool, str]:
        """Validate the tool configuration

        Returns:
            (is_valid, error_message)
        """
        if not self.is_configured:
            required_params = self.get_required_config_parameters()
            missing_params = [p for p in required_params if not self.parameters_config.get(p)]
            return False, f"缺少必需的配置参数: {', '.join(missing_params)}"

        return True, ""

    async def safe_execute(self, **kwargs) -> ToolResult:
        """Execute the tool with configuration validation.

        Args:
            **kwargs: Tool parameters

        Returns:
            Execution result
        """
        # Validate the configuration first
        is_valid, error_msg = self.validate_configuration()
        if not is_valid:
            return ToolResult.error_result(
                error=f"工具配置无效: {error_msg}",
                error_code="CONFIGURATION_ERROR",
                execution_time=0.0
            )

        # Delegate to the base safe execution
        return await super().safe_execute(**kwargs)

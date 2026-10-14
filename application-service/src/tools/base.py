"""Base tool interface definitions."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from ..models.tool_model import ToolType, ToolStatus
from ..schemas.tool_schema import ToolParameter, ParameterType, ToolResult


class BaseTool(ABC):
    """Base abstract class for all tools."""

    def __init__(self, tool_id: str, config: Dict[str, Any]):
        """Initialize the tool

        Args:
            tool_id: Tool ID
            config: Tool configuration
        """
        self.tool_id = tool_id
        self.config = config
        self._status = ToolStatus.AVAILABLE
        self._runtime_context: Dict[str, Any] = {}

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Tool description."""
        pass

    @property
    @abstractmethod
    def tool_type(self) -> ToolType:
        """Tool type."""
        pass

    @property
    def version(self) -> str:
        """Tool version."""
        return self.config.get("version", "1.0.0")

    @property
    def status(self) -> ToolStatus:
        """Tool status."""
        return self._status

    @status.setter
    def status(self, value: ToolStatus):
        """Set the tool status."""
        self._status = value

    @property
    @abstractmethod
    def parameters(self) -> List[ToolParameter]:
        """Tool parameter definitions."""
        pass

    @property
    def tags(self) -> List[str]:
        """Tool tags."""
        return self.config.get("tags", [])

    def __repr__(self):
        return f"<{self.__class__.__name__}(id={self.tool_id}, name={self.name})>"

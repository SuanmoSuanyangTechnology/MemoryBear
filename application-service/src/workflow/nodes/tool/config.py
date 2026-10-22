from pydantic import Field
from typing import Any

from ...nodes.base_config import BaseNodeConfig


class ToolNodeConfig(BaseNodeConfig):
    """Tool node config."""
    
    tool_id: str = Field(..., description="工具ID")
    tool_parameters: dict[str, Any] = Field(default_factory=dict, description="工具参数映射，支持工作流变量")

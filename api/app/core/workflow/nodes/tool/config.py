from pydantic import Field
from app.core.workflow.nodes.base_config import BaseNodeConfig


class ToolNodeConfig(BaseNodeConfig):
    """工具节点配置"""
    
    tool_id: str = Field(..., description="工具ID")
    tool_parameters: dict[str, str] = Field(default_factory=dict, description="工具参数映射，支持工作流变量")

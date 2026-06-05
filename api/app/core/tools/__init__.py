"""工具管理核心模块"""

from app.core.tools.base import BaseTool, ToolResult, ToolParameter
from app.core.tools.langchain_adapter import LangchainAdapter
from app.core.tools.serialization import serialize_tool_parameter

# 可选导入，避免导入错误
try:
    from .custom.base import CustomTool
except ImportError:
    CustomTool = None

try:
    from .mcp.base import MCPTool
except ImportError:
    MCPTool = None

try:
    from .workflow.base import WorkflowAsTool
except ImportError:
    WorkflowAsTool = None

__all__ = [
    "BaseTool",
    "ToolResult", 
    "ToolParameter",
    "LangchainAdapter",
    "serialize_tool_parameter",
]

# 只有在成功导入时才添加到__all__
if CustomTool:
    __all__.append("CustomTool")

if MCPTool:
    __all__.append("MCPTool")

if WorkflowAsTool:
    __all__.append("WorkflowAsTool")

"""工具管理核心模块"""

from .base import BaseTool, ToolResult, ToolParameter
from .registry import ToolRegistry
from .executor import ToolExecutor
from .langchain_adapter import LangchainAdapter
from .config_manager import ConfigManager
from .chain_manager import ChainManager

# 可选导入，避免导入错误
try:
    from .custom.base import CustomTool
except ImportError:
    CustomTool = None

try:
    from .mcp.base import MCPTool
except ImportError:
    MCPTool = None

__all__ = [
    "BaseTool",
    "ToolResult", 
    "ToolParameter",
    "ToolRegistry",
    "ToolExecutor",
    "LangchainAdapter",
    "ConfigManager",
    "ChainManager"
]

# 只有在成功导入时才添加到__all__
if CustomTool:
    __all__.append("CustomTool")

if MCPTool:
    __all__.append("MCPTool")
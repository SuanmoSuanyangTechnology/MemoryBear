"""MCP 工具模块 - Model Context Protocol 支持"""

# 主要类导出
from .base import MCPTool, MCPToolManager, MCPError
from .client import SimpleMCPClient, MCPConnectionError
from .service_manager import MCPServiceManager

__all__ = [
    # 核心类
    "MCPTool",
    "MCPToolManager", 
    "MCPError",
    
    # 客户端类
    "SimpleMCPClient",
    "MCPConnectionError",
    
    # 服务管理（简化版）
    "MCPServiceManager"
]
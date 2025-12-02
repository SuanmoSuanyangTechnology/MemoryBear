from app.core.config import settings

def get_mcp_server_config():
    """
    Get the MCP server configuration
    """
    mcp_server_config = {
        "data_flow": {
            "url": f"http://{settings.SERVER_IP}:8081/sse",  # 你前面的 FastMCP(weather) 服务端口
            "transport": "sse",
            "timeout": 15000,
            "sse_read_timeout": 15000,
        }
    }
    return mcp_server_config

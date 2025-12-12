import os
from app.core.config import settings

def get_mcp_server_config():
    """
    Get the MCP server configuration.
    
    Uses MCP_SERVER_URL environment variable if set (for Docker),
    otherwise falls back to SERVER_IP and MCP_PORT (for local development).
    """
    # Get MCP port from environment (default: 8081)
    mcp_port = os.getenv("MCP_PORT", "8081")
    
    # In Docker: MCP_SERVER_URL=http://mcp-server:8081
    # In local dev: uses SERVER_IP (127.0.0.1 or localhost)
    mcp_server_url = os.getenv("MCP_SERVER_URL")
    
    if mcp_server_url:
        # Docker environment: use full URL from environment
        base_url = mcp_server_url
    else:
        # Local development: build URL from SERVER_IP and MCP_PORT
        base_url = f"http://{settings.SERVER_IP}:{mcp_port}"
    
    mcp_server_config = {
        "data_flow": {
            "url": f"{base_url}/sse",
            "transport": "sse",
            "timeout": 15000,
            "sse_read_timeout": 15000,
        }
    }
    return mcp_server_config

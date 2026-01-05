#!/usr/bin/env python3
"""API Key认证MCP服务器"""

from fastapi import FastAPI, HTTPException, Depends, Header
from typing import Optional
import uvicorn
from mcp_base import MCPRequest, handle_mcp_request, TOOLS

app = FastAPI(title="API Key MCP Server", version="1.0.0")

# API Key配置
API_KEYS = {"test-api-key", "demo-key-123"}

def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """验证API Key"""
    if x_api_key and x_api_key in API_KEYS:
        return True
    raise HTTPException(status_code=401, detail="Invalid API Key")

@app.get("/")
async def root():
    return {"name": "API Key MCP Server", "version": "1.0.0", "auth_type": "api_key"}

@app.get("/health")
async def health():
    return {"status": "healthy", "tools": len(TOOLS), "auth_type": "api_key"}

@app.post("/mcp")
async def mcp_handler(request: MCPRequest, _: bool = Depends(verify_api_key)):
    return await handle_mcp_request(request, "API Key MCP Server")

if __name__ == "__main__":
    print("启动API Key认证MCP服务器...")
    print("访问 http://localhost:8004 查看服务状态")
    print("MCP端点: http://localhost:8004/mcp")
    print("认证方式: API Key (Header: X-API-Key)")
    print("测试API Keys: test-api-key, demo-key-123")
    uvicorn.run(app, host="0.0.0.0", port=8004)
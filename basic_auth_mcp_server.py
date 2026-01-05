#!/usr/bin/env python3
"""Basic Auth认证MCP服务器"""

from fastapi import FastAPI, HTTPException, Depends, Header
from typing import Optional
import uvicorn
import base64
from mcp_base import MCPRequest, handle_mcp_request, TOOLS

app = FastAPI(title="Basic Auth MCP Server", version="1.0.0")

# Basic Auth配置
BASIC_AUTH_USERS = {"admin": "password", "user": "secret"}

def verify_basic_auth(authorization: Optional[str] = Header(None)):
    """验证Basic Auth"""
    if authorization and authorization.startswith("Basic "):
        try:
            credentials = base64.b64decode(authorization.split(" ")[1]).decode()
            username, password = credentials.split(":", 1)
            if username in BASIC_AUTH_USERS and BASIC_AUTH_USERS[username] == password:
                return True
        except:
            pass
    raise HTTPException(status_code=401, detail="Invalid Basic Auth")

@app.get("/")
async def root():
    return {"name": "Basic Auth MCP Server", "version": "1.0.0", "auth_type": "basic_auth"}

@app.get("/health")
async def health():
    return {"status": "healthy", "tools": len(TOOLS), "auth_type": "basic_auth"}

@app.post("/mcp")
async def mcp_handler(request: MCPRequest, _: bool = Depends(verify_basic_auth)):
    return await handle_mcp_request(request, "Basic Auth MCP Server")

if __name__ == "__main__":
    print("启动Basic Auth认证MCP服务器...")
    print("访问 http://localhost:8006 查看服务状态")
    print("MCP端点: http://localhost:8006/mcp")
    print("认证方式: Basic Auth (Header: Authorization: Basic <base64>)")
    print("测试用户: admin:password, user:secret")
    uvicorn.run(app, host="0.0.0.0", port=8006)
#!/usr/bin/env python3
"""Bearer Token认证MCP服务器"""

from fastapi import FastAPI, HTTPException, Depends, Header
from typing import Optional
import uvicorn
from mcp_base import MCPRequest, handle_mcp_request, TOOLS

app = FastAPI(title="Bearer Token MCP Server", version="1.0.0")

# Bearer Token配置
BEARER_TOKENS = {"bearer-token-123", "demo-bearer-token"}

def verify_bearer_token(authorization: Optional[str] = Header(None)):
    """验证Bearer Token"""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        if token in BEARER_TOKENS:
            return True
    raise HTTPException(status_code=401, detail="Invalid Bearer Token")

@app.get("/")
async def root():
    return {"name": "Bearer Token MCP Server", "version": "1.0.0", "auth_type": "bearer_token"}

@app.get("/health")
async def health():
    return {"status": "healthy", "tools": len(TOOLS), "auth_type": "bearer_token"}

@app.post("/mcp")
async def mcp_handler(request: MCPRequest, _: bool = Depends(verify_bearer_token)):
    return await handle_mcp_request(request, "Bearer Token MCP Server")

if __name__ == "__main__":
    print("启动Bearer Token认证MCP服务器...")
    print("访问 http://localhost:8005 查看服务状态")
    print("MCP端点: http://localhost:8005/mcp")
    print("认证方式: Bearer Token (Header: Authorization: Bearer <token>)")
    print("测试Bearer Tokens: bearer-token-123, demo-bearer-token")
    uvicorn.run(app, host="0.0.0.0", port=8005)
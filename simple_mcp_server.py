#!/usr/bin/env python3
"""简化的MCP服务器 - 用于测试MCP工具集成"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List
import uvicorn

app = FastAPI(title="Simple MCP Server", version="1.0.0")

class MCPRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str
    method: str
    params: Dict[str, Any] = {}

class MCPResponse(BaseModel):
    jsonrpc: str = "2.0"
    id: str
    result: Any = None
    error: Dict[str, Any] = None

# 可用工具定义
TOOLS = [
    {
        "name": "calculator",
        "description": "简单计算器",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "数学表达式"}
            },
            "required": ["expression"]
        }
    },
    {
        "name": "echo",
        "description": "回显工具",
        "inputSchema": {
            "type": "object", 
            "properties": {
                "message": {"type": "string", "description": "要回显的消息"}
            },
            "required": ["message"]
        }
    }
]

@app.get("/")
async def root():
    return {"name": "Simple MCP Server", "version": "1.0.0"}

@app.get("/health")
async def health():
    return {"status": "healthy", "tools": len(TOOLS)}

@app.post("/mcp")
async def mcp_handler(request: MCPRequest):
    """处理MCP请求"""
    try:
        if request.method == "initialize":
            return MCPResponse(
                id=request.id,
                result={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "Simple MCP Server", "version": "1.0.0"}
                }
            )
        
        elif request.method == "tools/list":
            return MCPResponse(
                id=request.id,
                result={"tools": TOOLS}
            )
        
        elif request.method == "tools/call":
            tool_name = request.params.get("name")
            arguments = request.params.get("arguments", {})
            
            if tool_name == "calculator":
                try:
                    expression = arguments.get("expression", "")
                    result = eval(expression)  # 注意：生产环境不要用eval
                    return MCPResponse(
                        id=request.id,
                        result={"content": [{"type": "text", "text": f"结果: {result}"}]}
                    )
                except Exception as e:
                    return MCPResponse(
                        id=request.id,
                        error={"code": -1, "message": f"计算错误: {str(e)}"}
                    )
            
            elif tool_name == "echo":
                message = arguments.get("message", "")
                return MCPResponse(
                    id=request.id,
                    result={"content": [{"type": "text", "text": f"Echo: {message}"}]}
                )
            
            else:
                return MCPResponse(
                    id=request.id,
                    error={"code": -1, "message": f"未知工具: {tool_name}"}
                )
        
        elif request.method == "ping":
            return MCPResponse(
                id=request.id,
                result={"status": "pong"}
            )
        
        else:
            return MCPResponse(
                id=request.id,
                error={"code": -1, "message": f"未知方法: {request.method}"}
            )
    
    except Exception as e:
        return MCPResponse(
            id=request.id,
            error={"code": -1, "message": str(e)}
        )

if __name__ == "__main__":
    print("启动简化MCP服务器...")
    print("访问 http://localhost:8002 查看服务状态")
    print("MCP端点: http://localhost:8002/mcp")
    uvicorn.run(app, host="0.0.0.0", port=8002)
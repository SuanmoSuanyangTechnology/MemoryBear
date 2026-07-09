"""Callback-based tool wrapper for sandbox execution"""
import asyncio
from typing import Any, Optional, Type
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class CallbackToolInput(BaseModel):
    query: str = Field(description="The input for the tool")


class CallbackTool(BaseTool):
    name: str = "callback_tool"
    description: str = "A tool that executes via callback"
    args_schema: Type[BaseModel] = CallbackToolInput
    tool_type: str = "builtin"
    tool_id: Optional[str] = None
    callback_client: Any = None

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str = "", **kwargs) -> str:
        return asyncio.run(self._arun(query=query, **kwargs))

    async def _arun(self, query: str = "", **kwargs) -> str:
        if not self.callback_client:
            return "Error: No callback client"
        result = await self.callback_client.execute_tool(
            tool_name=self.name, tool_type=self.tool_type,
            tool_input={"query": query, **kwargs}, tool_id=self.tool_id,
        )
        if "error" in result:
            return f"Error: {result['error']}"
        return result.get("output", str(result))

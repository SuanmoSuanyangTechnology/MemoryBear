"""
Sandbox Tool Wrappers

将 sandbox 外的工具能力通过 Callback API 暴露为 LangChain BaseTool。
Agent 在 sandbox 内调用工具时，实际通过 HTTP 回调主 API 执行。
"""
import asyncio
import logging
from typing import Any, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CallbackToolInput(BaseModel):
    """Generic tool input schema"""
    query: str = Field(description="The input query or parameters for the tool")


class CallbackTool(BaseTool):
    """A LangChain tool that delegates execution to the API via callback

    This wraps remote tools that need DB access, external APIs, or
    other resources not available inside the sandbox.
    """
    name: str = "callback_tool"
    description: str = "A tool that executes via callback to the main API"
    args_schema: Type[BaseModel] = CallbackToolInput

    # Callback metadata
    tool_type: str = "builtin"
    tool_id: Optional[str] = None
    callback_client: Any = None  # CallbackClient instance

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str = "", **kwargs) -> str:
        """Sync execution (uses asyncio.run for compatibility)"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context, create a task
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(
                        asyncio.run, self._arun(query=query, **kwargs)
                    ).result()
                return result
            else:
                return asyncio.run(self._arun(query=query, **kwargs))
        except Exception as e:
            return f"Tool execution error: {str(e)}"

    async def _arun(self, query: str = "", **kwargs) -> str:
        """Async execution via callback"""
        if not self.callback_client:
            return "Error: Callback client not configured"

        # Merge query and kwargs into tool_input
        tool_input = {"query": query, **kwargs}

        result = await self.callback_client.execute_tool(
            tool_name=self.name,
            tool_type=self.tool_type,
            tool_input=tool_input,
            tool_id=self.tool_id,
        )

        if "error" in result:
            return f"Tool error: {result['error']}"

        return result.get("output", str(result))


class KnowledgeRetrievalTool(BaseTool):
    """知识库检索工具 - 通过回调 API 执行"""
    name: str = "knowledge_retrieval"
    description: str = "Search the knowledge base for relevant information"

    knowledge_base_ids: list[str] = []
    callback_client: Any = None
    top_k: int = 5

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str) -> str:
        return asyncio.run(self._arun(query))

    async def _arun(self, query: str) -> str:
        if not self.callback_client:
            return "Error: Callback client not configured"

        results = await self.callback_client.retrieve_knowledge(
            query=query,
            knowledge_base_ids=self.knowledge_base_ids,
            top_k=self.top_k,
        )

        if not results:
            return "No relevant information found."

        # Format results
        formatted = []
        for i, r in enumerate(results, 1):
            content = r.get("content", "")
            source = r.get("source", "unknown")
            score = r.get("score", 0)
            formatted.append(f"[{i}] (score: {score:.2f}, source: {source})\n{content}")

        return "\n\n".join(formatted)


class MemoryReadTool(BaseTool):
    """长期记忆读取工具 - 通过回调 API 执行"""
    name: str = "memory_read"
    description: str = "Read user's long-term memory for relevant context"

    callback_client: Any = None
    memory_type: str = "long_term"

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str) -> str:
        return asyncio.run(self._arun(query))

    async def _arun(self, query: str) -> str:
        if not self.callback_client:
            return "Error: Callback client not configured"

        memories = await self.callback_client.read_memory(
            query=query,
            memory_type=self.memory_type,
        )

        if not memories:
            return "No relevant memories found."

        formatted = []
        for m in memories:
            content = m.get("content", "")
            timestamp = m.get("timestamp", "")
            formatted.append(f"[{timestamp}] {content}")

        return "\n".join(formatted)


class MemoryWriteTool(BaseTool):
    """长期记忆写入工具 - 通过回调 API 执行"""
    name: str = "memory_write"
    description: str = "Write important information to user's long-term memory"

    callback_client: Any = None
    memory_type: str = "long_term"

    class Config:
        arbitrary_types_allowed = True

    def _run(self, content: str) -> str:
        return asyncio.run(self._arun(content))

    async def _arun(self, content: str) -> str:
        if not self.callback_client:
            return "Error: Callback client not configured"

        success = await self.callback_client.write_memory(
            content=content,
            memory_type=self.memory_type,
        )

        return "Memory saved successfully." if success else "Failed to save memory."

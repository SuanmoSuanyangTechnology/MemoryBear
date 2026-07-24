"""Callback-based tool wrappers for sandbox execution"""
import asyncio
import json
import logging
from typing import Any, Optional, Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, create_model

logger = logging.getLogger(__name__)


def _build_args_schema(name: str, parameters: dict | None) -> Type[BaseModel]:
    """Dynamically create a Pydantic model from JSON Schema parameters."""
    if not parameters or not isinstance(parameters, dict):
        return type(f"{name}_Input", (BaseModel,), {
            "query": Field(default="", description="The input for the tool"),
            "__annotations__": {"query": str},
        })

    properties = parameters.get("properties", {})
    required = set(parameters.get("required", []))

    if not properties:
        return type(f"{name}_Input", (BaseModel,), {
            "query": Field(default="", description="The input for the tool"),
            "__annotations__": {"query": str},
        })

    fields: dict[str, tuple[Type, Any]] = {}
    for prop_name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        prop_type = prop_schema.get("type", "string")
        description = prop_schema.get("description", "")
        default = prop_schema.get("default")

        py_type: Type = str
        if prop_type == "integer":
            py_type = int
        elif prop_type == "number":
            py_type = float
        elif prop_type == "boolean":
            py_type = bool
        elif prop_type == "array":
            py_type = list
        elif prop_type == "object":
            py_type = dict

        if prop_name in required:
            fields[prop_name] = (py_type, Field(description=description))
        else:
            fields[prop_name] = (py_type, Field(default=default, description=description))

    return create_model(f"{name}_Input", **fields)


class CallbackTool(BaseTool):
    name: str = "callback_tool"
    description: str = "A tool that executes via callback"
    args_schema: Type[BaseModel] = type("CallbackToolInput", (BaseModel,), {
        "query": Field(default="", description="The input for the tool"),
        "__annotations__": {"query": str},
    })
    tool_type: str = "builtin"
    tool_id: Optional[str] = None
    callback_client: Any = None
    tool_config: dict = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str = "", **kwargs) -> str:
        return asyncio.run(self._arun(query=query, **kwargs))

    async def _arun(self, query: str = "", **kwargs) -> str:
        if not self.callback_client:
            return "Error: No callback client"
        tool_input = {"query": query, **kwargs}
        operation = self.tool_config.get("operation") if self.tool_config else None
        if operation:
            tool_input["operation"] = operation
        result = await self.callback_client.execute_tool(
            tool_name=self.name, tool_type=self.tool_type,
            tool_input=tool_input, tool_id=self.tool_id,
        )
        if "error" in result:
            return f"Error: {result['error']}"
        return result.get("output", str(result))


class KnowledgeRetrievalTool(BaseTool):
    """Tool that retrieves from knowledge bases via callback to API."""
    name: str = "knowledge_retrieval"
    description: str = "Search knowledge bases for relevant information"
    args_schema: Type[BaseModel] = type("KBInput", (BaseModel,), {
        "query": Field(default="", description="Search query"),
        "__annotations__": {"query": str},
    })
    kb_ids: list = Field(default_factory=list)
    top_k: int = 5
    score_threshold: float = 0.5
    callback_client: Any = None
    tool_config: dict = Field(default_factory=dict)
    _collected_citations: list = []

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str = "", **kwargs) -> str:
        return asyncio.run(self._arun(query=query, **kwargs))

    async def _arun(self, query: str = "", **kwargs) -> str:
        if not self.callback_client:
            return "Error: No callback client"
        result = await self.callback_client.retrieve_knowledge(
            query=query,
            kb_ids=self.kb_ids,
            top_k=self.top_k,
            score_threshold=self.score_threshold,
        )
        if result.get("error"):
            return f"Knowledge retrieval error: {result['error']}"

        citations = result.get("citations", [])
        self._collected_citations.extend(citations)

        results = result.get("results", [])
        if not results:
            return "No relevant information found in knowledge bases."

        formatted = []
        for i, r in enumerate(results, 1):
            content = r.get("content", "")
            source = r.get("source", "")
            score = r.get("score", 0)
            formatted.append(f"[{i}] (score: {score:.2f}, source: {source})\n{content}")
        return "\n\n".join(formatted)

    def get_citations(self) -> list:
        return list(self._collected_citations)


class MemoryReadTool(BaseTool):
    """Tool that reads user memories via callback to API."""
    name: str = "memory_read"
    description: str = "Read user's long-term memories"
    args_schema: Type[BaseModel] = type("MemoryReadInput", (BaseModel,), {
        "query": Field(default="", description="What to search for in memories"),
        "__annotations__": {"query": str},
    })
    memory_type: str = "long_term"
    config_id: Optional[str] = None
    callback_client: Any = None
    tool_config: dict = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str = "", **kwargs) -> str:
        return asyncio.run(self._arun(query=query, **kwargs))

    async def _arun(self, query: str = "", **kwargs) -> str:
        if not self.callback_client:
            return "Error: No callback client"
        result = await self.callback_client.read_memory(
            query=query,
            memory_type=self.memory_type,
            config_id=self.config_id,
        )
        if result.get("error"):
            return f"Memory read error: {result['error']}"

        memories = result.get("memories", [])
        if not memories:
            return "No relevant memories found."

        formatted = []
        for i, m in enumerate(memories, 1):
            content = m.get("content", "")
            count = m.get("count", 0)
            formatted.append(f"[{i}] (count: {count})\n{content}")
        return "\n\n".join(formatted)


class MemoryWriteTool(BaseTool):
    """Tool that writes user memories via callback to API."""
    name: str = "memory_write"
    description: str = "Save information to user's long-term memory"
    args_schema: Type[BaseModel] = type("MemoryWriteInput", (BaseModel,), {
        "content": Field(default="", description="Content to remember"),
        "__annotations__": {"content": str},
    })
    memory_type: str = "long_term"
    config_id: Optional[str] = None
    callback_client: Any = None
    tool_config: dict = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True

    def _run(self, content: str = "", **kwargs) -> str:
        return asyncio.run(self._arun(content=content, **kwargs))

    async def _arun(self, content: str = "", **kwargs) -> str:
        if not self.callback_client:
            return "Error: No callback client"
        result = await self.callback_client.write_memory(
            content=content,
            memory_type=self.memory_type,
            config_id=self.config_id,
        )
        if result.get("error"):
            return f"Memory write error: {result['error']}"
        status = result.get("status", "unknown")
        return f"Memory saved successfully (status: {status})."


class WebSearchTool(BaseTool):
    """Local web search tool using DuckDuckGo."""
    name: str = "web_search"
    description: str = "Search the web for current information"
    args_schema: Type[BaseModel] = type("WebSearchInput", (BaseModel,), {
        "query": Field(default="", description="Search query"),
        "__annotations__": {"query": str},
    })
    max_results: int = 5
    tool_config: dict = Field(default_factory=dict)

    class Config:
        arbitrary_types_allowed = True

    def _run(self, query: str = "", **kwargs) -> str:
        return asyncio.run(self._arun(query=query, **kwargs))

    async def _arun(self, query: str = "", **kwargs) -> str:
        try:
            from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=self.max_results):
                    results.append(f"- {r.get('title', '')}: {r.get('body', '')}\n  {r.get('href', '')}")
            if not results:
                return f"No results found for: {query}"
            return "\n\n".join(results)
        except ImportError:
            logger.warning("duckduckgo_search not installed, trying fallback")
            return await self._fallback_search(query)

    async def _fallback_search(self, query: str) -> str:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.duckduckgo.com/",
                    params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                )
                data = resp.json()
                abstract = data.get("AbstractText", "")
                results = data.get("RelatedTopics", [])
                lines = []
                if abstract:
                    lines.append(f"Abstract: {abstract}")
                for r in results[:self.max_results]:
                    if isinstance(r, dict):
                        text = r.get("Text", "")
                        url = r.get("FirstURL", "")
                        if text:
                            lines.append(f"- {text}\n  {url}")
                return "\n\n".join(lines) if lines else f"No results found for: {query}"
        except Exception as e:
            return f"Web search failed: {e}"

"""
Tool Loader - Instantiate sandbox tools from configuration

Takes serialized tool configs from the API and creates LangChain BaseTool instances.
Tools that need DB/external access → CallbackTool (proxy to API)
Tools that can run locally → Direct implementation
"""
import logging
from typing import Any

from langchain_core.tools import BaseTool

from .base import CallbackTool, KnowledgeRetrievalTool, MemoryReadTool, MemoryWriteTool

logger = logging.getLogger(__name__)


def load_tools(
    tool_configs: list[dict],
    callback_client: Any,
) -> list[BaseTool]:
    """Load tools from configuration

    Args:
        tool_configs: List of tool configuration dicts from the API
            Each dict has:
              - name: str
              - description: str
              - type: str (builtin, custom, mcp, knowledge, memory_read, memory_write)
              - tool_id: Optional[str]
              - parameters: Optional schema dict
              - config: Optional extra config
        callback_client: CallbackClient instance for remote tool execution

    Returns:
        List of LangChain BaseTool instances ready for Agent use
    """
    tools: list[BaseTool] = []

    for config in tool_configs:
        tool_type = config.get("type", "builtin")
        tool_name = config.get("name", "unknown")

        try:
            if tool_type == "knowledge_retrieval":
                tool = KnowledgeRetrievalTool(
                    name=config.get("name", "knowledge_retrieval"),
                    description=config.get("description", "Search knowledge base"),
                    knowledge_base_ids=config.get("config", {}).get("knowledge_base_ids", []),
                    callback_client=callback_client,
                    top_k=config.get("config", {}).get("top_k", 5),
                )
                tools.append(tool)

            elif tool_type == "memory_read":
                tool = MemoryReadTool(
                    name=config.get("name", "memory_read"),
                    description=config.get("description", "Read user memory"),
                    callback_client=callback_client,
                    memory_type=config.get("config", {}).get("memory_type", "long_term"),
                )
                tools.append(tool)

            elif tool_type == "memory_write":
                tool = MemoryWriteTool(
                    name=config.get("name", "memory_write"),
                    description=config.get("description", "Write to user memory"),
                    callback_client=callback_client,
                    memory_type=config.get("config", {}).get("memory_type", "long_term"),
                )
                tools.append(tool)

            elif tool_type == "web_search":
                # Web search can run locally inside sandbox (has network access)
                tool = _create_web_search_tool(config)
                if tool:
                    tools.append(tool)

            else:
                # All other tools → callback to API
                tool = CallbackTool(
                    name=tool_name,
                    description=config.get("description", f"Execute {tool_name}"),
                    tool_type=tool_type,
                    tool_id=config.get("tool_id"),
                    callback_client=callback_client,
                )
                tools.append(tool)

            logger.info(f"Loaded tool: {tool_name} (type={tool_type})")

        except Exception as e:
            logger.error(f"Failed to load tool {tool_name}: {e}")
            continue

    logger.info(f"Total tools loaded: {len(tools)}")
    return tools


def _create_web_search_tool(config: dict) -> BaseTool | None:
    """Create web search tool (runs locally in sandbox)"""
    try:
        from langchain_community.tools import DuckDuckGoSearchRun
        return DuckDuckGoSearchRun(
            name=config.get("name", "web_search"),
            description=config.get("description", "Search the web for current information"),
        )
    except ImportError:
        logger.warning("DuckDuckGoSearchRun not available, using callback for web search")
        return None

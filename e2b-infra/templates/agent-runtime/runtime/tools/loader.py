"""Tool loader - creates LangChain tools from config"""
import logging
from langchain_core.tools import BaseTool
from runtime.tools.base import (
    CallbackTool,
    KnowledgeRetrievalTool,
    MemoryReadTool,
    MemoryWriteTool,
    WebSearchTool,
    _build_args_schema,
)

logger = logging.getLogger(__name__)

# Tool types that get their own specialized class
_SPECIALIZED_TOOLS = {
    "knowledge_retrieval",
    "memory_read",
    "memory_write",
    "web_search",
    "skill",
}


def load_tools(tool_configs: list, callback_client) -> list[BaseTool]:
    tools = []
    for config in tool_configs:
        tool_name = config.get("name", "unknown")
        tool_type = config.get("type", "builtin")
        tool_params = config.get("config", {}).get("parameters")

        try:
            if tool_type == "knowledge_retrieval":
                tool = _create_knowledge_retrieval_tool(config, callback_client)
            elif tool_type == "memory_read":
                tool = _create_memory_read_tool(config, callback_client)
            elif tool_type == "memory_write":
                tool = _create_memory_write_tool(config, callback_client)
            elif tool_type == "web_search":
                tool = _create_web_search_tool(config)
            elif tool_type == "skill":
                tool = _create_skill_tool(config, callback_client)
            else:
                # If a specific operation is pre-selected, filter it from the
                # args_schema so the LLM doesn't need to choose one. The
                # operation is auto-injected by CallbackTool._arun at call time.
                operation = config.get("operation")
                if operation and tool_params and isinstance(tool_params, dict):
                    props = tool_params.get("properties")
                    if isinstance(props, dict) and "operation" in props:
                        tool_params = {**tool_params}
                        tool_params["properties"] = {k: v for k, v in props.items() if k != "operation"}
                        req = tool_params.get("required")
                        if isinstance(req, list) and "operation" in req:
                            tool_params["required"] = [r for r in req if r != "operation"]

                args_schema = _build_args_schema(tool_name, tool_params)
                tool = CallbackTool(
                    name=tool_name,
                    description=config.get("description", f"Execute {tool_name}"),
                    args_schema=args_schema,
                    tool_type=tool_type,
                    tool_id=config.get("tool_id"),
                    callback_client=callback_client,
                    tool_config=config,
                )
            tools.append(tool)
        except Exception as e:
            logger.error(f"Failed to load tool {tool_name}: {e}")
    logger.info(f"Loaded {len(tools)} tools")
    return tools


def _create_knowledge_retrieval_tool(config: dict, callback_client) -> KnowledgeRetrievalTool:
    kb_config = config.get("config", {})
    kb_ids = kb_config.get("kb_ids", [])
    if not kb_ids:
        kb_ids = config.get("kb_ids", [])
    return KnowledgeRetrievalTool(
        name=config.get("name", "knowledge_retrieval"),
        description=config.get("description", "Search knowledge bases for relevant information"),
        kb_ids=kb_ids,
        top_k=kb_config.get("top_k", config.get("top_k", 5)),
        score_threshold=kb_config.get("score_threshold", config.get("score_threshold", 0.5)),
        callback_client=callback_client,
        tool_config=config,
    )


def _create_memory_read_tool(config: dict, callback_client) -> MemoryReadTool:
    mem_config = config.get("config", {})
    return MemoryReadTool(
        name=config.get("name", "memory_read"),
        description=config.get("description", "Read user's long-term memories"),
        memory_type=mem_config.get("memory_type", config.get("memory_type", "long_term")),
        config_id=mem_config.get("config_id"),
        callback_client=callback_client,
        tool_config=config,
    )


def _create_memory_write_tool(config: dict, callback_client) -> MemoryWriteTool:
    mem_config = config.get("config", {})
    return MemoryWriteTool(
        name=config.get("name", "memory_write"),
        description=config.get("description", "Save information to user's long-term memory"),
        memory_type=mem_config.get("memory_type", config.get("memory_type", "long_term")),
        config_id=mem_config.get("config_id"),
        callback_client=callback_client,
        tool_config=config,
    )


def _create_web_search_tool(config: dict) -> WebSearchTool:
    search_config = config.get("config", {})
    return WebSearchTool(
        name=config.get("name", "web_search"),
        description=config.get("description", "Search the web for current information"),
        max_results=search_config.get("max_results", config.get("max_results", 5)),
        tool_config=config,
    )


def _create_skill_tool(config: dict, callback_client) -> CallbackTool:
    skill_config = config.get("config", {})
    tool_params = skill_config.get("parameters")
    tool_name = config.get("name", "skill")
    args_schema = _build_args_schema(tool_name, tool_params)
    return CallbackTool(
        name=tool_name,
        description=config.get("description", f"Execute skill: {tool_name}"),
        args_schema=args_schema,
        tool_type="skill",
        tool_id=config.get("tool_id"),
        callback_client=callback_client,
        tool_config=config,
    )

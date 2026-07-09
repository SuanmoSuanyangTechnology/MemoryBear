"""Tool loader - creates LangChain tools from config"""
import logging
from langchain_core.tools import BaseTool
from runtime.tools.base import CallbackTool

logger = logging.getLogger(__name__)


def load_tools(tool_configs: list, callback_client) -> list[BaseTool]:
    tools = []
    for config in tool_configs:
        tool_name = config.get("name", "unknown")
        tool_type = config.get("type", "builtin")
        try:
            tool = CallbackTool(
                name=tool_name,
                description=config.get("description", f"Execute {tool_name}"),
                tool_type=tool_type,
                tool_id=config.get("tool_id"),
                callback_client=callback_client,
            )
            tools.append(tool)
        except Exception as e:
            logger.error(f"Failed to load tool {tool_name}: {e}")
    logger.info(f"Loaded {len(tools)} tools")
    return tools

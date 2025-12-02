import asyncio
import json
from contextlib import asynccontextmanager
from langgraph.constants import START, END
from langgraph.graph import add_messages, StateGraph

from langgraph.prebuilt import ToolNode
from app.core.memory.agent.utils.llm_tools import WriteState
import warnings
import sys
from langchain_core.messages import AIMessage
from app.core.logging_config import get_agent_logger

warnings.filterwarnings("ignore", category=RuntimeWarning)

logger = get_agent_logger(__name__)

if sys.platform.startswith("win"):
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
@asynccontextmanager
async def make_write_graph(user_id, tools, apply_id, group_id, config_id=None):
    logger.info("加载 MCP 工具: %s", [t.name for t in tools])
    if config_id:
        logger.info(f"使用配置 ID: {config_id}")

    data_type_tool = next((t for t in tools if t.name == "Data_type_differentiation"), None)
    data_write_tool = next((t for t in tools if t.name == "Data_write"), None)

    if not data_type_tool or not data_write_tool:
        logger.error('不存在数据存储工具', exc_info=True)
        raise ValueError('不存在数据存储工具')
    # ToolNode
    write_node = ToolNode([data_write_tool])


    async def call_model(state):
        messages = state["messages"]
        last_message = messages[-1]

        result = await data_type_tool.ainvoke({
            "context": last_message[1] if isinstance(last_message, tuple) else last_message.content
        })
        result=json.loads( result)

        # 调用 Data_write，传递 config_id
        write_params = {
            "content": result["context"],
            "apply_id": apply_id,
            "group_id": group_id,
            "user_id": user_id
        }
        
        # 如果提供了 config_id，添加到参数中
        if config_id:
            write_params["config_id"] = config_id
            logger.debug(f"传递 config_id 到 Data_write: {config_id}")
        
        write_result = await data_write_tool.ainvoke(write_params)

        if isinstance(write_result, dict):
            content = write_result.get("data", str(write_result))
        else:
            content = str(write_result)
        logger.info("写入内容: %s", content)
        return {"messages": [AIMessage(content=content)]}

    workflow = StateGraph(WriteState)
    workflow.add_node("content_input", call_model)
    workflow.add_node("save_neo4j", write_node)
    workflow.add_edge(START, "content_input")
    workflow.add_edge("content_input", "save_neo4j")
    workflow.add_edge("save_neo4j", END)

    graph = workflow.compile()


    yield graph

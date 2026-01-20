import asyncio
import json
import sys
import warnings
from contextlib import asynccontextmanager

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.utils.llm_tools import WriteState
from app.schemas.memory_config_schema import MemoryConfig
from langchain_core.messages import AIMessage
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

warnings.filterwarnings("ignore", category=RuntimeWarning)

logger = get_agent_logger(__name__)

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@asynccontextmanager
async def make_write_graph(user_id, tools, apply_id, group_id, memory_config: MemoryConfig):
    """
    Create a write graph workflow for memory operations.
    
    Args:
        user_id: User identifier
        tools: MCP tools loaded from session
        apply_id: Application identifier
        group_id: Group identifier
        memory_config: MemoryConfig object containing all configuration
    """
    logger.info("Loading MCP tools: %s", [t.name for t in tools])
    logger.info(f"Using memory_config: {memory_config.config_name} (id={memory_config.config_id})")

    data_write_tool = next((t for t in tools if t.name == "Data_write"), None)

    if not data_write_tool:
        logger.error("Data_write tool not found", exc_info=True)
        raise ValueError("Data_write tool not found")

    write_node = ToolNode([data_write_tool])

    async def call_model(state):
        messages = state["messages"]
        
        if not isinstance(messages, list) or len(messages) == 0:
            raise ValueError(f"Invalid messages format: expected non-empty list, got {type(messages)}")
        
        from langchain_core.messages import BaseMessage, HumanMessage, AIMessage as LangChainAIMessage
        
        if isinstance(messages[0], BaseMessage):
            # LangChain messages: convert to structured dict
            logger.info(f"Converting LangChain messages to structured format: {len(messages)} messages")
            
            structured_messages = []
            for msg in messages:
                if isinstance(msg, HumanMessage):
                    role = "user"
                elif isinstance(msg, LangChainAIMessage):
                    role = "assistant"
                else:
                    logger.warning(f"Skipping unsupported message type: {type(msg)}")
                    continue
                
                structured_messages.append({
                    "role": role,
                    "content": msg.content
                })
            
        elif isinstance(messages[0], dict) and 'role' in messages[0] and 'content' in messages[0]:
            # Already structured: use directly
            logger.info(f"Processing structured messages: {len(messages)} messages")
            structured_messages = messages
            
        else:
            raise ValueError(f"Unsupported message format: {type(messages[0])}. Expected LangChain BaseMessage or dict with 'role' and 'content' keys.")
        
        write_params = {
            "messages": structured_messages,
            "apply_id": apply_id,
            "group_id": group_id,
            "user_id": user_id,
            "memory_config": memory_config,
        }
        
        logger.debug(f"Passing memory_config to Data_write: {memory_config.config_id}")

        write_result = await data_write_tool.ainvoke(write_params)

        if isinstance(write_result, dict):
            result_content = write_result.get("data", str(write_result))
        else:
            result_content = str(write_result)
        logger.info("Write content: %s", result_content)
        return {"messages": [AIMessage(content=result_content)]}

    workflow = StateGraph(WriteState)
    workflow.add_node("content_input", call_model)
    workflow.add_node("save_neo4j", write_node)
    workflow.add_edge(START, "content_input")
    workflow.add_edge("content_input", "save_neo4j")
    workflow.add_edge("save_neo4j", END)

    graph = workflow.compile()


    yield graph


import asyncio
import sys
import warnings
from contextlib import asynccontextmanager
from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.utils.llm_tools import WriteState
from app.core.memory.agent.langgraph_graph.nodes.write_nodes import write_node

warnings.filterwarnings("ignore", category=RuntimeWarning)
logger = get_agent_logger(__name__)

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
@asynccontextmanager
async def make_write_graph():
    """
    Create a write graph workflow for memory operations.

    Args:
        user_id: User identifier
        tools: MCP tools loaded from session
        apply_id: Application identifier
        end_user_id: Group identifier
        memory_config: MemoryConfig object containing all configuration
    """
    workflow = StateGraph(WriteState)
    workflow.add_node("save_neo4j", write_node)
    workflow.add_edge(START, "save_neo4j")
    workflow.add_edge("save_neo4j", END)

    graph = workflow.compile()

    yield graph
async def long_term_storage(long_term_type:str="chunk",langchain_messages:list=[],memory_config:str='',end_user_id:str='',scope:int=6):
    """Dispatch long-term memory storage to Celery background tasks.
    
    Args:
        long_term_type: Storage strategy - 'chunk' (window), 'time', or 'aggregate'
        langchain_messages: List of messages to store
        memory_config: Memory configuration ID (string)
        end_user_id: End user identifier
        scope: Window size for 'chunk' strategy (default: 6)
    """
    from app.tasks import (
        long_term_storage_window_task,
        # TODO: Uncomment when implemented
        # long_term_storage_time_task,
        # long_term_storage_aggregate_task,
    )
    from app.core.logging_config import get_logger
    
    logger = get_logger(__name__)
    
    # Convert config to string if needed
    config_id = str(memory_config) if memory_config else ''
    
    if long_term_type == 'chunk':
        # Strategy 1: Window-based batching (6 rounds of dialogue)
        logger.info(f"[LONG_TERM] Dispatching window task - end_user_id={end_user_id}, scope={scope}")
        long_term_storage_window_task.delay(
            end_user_id=end_user_id,
            langchain_messages=langchain_messages,
            config_id=config_id,
            scope=scope
        )
    # TODO: Uncomment when time-based strategy is fully implemented
    # elif long_term_type == 'time':
    #     # Strategy 2: Time-based retrieval
    #     logger.info(f"[LONG_TERM] Dispatching time task - end_user_id={end_user_id}")
    #     long_term_storage_time_task.delay(
    #         end_user_id=end_user_id,
    #         config_id=config_id,
    #         time_window=5
    #     )
    # TODO: Uncomment when aggregate strategy is fully implemented
    # elif long_term_type == 'aggregate':
    #     # Strategy 3: Aggregate judgment (deduplication)
    #     logger.info(f"[LONG_TERM] Dispatching aggregate task - end_user_id={end_user_id}")
    #     long_term_storage_aggregate_task.delay(
    #         end_user_id=end_user_id,
    #         langchain_messages=langchain_messages,
    #         config_id=config_id
    #     )


# async def main():
#     """主函数 - 运行工作流"""
#     langchain_messages = [
#     {
#       "role": "user",
#       "content": "今天周五好开心啊"
#     },
#     {
#       "role": "assistant",
#       "content": "你也这么觉得，我也是耶"
#     }
#
#   ]
#     end_user_id = '837fee1b-04a2-48ee-94d7-211488908940'  # 组ID
#     memory_config="08ed205c-0f05-49c3-8e0c-a580d28f5fd4"
#     # await long_term_storage(long_term_type="chunk",langchain_messages=langchain_messages,memory_config=memory_config,end_user_id=end_user_id,scope=2)
#     result=await long_term_storage(long_term_type="chunk",langchain_messages=langchain_messages,memory_config=memory_config,end_user_id=end_user_id,scope=2)
#
#
# if __name__ == "__main__":
#     import asyncio
#     asyncio.run(main())
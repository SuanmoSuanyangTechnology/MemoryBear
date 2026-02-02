
import asyncio
import json
import sys
import warnings
from contextlib import asynccontextmanager
from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.core.memory.agent.langgraph_graph.tools.write_tool import format_parsing, chat_data_format, messages_parse
from app.db import get_db
from app.core.logging_config import get_agent_logger
from app.core.memory.agent.utils.llm_tools import WriteState
from app.core.memory.agent.langgraph_graph.nodes.write_nodes import write_node
from app.services.memory_config_service import MemoryConfigService

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
    from app.core.memory.agent.langgraph_graph.routing.write_router import memory_long_term_storage, window_dialogue,aggregate_judgment
    from app.core.memory.agent.langgraph_graph.tools.write_tool import chat_data_format
    from app.core.memory.agent.utils.redis_tool import write_store
    write_store.save_session_write(end_user_id, await chat_data_format(langchain_messages))
    # 获取数据库会话
    db_session = next(get_db())
    config_service = MemoryConfigService(db_session)
    memory_config = config_service.load_memory_config(
        config_id="08ed205c-0f05-49c3-8e0c-a580d28f5fd4",  # 改为整数
        service_name="MemoryAgentService"
    )
    if long_term_type=='chunk':
        '''方案一:对话窗口6轮对话'''
        await window_dialogue(end_user_id,langchain_messages,memory_config,scope)
    if long_term_type=='time':
        """时间"""
        await memory_long_term_storage(end_user_id, memory_config,5)
    if  long_term_type=='aggregate':

        """方案三：聚合判断"""
        await aggregate_judgment(end_user_id, langchain_messages, memory_config)

#
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
#     from app.core.memory.agent.utils.redis_tool import write_store
#     result=write_store.get_session_by_userid(end_user_id)
#     data=await format_parsing(result,"dict")
#     chunk_data=data[:6]
#
#     long_time_data = write_store.find_user_recent_sessions(end_user_id, 240)
#     long_=await messages_parse(long_time_data)
#     print(long_)
#
#
# if __name__ == "__main__":
#     import asyncio
#     asyncio.run(main())
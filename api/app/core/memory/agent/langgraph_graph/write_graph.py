import asyncio
import json
import sys
import warnings
from contextlib import asynccontextmanager
from langgraph.constants import END, START
from langgraph.graph import StateGraph

from app.db import get_db, get_db_context
from app.core.logging_config import get_agent_logger
from app.core.memory.agent.utils.llm_tools import WriteState
from app.core.memory.agent.langgraph_graph.nodes.write_nodes import write_node
from app.schemas.memory_agent_schema import AgentMemory_Long_Term
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
    from app.core.memory.agent.utils.redis_tool import write_store
    write_store.save_session_write(end_user_id,  (langchain_messages))
    # 获取数据库会话
    with get_db_context() as db_session:
        config_service = MemoryConfigService(db_session)
        memory_config = config_service.load_memory_config(
            config_id=memory_config,  # 改为整数
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



async def write_long_term(storage_type,end_user_id,message_chat,aimessages,user_rag_memory_id,actual_config_id):
    from app.core.memory.agent.langgraph_graph.routing.write_router import write_rag_agent
    from app.core.memory.agent.langgraph_graph.routing.write_router import term_memory_save
    from app.core.memory.agent.langgraph_graph.tools.write_tool import  agent_chat_messages
    if storage_type == AgentMemory_Long_Term.STORAGE_RAG:
        await write_rag_agent(end_user_id, message_chat, aimessages, user_rag_memory_id)
    else:
        # AI 回复写入（用户消息和 AI 回复配对，一次性写入完整对话）
        CHUNK = AgentMemory_Long_Term.STRATEGY_CHUNK
        SCOPE = AgentMemory_Long_Term.DEFAULT_SCOPE
        long_term_messages = await agent_chat_messages(message_chat, aimessages)
        await long_term_storage(long_term_type=CHUNK, langchain_messages=long_term_messages,
                                memory_config=actual_config_id, end_user_id=end_user_id, scope=SCOPE)
        await term_memory_save(long_term_messages, actual_config_id, end_user_id, CHUNK, scope=SCOPE)

# async def main():
#     """主函数 - 运行工作流"""
#     langchain_messages = [
#     {
#       "role": "user",
#       "content": "今天周五去爬山"
#     },
#     {
#       "role": "assistant",
#       "content": "好耶"
#     }
#
#   ]
#     end_user_id = '837fee1b-04a2-48ee-94d7-211488908940'  # 组ID
#     memory_config="08ed205c-0f05-49c3-8e0c-a580d28f5fd4"
#     await long_term_storage(long_term_type="chunk",langchain_messages=langchain_messages,memory_config=memory_config,end_user_id=end_user_id,scope=2)
#
#
#
# if __name__ == "__main__":
#     import asyncio
#     asyncio.run(main())
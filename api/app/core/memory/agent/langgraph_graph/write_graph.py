
import asyncio
import sys
import warnings
from contextlib import asynccontextmanager


from langchain_core.messages import HumanMessage
from langgraph.constants import END, START
from langgraph.graph import StateGraph


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

    The workflow directly processes messages from the initial state
    and saves them to Neo4j storage.
    """
    workflow = StateGraph(WriteState)
    workflow.add_node("save_neo4j", write_node)
    workflow.add_edge(START, "save_neo4j")
    workflow.add_edge("save_neo4j", END)

    graph = workflow.compile()

    yield graph


async def main():
    """主函数 - 运行工作流"""
    message = "今天周一"
    group_id = 'new_2025test1103'  # 组ID


    # 获取数据库会话
    db_session = next(get_db())
    config_service = MemoryConfigService(db_session)
    memory_config = config_service.load_memory_config(
        config_id=17,  # 改为整数
        service_name="MemoryAgentService"
    )
    try:
        async with make_write_graph() as graph:
            config = {"configurable": {"thread_id": group_id}}
            # 初始状态 - 包含所有必要字段
            initial_state = {"messages": [HumanMessage(content=message)],  "group_id": group_id, "memory_config": memory_config}

            # 获取节点更新信息
            async for update_event in graph.astream(
                    initial_state,
                    stream_mode="updates",
                    config=config
            ):
                for node_name, node_data in update_event.items():
                    if 'save_neo4j'==node_name:
                        massages=node_data
            massages=massages.get('write_result')['status']
            print(massages)  # | 更新数据: {node_data}

    except Exception as e:
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
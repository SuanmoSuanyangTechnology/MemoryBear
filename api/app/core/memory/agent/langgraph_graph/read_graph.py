#!/usr/bin/env python3
from contextlib import asynccontextmanager

from langchain_core.messages import HumanMessage
from langgraph.constants import START, END
from langgraph.graph import StateGraph


from app.db import get_db
from app.services.memory_config_service import MemoryConfigService

from app.core.memory.agent.utils.llm_tools import ReadState
from app.core.memory.agent.langgraph_graph.nodes.data_nodes import content_input_node
from app.core.memory.agent.langgraph_graph.nodes.problem_nodes import (
    Split_The_Problem,
    Problem_Extension,
)
from app.core.memory.agent.langgraph_graph.nodes.retrieve_nodes import (
    retrieve,
)
from app.core.memory.agent.langgraph_graph.nodes.summary_nodes import (
    Input_Summary,
    Retrieve_Summary,
    Summary_fails,
    Summary,
)
from app.core.memory.agent.langgraph_graph.nodes.verification_nodes import Verify
from app.core.memory.agent.langgraph_graph.routing.routers import (
    Split_continue,
    Retrieve_continue,
    Verify_continue,
)



@asynccontextmanager
async def make_read_graph():
    """创建并返回 LangGraph 工作流"""
    try:
        # Build workflow graph
        workflow = StateGraph(ReadState)
        workflow.add_node("content_input", content_input_node)
        workflow.add_node("Split_The_Problem", Split_The_Problem)
        workflow.add_node("Problem_Extension", Problem_Extension)
        workflow.add_node("Input_Summary", Input_Summary)
        # workflow.add_node("Retrieve", retrieve_nodes)
        workflow.add_node("Retrieve", retrieve)
        workflow.add_node("Verify", Verify)
        workflow.add_node("Retrieve_Summary", Retrieve_Summary)
        workflow.add_node("Summary", Summary)
        workflow.add_node("Summary_fails", Summary_fails)
        
        # 添加边
        workflow.add_edge(START, "content_input")
        workflow.add_conditional_edges("content_input", Split_continue)
        workflow.add_edge("Input_Summary", END)
        workflow.add_edge("Split_The_Problem", "Problem_Extension")
        workflow.add_edge("Problem_Extension", "Retrieve")
        workflow.add_conditional_edges("Retrieve", Retrieve_continue)
        workflow.add_edge("Retrieve_Summary", END)
        workflow.add_conditional_edges("Verify", Verify_continue)
        workflow.add_edge("Summary_fails", END)
        workflow.add_edge("Summary", END)


        '''-----'''
        # workflow.add_edge("Retrieve", END)
        
        # 编译工作流
        graph = workflow.compile()
        yield graph
        
    except Exception as e:
        print(f"创建工作流失败: {e}")
        raise
    finally:
        print("工作流创建完成")

async def main():
    """主函数 - 运行工作流"""
    message = "昨天有什么好看的电影"
    group_id = '88a459f5_text09'  # 组ID
    storage_type = 'neo4j'  # 存储类型
    search_switch = '1'  # 搜索开关
    user_rag_memory_id = 'wwwwwwww'  # 用户RAG记忆ID

    # 获取数据库会话
    db_session = next(get_db())
    config_service = MemoryConfigService(db_session)
    memory_config = config_service.load_memory_config(
        config_id=17,  # 改为整数
        service_name="MemoryAgentService"
    )
    import time
    start=time.time()
    try:
        async with make_read_graph() as graph:
            config = {"configurable": {"thread_id": group_id}}
            # 初始状态 - 包含所有必要字段
            initial_state = {"messages": [HumanMessage(content=message)] ,"search_switch":search_switch,"group_id":group_id
                             ,"storage_type":storage_type,"user_rag_memory_id":user_rag_memory_id,"memory_config":memory_config}
            # 获取节点更新信息
            _intermediate_outputs = []
            summary = ''
            
            async for update_event in graph.astream(
                    initial_state,
                    stream_mode="updates",
                    config=config
            ):
                for node_name, node_data in update_event.items():
                    print(f"处理节点: {node_name}")
                    
                    # 处理不同Summary节点的返回结构
                    if 'Summary' in node_name:
                        if 'InputSummary' in node_data and 'summary_result' in node_data['InputSummary']:
                            summary = node_data['InputSummary']['summary_result']
                        elif 'RetrieveSummary' in node_data and 'summary_result' in node_data['RetrieveSummary']:
                            summary = node_data['RetrieveSummary']['summary_result']
                        elif 'summary' in node_data and 'summary_result' in node_data['summary']:
                            summary = node_data['summary']['summary_result']
                        elif 'SummaryFails' in node_data and 'summary_result' in node_data['SummaryFails']:
                            summary = node_data['SummaryFails']['summary_result']

                    spit_data = node_data.get('spit_data', {}).get('_intermediate', None)
                    if spit_data and spit_data != [] and spit_data != {}:
                        _intermediate_outputs.append(spit_data)
                    
                    # Problem_Extension 节点
                    problem_extension = node_data.get('problem_extension', {}).get('_intermediate', None)
                    if problem_extension and problem_extension != [] and problem_extension != {}:
                        _intermediate_outputs.append(problem_extension)
                    
                    # Retrieve 节点
                    retrieve_node = node_data.get('retrieve', {}).get('_intermediate_outputs', None)
                    if retrieve_node and retrieve_node != [] and retrieve_node != {}:
                        _intermediate_outputs.extend(retrieve_node)
                    
                    # Verify 节点
                    verify_n = node_data.get('verify', {}).get('_intermediate', None)
                    if verify_n and verify_n != [] and verify_n != {}:
                        _intermediate_outputs.append(verify_n)

                    
                    # Summary 节点
                    summary_n = node_data.get('summary', {}).get('_intermediate', None)
                    if summary_n and summary_n != [] and summary_n != {}:
                        _intermediate_outputs.append(summary_n)

            # # 过滤掉空值
            # _intermediate_outputs = [item for item in _intermediate_outputs if item and item != [] and item != {}]
            #
            # # 优化搜索结果
            # print("=== 开始优化搜索结果 ===")
            # optimized_outputs = merge_multiple_search_results(_intermediate_outputs)
            # result=reorder_output_results(optimized_outputs)
            # # 保存优化后的结果到文件
            # with open('_intermediate_outputs_optimized.json', 'w', encoding='utf-8') as f:
            #     import json
            #     f.write(json.dumps(result, indent=4, ensure_ascii=False))
            #
            print(f"=== 最终摘要 ===")
            print(summary)
                
    except Exception as e:
        import traceback
        traceback.print_exc()

    end=time.time()
    print(100*'y')
    print(f"总耗时: {end-start}s")
    print(100*'y')


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

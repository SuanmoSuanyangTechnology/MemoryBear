import os

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.langgraph_graph.tools.write_tool import chat_data_format, format_parsing
from app.core.memory.agent.langgraph_graph.write_graph import make_write_graph

from app.core.memory.agent.models.write_aggregate_model import WriteAggregateModel
from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_
from app.core.memory.agent.utils.redis_tool import write_store
from app.core.memory.agent.utils.redis_tool import count_store
from app.core.memory.agent.utils.template_tools import TemplateService
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
logger = get_agent_logger(__name__)
template_root = os.path.join(PROJECT_ROOT_, 'memory', 'agent', 'utils', 'prompt')


async def write_messages(end_user_id,langchain_messages,memory_config):
    '''
    写入数据到neo4j：
     Args:
        end_user_id: 终端用户ID
        memory_config: 内存配置对象
        langchain_messages：原始数据LIST
    '''
    try:

        async with make_write_graph() as graph:
            config = {"configurable": {"thread_id": end_user_id}}
            # 初始状态 - 包含所有必要字段
            initial_state = {
                "messages": langchain_messages,
                "end_user_id": end_user_id,
                "memory_config": memory_config
            }

            # 获取节点更新信息
            async for update_event in graph.astream(
                    initial_state,
                    stream_mode="updates",
                    config=config
            ):
                for node_name, node_data in update_event.items():
                    if 'save_neo4j' == node_name:
                        massages = node_data
            # TODO：删除
            massagesstatus = massages.get('write_result')['status']
            contents = massages.get('write_result')
            print(contents)
    except Exception as e:
        import traceback
        traceback.print_exc()
'''根据窗口'''
async def window_dialogue(end_user_id,langchain_messages,memory_config,scope):
    '''
    根据窗口获取redis数据,写入neo4j：
     Args:
        end_user_id: 终端用户ID
        memory_config: 内存配置对象
        langchain_messages：原始数据LIST
        scope：窗口大小
    '''
    scope=scope
    redis_messages = []
    is_end_user_id = count_store.get_sessions_count(end_user_id)
    if is_end_user_id is not False:
        is_end_user_id = count_store.get_sessions_count(end_user_id)[0]
        redis_messages = count_store.get_sessions_count(end_user_id)[1]
    if is_end_user_id and int(is_end_user_id) != int(scope):
        print(is_end_user_id)
        is_end_user_id += 1
        langchain_messages += redis_messages
        count_store.update_sessions_count(end_user_id, is_end_user_id, langchain_messages)
    elif int(is_end_user_id) == int(scope):
        print('写入长期记忆，并且设置为0')
        print(is_end_user_id)
        formatted_messages = await chat_data_format(redis_messages)
        print(100*'-')
        print(formatted_messages)
        print(100*'-')
        await write_messages(end_user_id, formatted_messages, memory_config)
        count_store.update_sessions_count(end_user_id, 0, '')
    else:
        count_store.save_sessions_count(end_user_id, 1, langchain_messages)


"""根据时间"""
async def memory_long_term_storage(end_user_id,memory_config,time):
    '''
    根据时间获取redis数据,写入neo4j：
     Args:
        end_user_id: 终端用户ID
        memory_config: 内存配置对象
    '''
    long_time_data = write_store.find_user_recent_sessions(end_user_id, time)
    # Handle case where no session exists in Redis (returns False or empty)
    if not long_time_data or long_time_data is False:
        return
    format_messages = await chat_data_format(long_time_data)
    if format_messages!=[]:
        await write_messages(end_user_id, format_messages, memory_config)
'''聚合判断'''
async def aggregate_judgment(end_user_id: str, ori_messages: list, memory_config) -> dict:
    """
    聚合判断函数：判断输入句子和历史消息是否描述同一事件
    
    Args:
        end_user_id: 终端用户ID
        ori_messages: 原始消息列表，格式如 [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        memory_config: 内存配置对象
    """
    
    try:
        # 1. 获取历史会话数据（使用新方法）
        result = write_store.get_all_sessions_by_end_user_id(end_user_id)
        
        # Handle case where no session exists in Redis (returns False or empty)
        if not result or result is False:
            history = []
        else:
            history = await format_parsing(result)
        json_schema = WriteAggregateModel.model_json_schema()
        template_service = TemplateService(template_root)
        system_prompt = await template_service.render_template(
            template_name='write_aggregate_judgment.jinja2',
            operation_name='aggregate_judgment',
            history=history,
            sentence=ori_messages,
            json_schema=json_schema
        )
        with get_db_context() as db_session:
            factory = MemoryClientFactory(db_session)
            llm_client = factory.get_llm_client(memory_config.llm_model_id)
            messages = [
                {
                    "role": "user",
                    "content": system_prompt
                }
            ]
            structured = await llm_client.response_structured(
                messages=messages,
                response_model=WriteAggregateModel
            )
        output_value = structured.output
        if isinstance(output_value, list):
            output_value = [
                {"role": msg.role, "content": msg.content} 
                for msg in output_value
            ]

        result_dict = {
            "is_same_event": structured.is_same_event,
            "output": output_value
        }
        if not structured.is_same_event:
            logger.info(result_dict)
            await write_messages(end_user_id, output_value, memory_config)
        return result_dict
        
    except Exception as e:
        print(f"[aggregate_judgment] 发生错误: {e}")
        import traceback
        traceback.print_exc()
        
        return {
            "is_same_event": False,
            "output": ori_messages,
            "messages": ori_messages,
            "history": history if 'history' in locals() else [],
            "error": str(e)
        }
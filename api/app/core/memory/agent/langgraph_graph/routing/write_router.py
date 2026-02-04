import json
import os

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.langgraph_graph.tools.write_tool import  format_parsing,  messages_parse
from app.core.memory.agent.langgraph_graph.write_graph import make_write_graph, long_term_storage

from app.core.memory.agent.models.write_aggregate_model import WriteAggregateModel
from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_
from app.core.memory.agent.utils.redis_tool import write_store
from app.core.memory.agent.utils.redis_tool import count_store
from app.core.memory.agent.utils.template_tools import TemplateService
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context, get_db
from app.repositories.memory_short_repository import LongTermMemoryRepository
from app.schemas.memory_agent_schema import AgentMemory_Long_Term
from app.services.memory_konwledges_server import write_rag
from app.services.task_service import get_task_memory_write_result
from app.tasks import write_message_task
from app.utils.config_utils import resolve_config_id
logger = get_agent_logger(__name__)
template_root = os.path.join(PROJECT_ROOT_, 'memory', 'agent', 'utils', 'prompt')

async def write_rag_agent(end_user_id, user_message, ai_message, user_rag_memory_id):
    # RAG 模式：组合消息为字符串格式（保持原有逻辑）
    combined_message = f"user: {user_message}\nassistant: {ai_message}"
    await write_rag(end_user_id, combined_message, user_rag_memory_id)
    logger.info(f'RAG_Agent:{end_user_id};{user_rag_memory_id}')
async def write(storage_type, end_user_id, user_message, ai_message, user_rag_memory_id, actual_end_user_id,
                actual_config_id, long_term_messages=[]):
    """
    写入记忆（支持结构化消息）

    Args:
        storage_type: 存储类型 (neo4j/rag)
        end_user_id: 终端用户ID
        user_message: 用户消息内容
        ai_message: AI 回复内容
        user_rag_memory_id: RAG 记忆ID
        actual_end_user_id: 实际用户ID
        actual_config_id: 配置ID

    逻辑说明：
    - RAG 模式：组合 user_message 和 ai_message 为字符串格式，保持原有逻辑不变
    - Neo4j 模式：使用结构化消息列表
      1. 如果 user_message 和 ai_message 都不为空：创建配对消息 [user, assistant]
      2. 如果只有 user_message：创建单条用户消息 [user]（用于历史记忆场景）
      3. 每条消息会被转换为独立的 Chunk，保留 speaker 字段
    """

    db = next(get_db())
    try:
        actual_config_id = resolve_config_id(actual_config_id, db)
        # Neo4j 模式：使用结构化消息列表
        structured_messages = []

        # 始终添加用户消息（如果不为空）
        if isinstance(user_message, str) and user_message.strip() != "":
            structured_messages.append({"role": "user", "content": user_message})

        # 只有当 AI 回复不为空时才添加 assistant 消息
        if isinstance(ai_message, str) and ai_message.strip() != "":
            structured_messages.append({"role": "assistant", "content": ai_message})

        # 如果提供了 long_term_messages，使用它替代 structured_messages
        if long_term_messages and isinstance(long_term_messages, list):
            structured_messages = long_term_messages
        elif long_term_messages and isinstance(long_term_messages, str):
            # 如果是 JSON 字符串，先解析
            try:
                structured_messages = json.loads(long_term_messages)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse long_term_messages as JSON: {long_term_messages}")

        # 如果没有消息，直接返回
        if not structured_messages:
            logger.warning(f"No messages to write for user {actual_end_user_id}")
            return

        logger.info(
            f"[WRITE] Submitting Celery task - user={actual_end_user_id}, messages={len(structured_messages)}, config={actual_config_id}")
        write_id = write_message_task.delay(
            actual_end_user_id,  # end_user_id: 用户ID
            structured_messages,  # message: JSON 字符串格式的消息列表
            str(actual_config_id),  # config_id: 配置ID字符串
            storage_type,  # storage_type: "neo4j"
            user_rag_memory_id or ""  # user_rag_memory_id: RAG记忆ID（Neo4j模式下不使用）
        )
        logger.info(f"[WRITE] Celery task submitted - task_id={write_id}")
        write_status = get_task_memory_write_result(str(write_id))
        logger.info(f'[WRITE] Task result - user={actual_end_user_id}, status={write_status}')
    finally:
        db.close()

async def term_memory_save(long_term_messages,actual_config_id,end_user_id,type,scope):
    with get_db_context() as db_session:
        try:
            repo = LongTermMemoryRepository(db_session)
            await long_term_storage(long_term_type=AgentMemory_Long_Term.STRATEGY_CHUNK, langchain_messages=long_term_messages,
                                    memory_config=actual_config_id, end_user_id=end_user_id, scope=scope)

            from app.core.memory.agent.utils.redis_tool import write_store
            result = write_store.get_session_by_userid(end_user_id)
            if type==AgentMemory_Long_Term.STRATEGY_CHUNK or AgentMemory_Long_Term.STRATEGY_AGGREGATE:
                data = await format_parsing(result, "dict")
                chunk_data = data[:scope]
                if len(chunk_data)==scope:
                    repo.upsert(end_user_id, chunk_data)
                    logger.info(f'---------写入短长期-----------')
            else:
                long_time_data = write_store.find_user_recent_sessions(end_user_id, 5)
                long_messages = await messages_parse(long_time_data)
                repo.upsert(end_user_id, long_messages)
                logger.info(f'写入短长期：')
            # yield db_session
        finally:
            db_session.close()


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
    is_end_user_id = count_store.get_sessions_count(end_user_id)
    if is_end_user_id is not False:
        is_end_user_id = count_store.get_sessions_count(end_user_id)[0]
        redis_messages = count_store.get_sessions_count(end_user_id)[1]
    if is_end_user_id and int(is_end_user_id) != int(scope):
        is_end_user_id += 1
        langchain_messages += redis_messages
        count_store.update_sessions_count(end_user_id, is_end_user_id, langchain_messages)
    elif int(is_end_user_id) == int(scope):
        logger.info('写入长期记忆NEO4J')
        formatted_messages = (redis_messages)
        # 获取 config_id（如果 memory_config 是对象，提取 config_id；否则直接使用）
        if hasattr(memory_config, 'config_id'):
            config_id = memory_config.config_id
        else:
            config_id = memory_config
        
        await write(AgentMemory_Long_Term.STORAGE_NEO4J, end_user_id, "", "", None, end_user_id,
                config_id, formatted_messages)
        count_store.update_sessions_count(end_user_id, 1, langchain_messages)
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
    format_messages = (long_time_data)
    messages=[]
    memory_config=memory_config.config_id
    for i in format_messages:
        message=json.loads(i['Query'])
        messages+= message
    if format_messages!=[]:
        await write(AgentMemory_Long_Term.STORAGE_NEO4J, end_user_id, "", "", None, end_user_id,
                    memory_config, messages)
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
        history = await format_parsing(result)
        if not result:
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
            await write("neo4j", end_user_id, "", "", None, end_user_id,
                        memory_config.config_id, output_value)
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
import json
import os

from app.core.logging_config import get_agent_logger
from app.core.memory.agent.langgraph_graph.tools.write_tool import format_parsing, messages_parse
from app.core.memory.agent.models.write_aggregate_model import WriteAggregateModel
from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_
from app.core.memory.agent.utils.redis_tool import count_store
from app.core.memory.agent.utils.redis_tool import write_store
from app.core.memory.agent.utils.template_tools import TemplateService
from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
from app.db import get_db_context
from app.repositories.memory_short_repository import LongTermMemoryRepository
from app.schemas.memory_agent_schema import AgentMemory_Long_Term
from app.services.task_service import get_task_memory_write_result
from app.tasks import write_message_task
from app.utils.config_utils import resolve_config_id

logger = get_agent_logger(__name__)
template_root = os.path.join(PROJECT_ROOT_, 'memory', 'agent', 'utils', 'prompt')


async def write(
        storage_type,
        end_user_id,
        user_message,
        ai_message,
        user_rag_memory_id,
        actual_end_user_id,
        actual_config_id,
        long_term_messages=None
):
    """
    Write memory with structured message support

    Handles memory writing operations for different storage types (Neo4j/RAG).
    Supports both individual message pairs and batch long-term message processing.

    Args:
        storage_type: Storage type identifier ("neo4j" or "rag")
        end_user_id: Terminal user identifier
        user_message: User message content
        ai_message: AI response content
        user_rag_memory_id: RAG memory identifier
        actual_end_user_id: Actual user identifier for storage
        actual_config_id: Configuration identifier
        long_term_messages: Optional list of structured messages for batch processing

    Logic explanation:
    - RAG mode: Combines user_message and ai_message into string format, maintains original logic
    - Neo4j mode: Uses structured message lists
      1. If both user_message and ai_message are not empty: Creates paired messages [user, assistant]
      2. If only user_message exists: Creates single user message [user] (for historical memory scenarios)
      3. Each message is converted to independent Chunk, preserving speaker field
    """

    if long_term_messages is None:
        long_term_messages = []
    with get_db_context() as db:
        actual_config_id = resolve_config_id(actual_config_id, db)
        # Neo4j mode: Use structured message lists
        structured_messages = []

        # Always add user message (if not empty)
        if isinstance(user_message, str) and user_message.strip() != "":
            structured_messages.append({"role": "user", "content": user_message})

        # Only add assistant message when AI reply is not empty
        if isinstance(ai_message, str) and ai_message.strip() != "":
            structured_messages.append({"role": "assistant", "content": ai_message})

        # If long_term_messages provided, use it to replace structured_messages
        if long_term_messages and isinstance(long_term_messages, list):
            structured_messages = long_term_messages
        elif long_term_messages and isinstance(long_term_messages, str):
            # If it's a JSON string, parse it first
            try:
                structured_messages = json.loads(long_term_messages)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse long_term_messages as JSON: {long_term_messages}")

        # If no messages, return directly
        if not structured_messages:
            logger.warning(f"No messages to write for user {actual_end_user_id}")
            return

        logger.info(
            f"[WRITE] Submitting Celery task - user={actual_end_user_id}, messages={len(structured_messages)}, config={actual_config_id}")
        write_id = write_message_task.delay(
            actual_end_user_id,  # end_user_id: User ID
            structured_messages,  # message: JSON string format message list
            str(actual_config_id),  # config_id: Configuration ID string
            storage_type,  # storage_type: "neo4j"
            user_rag_memory_id or ""  # user_rag_memory_id: RAG memory ID (not used in Neo4j mode)
        )
        logger.info(f"[WRITE] Celery task submitted - task_id={write_id}")
        write_status = get_task_memory_write_result(str(write_id))
        logger.info(f'[WRITE] Task result - user={actual_end_user_id}, status={write_status}')


async def term_memory_save(end_user_id, strategy_type, scope):
    """
    Save long-term memory data to database

    Handles the storage of long-term memory data based on different strategies
    (chunk-based or aggregate-based) and manages the transition from short-term
    to long-term memory storage.

    Args:
        end_user_id: User identifier for memory association
        strategy_type: Memory storage strategy type (STRATEGY_CHUNK or STRATEGY_AGGREGATE)
        scope: Scope/window size for memory processing
    """
    with get_db_context() as db_session:
        repo = LongTermMemoryRepository(db_session)

        from app.core.memory.agent.utils.redis_tool import write_store
        result = write_store.get_session_by_userid(end_user_id)
        if not result:
            logger.warning(f"No write data found for user {end_user_id}")
            return
        if strategy_type in [AgentMemory_Long_Term.STRATEGY_CHUNK, AgentMemory_Long_Term.STRATEGY_AGGREGATE]:
            data = await format_parsing(result, "dict")
            chunk_data = data[:scope]
            if len(chunk_data) == scope:
                repo.upsert(end_user_id, chunk_data)
                logger.info(f'---------写入短长期-----------')
        else:
            long_time_data = write_store.find_user_recent_sessions(end_user_id, 5)
            long_messages = await messages_parse(long_time_data)
            repo.upsert(end_user_id, long_messages)
            logger.info(f'写入短长期：')


async def window_dialogue(end_user_id, langchain_messages, memory_config, scope):
    """
    Process dialogue based on window size and write to Neo4j

    Manages conversation data based on a sliding window approach. When the window
    reaches the specified scope size, it triggers long-term memory storage to Neo4j.

    Args:
        end_user_id: Terminal user identifier
        memory_config: Memory configuration object containing settings
        langchain_messages: Original message data list
        scope: Window size determining when to trigger long-term storage
    """
    is_end_user_has_history = count_store.get_sessions_count(end_user_id)
    if is_end_user_has_history:
        end_user_visit_count, redis_messages = is_end_user_has_history
    else:
        count_store.save_sessions_count(end_user_id, 1, langchain_messages)
        return
    end_user_visit_count += 1
    if end_user_visit_count < scope:
        redis_messages.extend(langchain_messages)
        count_store.update_sessions_count(end_user_id, end_user_visit_count, redis_messages)
    else:
        logger.info('写入长期记忆NEO4J')
        redis_messages.extend(langchain_messages)
        # Get config_id (if memory_config is an object, extract config_id; otherwise use directly)
        if hasattr(memory_config, 'config_id'):
            config_id = memory_config.config_id
        else:
            config_id = memory_config

        write_message_task.delay(
            end_user_id,  # end_user_id: User ID
            redis_messages,  # message: JSON string format message list
            config_id,  # config_id: Configuration ID string
            AgentMemory_Long_Term.STORAGE_NEO4J,  # storage_type: "neo4j"
            ""  # user_rag_memory_id: RAG memory ID (not used in Neo4j mode)
        )
        count_store.update_sessions_count(end_user_id, 0, [])


async def memory_long_term_storage(end_user_id, memory_config, time):
    """
    Process memory storage based on time intervals and write to Neo4j

    Retrieves Redis data based on time intervals and writes it to Neo4j for
    long-term storage. This function handles time-based memory consolidation.

    Args:
        end_user_id: Terminal user identifier
        memory_config: Memory configuration object containing settings
        time: Time interval for data retrieval
    """
    long_time_data = write_store.find_user_recent_sessions(end_user_id, time)
    format_messages = long_time_data
    messages = []
    memory_config = memory_config.config_id
    for i in format_messages:
        message = json.loads(i['Query'])
        messages += message
    if format_messages:
        await write(AgentMemory_Long_Term.STORAGE_NEO4J, end_user_id, "", "", None, end_user_id,
                    memory_config, messages)


async def aggregate_judgment(end_user_id: str, ori_messages: list, memory_config) -> dict:
    """
    Aggregation judgment function: determine if input sentence and historical messages describe the same event

    Uses LLM-based analysis to determine whether new messages should be aggregated with existing
    historical data or stored as separate events. This helps optimize memory storage and retrieval.
    
    Args:
        end_user_id: Terminal user identifier
        ori_messages: Original message list, format like [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
        memory_config: Memory configuration object containing LLM settings

    Returns:
        dict: Aggregation judgment result containing is_same_event flag and processed output
    """
    history = None
    try:
        # 1. Get historical session data (using new method)
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
        logger.error(f"[aggregate_judgment] 发生错误: {e}", exc_info=True)

        return {
            "is_same_event": False,
            "output": ori_messages,
            "messages": ori_messages,
            "history": history if 'history' in locals() else [],
            "error": str(e)
        }

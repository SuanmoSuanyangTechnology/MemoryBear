"""
Memory Agent Service

Handles business logic for memory agent operations including read/write services,
health checks, and message type classification.
"""
import os
import re
import time
import json
import uuid
from threading import Lock
from typing import Dict, List, Optional, Any, AsyncGenerator
from app.services.memory_konwledges_server import write_rag
import redis
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, Field
 
from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.memory.agent.langgraph_graph.read_graph import make_read_graph
from app.core.memory.agent.langgraph_graph.write_graph import make_write_graph
from app.core.memory.agent.logger_file.log_streamer import LogStreamer
from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_
from app.core.memory.agent.utils.mcp_tools import get_mcp_server_config
from app.core.memory.agent.utils.type_classifier import status_typle
from app.db import get_db
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
# TODO 后续更新
# from app.core.memory.analytics.hot_memory_tags import get_hot_memory_tags
from app.core.memory.utils.llm.llm_utils import get_llm_client
from app.schemas.memory_storage_schema import ApiResponse, ok, fail
from app.models.knowledge_model import Knowledge, KnowledgeType
from app.repositories.data_config_repository import DataConfigRepository
from app.core.memory.agent.logger_file.log_streamer import LogStreamer
from app.services.memory_konwledges_server import memory_konwledges_up, SimpleUser, find_document_id_by_kb_and_filename
from app.core.memory.utils.config.definitions import reload_configuration_from_database
from app.schemas.file_schema import CustomTextFileCreate
try:
    from app.core.memory.utils.log.audit_logger import audit_logger
except ImportError:
    audit_logger = None
logger = get_logger(__name__)

# Initialize Neo4j connector for analytics functions
_neo4j_connector = Neo4jConnector()
db_gen = get_db()
db = next(db_gen)

class MemoryAgentService:
    """Service for memory agent operations"""
    
    def __init__(self):
        self.user_locks: Dict[str, Lock] = {}
        self.locks_lock = Lock()

    def writer_messages_deal(self,messages,start_time,group_id,config_id,message):
        messages = str(messages).replace("'", '"').replace('\\n', '').replace('\n', '').replace('\\', '')
        countext = re.findall(r'"status": "(.*?)",', messages)[0]
        duration = time.time() - start_time

        if countext == 'success':
            logger.info(f"Write operation successful for group {group_id} with config_id {config_id}")
            # 记录成功的操作
            if audit_logger:
                audit_logger.log_operation(operation="WRITE", config_id=config_id, group_id=group_id, success=True,
                                           duration=duration, details={"message_length": len(message)})
            return countext
        else:
            logger.warning(f"Write operation failed for group {group_id}")

            # 记录失败的操作
            if audit_logger:
                audit_logger.log_operation(
                    operation="WRITE",
                    config_id=config_id,
                    group_id=group_id,
                    success=False,
                    duration=duration,
                    error=f"写入失败: {messages[:100]}"
                )

            raise ValueError(f"写入失败: {messages}")
    
    def get_group_lock(self, group_id: str) -> Lock:
        """Get lock for specific group to prevent concurrent processing"""
        with self.locks_lock:
            if group_id not in self.user_locks:
                self.user_locks[group_id] = Lock()
            return self.user_locks[group_id]
    
    def extract_tool_call_info(self, event: Dict) -> bool:
        """Extract tool call information from event"""
        last_message = event["messages"][-1]

        # Check if AI message contains tool calls
        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            tool_calls = last_message.tool_calls
            for i, tool_call in enumerate(tool_calls):
                if isinstance(tool_call, dict):
                    tool_call_id = tool_call.get('id')
                    tool_name = tool_call.get('name')
                    tool_args = tool_call.get('args', {})
                else:
                    tool_call_id = getattr(tool_call, 'id', None)
                    tool_name = getattr(tool_call, 'name', None)
                    tool_args = getattr(tool_call, 'args', {})

                logger.debug(f"Tool Call {i + 1}: ID={tool_call_id}, Name={tool_name}, Args={tool_args}")
            return True

        # Check if tool message
        elif hasattr(last_message, 'tool_call_id'):
            tool_call_id = getattr(last_message, 'tool_call_id', None)
            if hasattr(last_message, 'name') and hasattr(last_message, 'content'):
                tool_name = getattr(last_message, 'name', None)
                try:
                    content = json.loads(getattr(last_message, 'content', '{}'))
                    tool_args = content.get('args', {})
                    logger.debug(f"Tool Call 1: ID={tool_call_id}, Name={tool_name}, Args={tool_args}")
                except:
                    logger.debug(f"Tool Response ID: {tool_call_id}")
            else:
                logger.debug(f"Tool Response ID: {tool_call_id}")
            return True

        return False
    
    async def get_health_status(self) -> Dict:
        """
        Get latest health status from Redis cache
        
        Returns health status information written by Celery periodic task
        """
        logger.info("Checking health status")
        
        client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None
        )
        payload = client.hgetall("memsci:health:read_service") or {}
        
        if payload:
            # decode bytes to str
            decoded = {k.decode("utf-8"): v.decode("utf-8") for k, v in payload.items()}
            status = decoded.get("status", "unknown")
        else:
            status = "unknown"
        
        logger.info(f"Health status: {status}")
        return {"status": status}

    def get_log_content(self) -> str:
        """
        Read and return agent service log file content
        
        Returns cleaned log content using the same cleaning logic as transmission mode        

        Returns cleaned log content using the same cleaning logic as transmission mode
        """
        logger.info("Reading log file")

        # Use project root directory for logs
        # Get the project root (redbear-mem directory)
        current_file = os.path.abspath(__file__)  # app/services/memory_agent_service.py
        app_dir = os.path.dirname(os.path.dirname(current_file))  # app directory
        project_root = os.path.dirname(app_dir)  # redbear-mem directory
        log_path = os.path.join(project_root, "logs", "agent_service.log")
        
        summer = ''

        with open(log_path, "r", encoding="utf-8") as infile:
            for line in infile:
                # Use the same cleaning logic as LogStreamer for consistency
                cleaned = LogStreamer.clean_log_line(line)
                summer += cleaned

        if len(summer) < 10:
            raise ValueError("NO LOGS")

        logger.info(f"Log content retrieved, size: {len(summer)} bytes")
        return summer
    
    async def stream_log_content(self) -> AsyncGenerator[str, None]:
        """
        Stream log content in real-time using Server-Sent Events (SSE)
        
        This method establishes a streaming connection and transmits log entries
        as they are written to the log file. It uses the LogStreamer to watch
        the file and yields SSE-formatted messages.
        
        Yields:
            SSE-formatted strings with the following event types:
            - log: Contains log content and timestamp
            - keepalive: Periodic keepalive messages to maintain connection
            - error: Error information if streaming fails
            - done: Indicates streaming has completed
            
        Raises:
            FileNotFoundError: If log file doesn't exist at stream start
            Exception: For other unexpected errors during streaming
        """
        logger.info("Starting log content streaming")
        
        # Get log file path - use project root directory
        current_file = os.path.abspath(__file__)  # app/services/memory_agent_service.py
        app_dir = os.path.dirname(os.path.dirname(current_file))  # app directory
        project_root = os.path.dirname(app_dir)  # redbear-mem directory
        log_path = os.path.join(project_root, "logs", "agent_service.log")
        
        # Check if file exists before starting stream
        if not os.path.exists(log_path):
            logger.error(f"Log file not found: {log_path}")
            # Send error event in SSE format
            yield f"event: error\ndata: {json.dumps({'code': 4006, 'message': '日志文件不存在', 'error': f'File not found: {log_path}'})}\n\n"
            return
        
        streamer = None
        try:
            # Initialize LogStreamer with keepalive interval from settings (default 300 seconds)
            keepalive_interval = getattr(settings, 'LOG_STREAM_KEEPALIVE_INTERVAL', 300)
            streamer = LogStreamer(log_path, keepalive_interval=keepalive_interval)
            
            logger.info(f"LogStreamer initialized for {log_path}")
            
            # Stream log content using read_existing_and_stream to get all existing content first
            async for message in streamer.read_existing_and_stream():
                event_type = message.get("event")
                data = message.get("data")
                
                # Format as SSE message
                # SSE format: "event: <type>\ndata: <json_data>\n\n"
                sse_message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
                
                logger.debug(f"Streaming event: {event_type}")
                yield sse_message
                
                # If error or done event, stop streaming
                if event_type in ["error", "done"]:
                    logger.info(f"Stream ended with event: {event_type}")
                    break
                    
        except FileNotFoundError as e:
            logger.error(f"Log file not found during streaming: {e}")
            yield f"event: error\ndata: {json.dumps({'code': 4006, 'message': '日志文件在流式传输期间变得不可用', 'error': str(e)})}\n\n"
            
        except Exception as e:
            logger.error(f"Unexpected error during log streaming: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'code': 8001, 'message': '流式传输期间发生错误', 'error': str(e)})}\n\n"
            
        finally:
            # Resource cleanup
            logger.info("Log streaming completed, cleaning up resources")
            # LogStreamer uses context manager for file handling, so cleanup is automatic
    
    async def write_memory(self, group_id: str, message: str, config_id: str,storage_type:str,user_rag_memory_id:str) -> str:
        """
        Process write operation with config_id
        
        Args:
            group_id: Group identifier
            message: Message to write
            config_id: Configuration ID from database
            
        Returns:
            Write operation result status
            
        Raises:
            ValueError: If config loading fails or write operation fails
        """
        if config_id==None:
            config_id = os.getenv("config_id")
        import time
        start_time = time.time()

        # 如果 config_id 为 None，使用默认值 "17"
        config_loaded = reload_configuration_from_database(config_id)
        if not config_loaded:
            error_msg = f"Failed to load configuration for config_id: {config_id}"
            logger.error(error_msg)
            
            # 记录失败的操作
            if audit_logger:
                duration = time.time() - start_time
                audit_logger.log_operation( operation="WRITE",  config_id=config_id, group_id=group_id,   success=False, duration=duration, error=error_msg  )
            
            raise ValueError(error_msg)
        logger.info(f"Configuration loaded successfully for config_id: {config_id}")
        mcp_config = get_mcp_server_config()
        client = MultiServerMCPClient(mcp_config)

        if storage_type == "rag":
            result = await write_rag(group_id, message, user_rag_memory_id)
            return result
        else:
            async with client.session("data_flow") as session:
                logger.debug("Connected to MCP Server: data_flow")
                tools = await load_mcp_tools(session)

                # Pass config_id to the graph workflow
                async with make_write_graph(group_id, tools, group_id, group_id, config_id=config_id) as graph:
                    logger.debug("Write graph created successfully")

                    config = {"configurable": {"thread_id": group_id}}

                    async for event in graph.astream(
                            {"messages": message, "config_id": config_id},
                            stream_mode="values",
                            config=config
                    ):
                        messages = event.get('messages')
            return self.writer_messages_deal(messages,start_time,group_id,config_id,message)
    
    async def read_memory(
        self,
        group_id: str,
        message: str,
        history: List[Dict],
        search_switch: str,
        config_id: str,
        storage_type: str,
        user_rag_memory_id: str
    ) -> Dict:
        """
        Process read operation with config_id
        
        search_switch values:
        - "0": Requires verification
        - "1": No verification, direct split
        - "2": Direct answer based on context
        
        Args:
            group_id: Group identifier
            message: User message
            history: Conversation history
            search_switch: Search mode switch
            config_id: Configuration ID from database
            
        Returns:
            Dict with 'answer' and 'intermediate_outputs' keys
            
        Raises:
            ValueError: If config loading fails
        """

        import time
        start_time = time.time()

        if config_id==None:
            config_id = os.getenv("config_id")
        
        logger.info(f"Read operation for group {group_id} with config_id {config_id}")
        
        # 导入审计日志记录器
        try:
            from app.core.memory.utils.log.audit_logger import audit_logger
        except ImportError:
            audit_logger = None
        
        # Get group lock to prevent concurrent processing
        group_lock = self.get_group_lock(group_id)

        with group_lock:
            # Step 1: Load configuration from database
            from app.core.memory.utils.config.definitions import reload_configuration_from_database
            
            config_loaded = reload_configuration_from_database(config_id)
            if not config_loaded:
                error_msg = f"Failed to load configuration for config_id: {config_id}"
                logger.error(error_msg)
                
                # 记录失败的操作
                if audit_logger:
                    duration = time.time() - start_time
                    audit_logger.log_operation(
                        operation="READ",
                        config_id=config_id,
                        group_id=group_id,
                        success=False,
                        duration=duration,
                        error=error_msg
                    )
                
                raise ValueError(error_msg)
            
            logger.info(f"Configuration loaded successfully for config_id: {config_id}")
            
            # Step 2: Prepare history
            history.append({"role": "user", "content": message})
            logger.debug(f"Group ID:{group_id}, Message:{message}, History:{history}, Config ID:{config_id}")

            # Step 3: Initialize MCP client and execute read workflow
            mcp_config = get_mcp_server_config()
            client = MultiServerMCPClient(mcp_config)
            
            async with client.session('data_flow') as session:
                logger.debug("Connected to MCP Server: data_flow")
                tools = await load_mcp_tools(session)
                outputs = []
                intermediate_outputs = []
                seen_intermediates = set()  # Track seen intermediate outputs to avoid duplicates

                # Pass config_id to the graph workflow
                async with make_read_graph(group_id, tools, search_switch, group_id, group_id, config_id=config_id,storage_type=storage_type,user_rag_memory_id=user_rag_memory_id) as graph:
                    start = time.time()
                    config = {"configurable": {"thread_id": group_id}}

                    async for event in graph.astream(
                            {"messages": history, "config_id": config_id},
                            stream_mode="values",
                            config=config
                    ):
                        messages = event.get('messages')
                        for msg in messages:
                            msg_content = msg.content
                            outputs.append({
                                "role": msg.__class__.__name__.lower().replace("message", ""),
                                "content": msg_content
                            })
                            
                            # Extract intermediate outputs
                            if hasattr(msg, 'content'):
                                try:
                                    # Debug: log message type and content preview
                                    msg_type = msg.__class__.__name__
                                    content_preview = str(msg_content)[:200] if msg_content else "empty"
                                    logger.debug(f"Processing message type={msg_type}, content preview={content_preview}")
                                    
                                    # Try to parse content as JSON
                                    if isinstance(msg_content, str):
                                        try:
                                            parsed = json.loads(msg_content)
                                            if isinstance(parsed, dict):
                                                # Debug: log what keys are in parsed
                                                logger.debug(f"Parsed dict keys: {list(parsed.keys())}")
                                                
                                                # Check for single intermediate output
                                                if '_intermediate' in parsed:
                                                    intermediate_data = parsed['_intermediate']
                                                    output_key = self._create_intermediate_key(intermediate_data)
                                                    logger.debug(f"Found _intermediate: {intermediate_data.get('type', 'unknown')}")
                                                    
                                                    if output_key not in seen_intermediates:
                                                        seen_intermediates.add(output_key)
                                                        intermediate_outputs.append(self._format_intermediate_output(intermediate_data))
                                                
                                                # Check for multiple intermediate outputs (from Retrieve)
                                                if '_intermediates' in parsed:
                                                    logger.debug(f"Found _intermediates list with {len(parsed['_intermediates'])} items")
                                                    for intermediate_data in parsed['_intermediates']:
                                                        output_key = self._create_intermediate_key(intermediate_data)
                                                        logger.debug(f"Processing intermediate: {intermediate_data.get('type', 'unknown')}")
                                                        
                                                        if output_key not in seen_intermediates:
                                                            seen_intermediates.add(output_key)
                                                            intermediate_outputs.append(self._format_intermediate_output(intermediate_data))
                                        except (json.JSONDecodeError, ValueError):
                                            pass
                                    elif isinstance(msg_content, dict):
                                        # Check for single intermediate output
                                        if '_intermediate' in msg_content:
                                            intermediate_data = msg_content['_intermediate']
                                            output_key = self._create_intermediate_key(intermediate_data)
                                            
                                            if output_key not in seen_intermediates:
                                                seen_intermediates.add(output_key)
                                                intermediate_outputs.append(self._format_intermediate_output(intermediate_data))
                                        
                                        # Check for multiple intermediate outputs (from Retrieve)
                                        if '_intermediates' in msg_content:
                                            for intermediate_data in msg_content['_intermediates']:
                                                output_key = self._create_intermediate_key(intermediate_data)
                                                
                                                if output_key not in seen_intermediates:
                                                    seen_intermediates.add(output_key)
                                                    intermediate_outputs.append(self._format_intermediate_output(intermediate_data))
                                except Exception as e:
                                    logger.debug(f"Failed to extract intermediate output: {e}")
            
            workflow_duration = time.time() - start
            logger.info(f"Read graph workflow completed in {workflow_duration}s")

            # Extract final answer
            final_answer = ""
            for messages in outputs:
                if messages['role'] == 'tool':
                    message = messages['content']
                    try:
                        message = json.loads(message) if isinstance(message, str) else message
                        if isinstance(message, dict) and message.get('status') != '':
                            summary_result = message.get('summary_result')
                            if summary_result:
                                final_answer = summary_result
                    except (json.JSONDecodeError, ValueError):
                        pass
            
            # 记录成功的操作
            total_duration = time.time() - start_time
            if audit_logger:
                audit_logger.log_operation(
                    operation="READ",
                    config_id=config_id,
                    group_id=group_id,
                    success=True,
                    duration=total_duration,
                    details={
                        "search_switch": search_switch,
                        "history_length": len(history),
                        "intermediate_outputs_count": len(intermediate_outputs),
                        "has_answer": bool(final_answer)
                    }
                )
            
            return {
                "answer": final_answer,
                "intermediate_outputs": intermediate_outputs
            }
    
    def _create_intermediate_key(self, output: Dict) -> str:
        """
        Create a unique key for an intermediate output to detect duplicates.
        
        Args:
            output: Intermediate output dictionary
            
        Returns:
            Unique string key for this output
        """
        output_type = output.get('type', 'unknown')
        
        if output_type == 'problem_split':
            # Use type + original query as key
            return f"split:{output.get('original_query', '')}"
        elif output_type == 'problem_extension':
            # Use type + original query as key
            return f"extension:{output.get('original_query', '')}"
        elif output_type == 'search_result':
            # Use type + query + index as key
            return f"search:{output.get('query', '')}:{output.get('index', 0)}"
        elif output_type == 'retrieval_summary':
            # Use type + query as key
            return f"summary:{output.get('query', '')}"
        elif output_type == 'verification':
            # Use type + query as key
            return f"verification:{output.get('query', '')}"
        elif output_type == 'input_summary':
            # Use type + query as key
            return f"input_summary:{output.get('query', '')}"
        else:
            # Fallback: use JSON representation
            import json
            return json.dumps(output, sort_keys=True)
    
    def _format_intermediate_output(self, output: Dict) -> Dict:
        """Format intermediate output for frontend display."""
        output_type = output.get('type', 'unknown')
        
        if output_type == 'problem_split':
            return {
                'type': 'problem_split',
                'title': '问题拆分',
                'data': output.get('data', []),
                'original_query': output.get('original_query', '')
            }
        elif output_type == 'problem_extension':
            return {
                'type': 'problem_extension',
                'title': '问题扩展',
                'data': output.get('data', {}),
                'original_query': output.get('original_query', '')
            }
        elif output_type == 'search_result':
            return {
                'type': 'search_result',
                'title': f'检索结果 ({output.get("index", 0)}/{output.get("total", 0)})',
                'query': output.get('query', ''),
                'raw_results': output.get('raw_results', ''),
                'index': output.get('index', 0),
                'total': output.get('total', 0)
            }
        elif output_type == 'retrieval_summary':
            return {
                'type': 'retrieval_summary',
                'title': '检索总结',
                'summary': output.get('summary', ''),
                'query': output.get('query', ''),
                'raw_results': output.get('raw_results'),

            }
        elif output_type == 'verification':
            return {
                'type': 'verification',
                'title': '数据验证',
                'result': output.get('result', 'unknown'),
                'reason': output.get('reason', ''),
                'query': output.get('query', ''),
                'verified_count': output.get('verified_count', 0)
            }
        elif output_type == 'input_summary':
            return {
                'type': 'input_summary',
                'title': '快速答案',
                'summary': output.get('summary', ''),
                'query': output.get('query', ''),
                'raw_results': output.get('raw_results'),

            }
        else:
            return output
    
    async def classify_message_type(self, message: str) -> Dict:
        """
        Determine the type of user message (read or write)
        
        Args:
            message: User message to classify
            
        Returns:
            Type classification result
        """
        logger.info("Classifying message type")
        
        status = await status_typle(message)
        logger.debug(f"Message type: {status}")
        return status
    
    # ==================== 新增的三个接口方法 ====================
    
    async def get_knowledge_type_stats(
        self,
        end_user_id: Optional[str] = None,
        only_active: bool = True,
        current_workspace_id: Optional[uuid.UUID] = None,
        db: Session = None
    ) -> Dict[str, Any]:
        """
        统计知识库类型分布，包含：
        1. PostgreSQL 中的知识库类型：General, Web, Third-party, Folder（根据 workspace_id 过滤）
        2. Neo4j 中的 memory 类型（仅统计 Chunk 数量，根据 end_user_id/group_id 过滤）
        3. total: 所有类型的总和
        
        参数：
        - end_user_id: 用户组ID（可选，未提供时 memory 统计为 0）
        - only_active: 是否仅统计有效记录
        - current_workspace_id: 当前工作空间ID（可选，未提供时知识库统计为 0）
        - db: 数据库会话
        
        返回格式：
        {
            "General": count,
            "Web": count,
            "Third-party": count,
            "Folder": count,
            "memory": chunk_count,
            "total": sum_of_all
        }
        """
        result = {}
        
        # 1. 统计 PostgreSQL 中的知识库类型
        try:
            if db is None:
                from app.db import get_db
                db_gen = get_db()
                db = next(db_gen)
            
            # 初始化所有标准类型为 0
            for kb_type in KnowledgeType:
                result[kb_type.value] = 0
            
            # 如果提供了 workspace_id，则按 workspace_id 过滤
            if current_workspace_id:
                # 构建查询条件
                query = db.query(
                    Knowledge.type,
                    func.count(Knowledge.id).label('count')
                ).filter(Knowledge.workspace_id == current_workspace_id)
                
                # 检查 Knowledge 模型是否有 status 字段
                if only_active and hasattr(Knowledge, 'status'):
                    query = query.filter(Knowledge.status == 1)
                
                # 按类型分组
                type_counts = query.group_by(Knowledge.type).all()
                
                # 只填充标准类型的统计值，忽略其他类型
                valid_types = {kb_type.value for kb_type in KnowledgeType}
                for type_name, count in type_counts:
                    if type_name in valid_types:
                        result[type_name] = count
                        
                logger.info(f"知识库类型统计成功 (workspace_id={current_workspace_id}): {result}")
            else:
                # 没有提供 workspace_id，所有知识库类型返回 0
                logger.info(f"未提供 workspace_id，知识库类型统计全部为 0")
                    
        except Exception as e:
            logger.error(f"知识库类型统计失败: {e}")
            raise Exception(f"知识库类型统计失败: {e}")
        
        # 2. 统计 Neo4j 中的 memory 总量（统计当前空间下所有宿主的 Chunk 总数）
        try:
            if current_workspace_id:
                # 获取当前空间下的所有宿主
                from app.repositories import app_repository, end_user_repository
                from app.schemas.app_schema import App as AppSchema
                from app.schemas.end_user_schema import EndUser as EndUserSchema
                
                # 查询应用并转换为 Pydantic 模型
                apps_orm = app_repository.get_apps_by_workspace_id(db, current_workspace_id)
                apps = [AppSchema.model_validate(h) for h in apps_orm]
                app_ids = [app.id for app in apps]
                
                # 获取所有宿主
                end_users = []
                for app_id in app_ids:
                    end_user_orm_list = end_user_repository.get_end_users_by_app_id(db, app_id)
                    end_users.extend([EndUserSchema.model_validate(h) for h in end_user_orm_list])
                
                # 统计所有宿主的 Chunk 总数
                total_chunks = 0
                for end_user in end_users:
                    end_user_id_str = str(end_user.id)
                    memory_query = """
                    MATCH (n:Chunk) WHERE n.group_id = $group_id RETURN count(n) AS Count
                    """
                    neo4j_result = await _neo4j_connector.execute_query(
                        memory_query,
                        group_id=end_user_id_str,
                    )
                    chunk_count = neo4j_result[0]["Count"] if neo4j_result else 0
                    total_chunks += chunk_count
                    logger.debug(f"EndUser {end_user_id_str} Chunk数量: {chunk_count}")
                
                result["memory"] = total_chunks
                logger.info(f"Neo4j memory统计成功: 总Chunk数={total_chunks}, 宿主数={len(end_users)}")
            else:
                # 没有 workspace_id 时，返回 0
                result["memory"] = 0
                logger.info(f"未提供 workspace_id，memory 统计为 0")
            
        except Exception as e:
            logger.error(f"Neo4j memory统计失败: {e}", exc_info=True)
            # 如果 Neo4j 查询失败，memory 设为 0
            result["memory"] = 0
        
        # 3. 计算知识库类型总和（不包括 memory）
        result["total"] = (
            result.get("General", 0) + 
            result.get("Web", 0) + 
            result.get("Third-party", 0) + 
            result.get("Folder", 0)
        )
        
        return result


    async def get_hot_memory_tags_by_user(
        self,
        end_user_id: Optional[str] = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        获取指定用户的热门记忆标签
        
        参数：
        - end_user_id: 用户ID（可选），对应Neo4j中的group_id字段
        - limit: 返回标签数量限制
        
        返回格式：
        [
            {"name": "标签名", "frequency": 频次},
            ...
        ]
        """
        try:
            # by_user=False 表示按 group_id 查询（在Neo4j中，group_id就是用户维度）
            tags = await get_hot_memory_tags(end_user_id, limit=limit, by_user=False)
            payload = [{"name": t, "frequency": f} for t, f in tags]
            return payload
        except Exception as e:
            logger.error(f"热门记忆标签查询失败: {e}")
            raise Exception(f"热门记忆标签查询失败: {e}")


    async def get_user_profile(
        self,
        end_user_id: Optional[str] = None,
        current_user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        获取用户详情，包含：
        1. 用户名字（直接使用 end_user_name)
        2. 用户标签（从摘要中用LLM总结3个标签）
        3. 热门记忆标签（从hot_memory_tags获取前4个）
        
        参数：
        - end_user_id: 用户ID（可选）
        - current_user_id: 当前登录用户的ID（保留参数）
        
        返回格式：
        {
            "name": "用户名",
            "tags": ["产品设计师", "旅行爱好者", "摄影发烧友"],
            "hot_tags": [
                {"name": "标签1", "frequency": 10},
                {"name": "标签2", "frequency": 8},
                ...
            ]
        }
        """
        result = {}
        
        # 1. 根据 end_user_id 获取 end_user_name
        try:
            if end_user_id:
                from app.repositories import end_user_repository
                from app.schemas.end_user_schema import EndUser as EndUserSchema
                
                end_user_orm = end_user_repository.get_end_user_by_id(db, end_user_id)
                if end_user_orm:
                    end_user = EndUserSchema.model_validate(end_user_orm)
                    end_user_name = end_user.other_name
                else:
                    end_user_name = "默认用户"
            else:
                end_user_name = "默认用户"
        except Exception as e:
            logger.error(f"Failed to get end_user_name: {e}")
            end_user_name = "默认用户"
             
        result["name"] = end_user_name
        logger.debug(f"The end_user is: {end_user_name}")
        
        # 2. 使用LLM从语句和实体中提取标签
        try:
            connector = Neo4jConnector()
            
            # 查询该用户的语句
            query = (
                "MATCH (s:Statement) "
                "WHERE ($group_id IS NULL OR s.group_id = $group_id) AND s.statement IS NOT NULL "
                "RETURN s.statement AS statement "
                "ORDER BY s.created_at DESC LIMIT 100"
            )
            rows = await connector.execute_query(query, group_id=end_user_id)
            statements = [r.get("statement", "") for r in rows if r.get("statement")]
            
            # 查询该用户的热门实体
            entity_query = (
                "MATCH (e:ExtractedEntity) "
                "WHERE ($group_id IS NULL OR e.group_id = $group_id) AND e.entity_type <> '人物' AND e.name IS NOT NULL "
                "RETURN e.name AS name, count(e) AS frequency "
                "ORDER BY frequency DESC LIMIT 20"
            )
            entity_rows = await connector.execute_query(entity_query, group_id=end_user_id)
            entities = [f"{r['name']} ({r['frequency']})" for r in entity_rows]
            
            await connector.close()
            
            if not statements:
                result["tags"] = []
            else:
                # 构建摘要文本
                summary_text = f"用户语句样本：{' | '.join(statements[:20])}\n核心实体：{', '.join(entities)}"
                logger.debug(f"User data found: {len(statements)} statements, {len(entities)} entities")
                
                # 使用LLM提取标签
                llm_client = get_llm_client()
                
                # 定义标签提取的结构
                class UserTags(BaseModel):
                    tags: list[str] = Field(..., description="3个描述用户特征的标签，如：产品设计师、旅行爱好者、摄影发烧友")
                
                messages = [
                    {
                        "role": "system",
                        "content": "你是一个信息提取助手。从用户的语句和实体中提取3个最能代表用户特征的标签。标签应该简洁（2-6个字），描述用户的职业、兴趣或特点。"
                    },
                    {
                        "role": "user",
                        "content": f"请从以下用户信息中提取3个标签：\n\n{summary_text}"
                    }
                ]
                
                user_tags = await llm_client.response_structured(
                    messages=messages,
                    response_model=UserTags
                )
                
                result["tags"] = user_tags.tags
                logger.debug(f"Extracted tags: {user_tags.tags}")
            
        except Exception as e:
            # 如果提取失败，使用默认值
            logger.error(f"Failed to extract user tags: {e}")
            result["tags"] = []
        
        try:
            # 3. 获取热门记忆标签（前4个）
            connector = Neo4jConnector()
            names_to_exclude = ['AI', 'Caroline', 'Melanie', 'Jon', 'Gina', '用户', 'AI助手', 'John', 'Maria']
            hot_tag_query = (
                "MATCH (e:ExtractedEntity) "
                "WHERE ($group_id IS NULL OR e.group_id = $group_id) AND e.entity_type <> '人物' "
                "AND e.name IS NOT NULL AND NOT e.name IN $names_to_exclude "
                "RETURN e.name AS name, count(e) AS frequency "
                "ORDER BY frequency DESC LIMIT 4"
            )
            hot_tag_rows = await connector.execute_query(
                hot_tag_query, 
                group_id=end_user_id, 
                names_to_exclude=names_to_exclude
            )
            await connector.close()
            
            result["hot_tags"] = [{"name": r["name"], "frequency": r["frequency"]} for r in hot_tag_rows]
            logger.debug(f"Hot tags found: {len(result['hot_tags'])} tags")
        except Exception as e:
            logger.error(f"Failed to get hot tags: {e}")
            result["hot_tags"] = []
        
        return result

    async def stream_log_content(self) -> AsyncGenerator[str, None]:
        """
        Stream log content in real-time using Server-Sent Events (SSE)

        This method establishes a streaming connection and transmits log entries
        as they are written to the log file. It uses the LogStreamer to watch
        the file and yields SSE-formatted messages.

        Yields:
            SSE-formatted strings with the following event types:
            - log: Contains log content and timestamp
            - keepalive: Periodic keepalive messages to maintain connection
            - error: Error information if streaming fails
            - done: Indicates streaming has completed

        Raises:
            FileNotFoundError: If log file doesn't exist at stream start
            Exception: For other unexpected errors during streaming
        """
        logger.info("Starting log content streaming")

        # Get log file path - use project root directory
        current_file = os.path.abspath(__file__)  # app/services/memory_agent_service.py
        app_dir = os.path.dirname(os.path.dirname(current_file))  # app directory
        project_root = os.path.dirname(app_dir)  # redbear-mem directory
        log_path = os.path.join(project_root, "logs", "agent_service.log")

        # Check if file exists before starting stream
        if not os.path.exists(log_path):
            logger.error(f"Log file not found: {log_path}")
            # Send error event in SSE format
            yield f"event: error\ndata: {json.dumps({'code': 4006, 'message': '日志文件不存在', 'error': f'File not found: {log_path}'})}\n\n"
            return

        streamer = None
        try:
            # Initialize LogStreamer with keepalive interval from settings (default 300 seconds)
            keepalive_interval = getattr(settings, 'LOG_STREAM_KEEPALIVE_INTERVAL', 300)
            streamer = LogStreamer(log_path, keepalive_interval=keepalive_interval)

            logger.info(f"LogStreamer initialized for {log_path}")

            # Stream log content using read_existing_and_stream to get all existing content first
            async for message in streamer.read_existing_and_stream():
                event_type = message.get("event")
                data = message.get("data")

                # Format as SSE message
                # SSE format: "event: <type>\ndata: <json_data>\n\n"
                sse_message = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

                logger.debug(f"Streaming event: {event_type}")
                yield sse_message

                # If error or done event, stop streaming
                if event_type in ["error", "done"]:
                    logger.info(f"Stream ended with event: {event_type}")
                    break

        except FileNotFoundError as e:
            logger.error(f"Log file not found during streaming: {e}")
            yield f"event: error\ndata: {json.dumps({'code': 4006, 'message': '日志文件在流式传输期间变得不可用', 'error': str(e)})}\n\n"

        except Exception as e:
            logger.error(f"Unexpected error during log streaming: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'code': 8001, 'message': '流式传输期间发生错误', 'error': str(e)})}\n\n"

        finally:
            # Resource cleanup
            logger.info("Log streaming completed, cleaning up resources")
            # LogStreamer uses context manager for file handling, so cleanup is automatic

# async def get_api_docs(self, file_path: Optional[str] = None) -> Dict[str, Any]:
#     """
#     Parse and return API documentation
    
#     Args:
#         file_path: Optional path to API docs file. If None, uses default path.
        
#     Returns:
#         Dict containing parsed API documentation or error information
#     """
#     try:
#         target = file_path or get_default_docs_path()
        
#         if not os.path.isfile(target):
#             return {
#                 "success": False,
#                 "msg": "API文档文件不存在",
#                 "error_code": "DOC_NOT_FOUND",
#                 "data": {"path": target}
#             }
        
#         data = parse_api_docs(target)
#         return {
#             "success": True,
#             "msg": "解析成功",
#             "data": data
#         }
#     except Exception as e:
#         logger.error(f"Failed to parse API docs: {e}")
#         return {
#             "success": False,
#             "msg": "解析失败",
#             "error_code": "DOC_PARSE_ERROR",
#             "data": {"error": str(e)}
#         }
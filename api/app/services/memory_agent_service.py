"""
Memory Agent Service

Handles business logic for memory agent operations including read/write services,
health checks, and message type classification.

TODO: Refactor get_end_user_connected_config
----------------------------------------------
1. Move get_end_user_connected_config to memory_config_service.py
2. Change return type from Dict[str, Any] (with config_id string) to full MemoryConfig model
3. This will eliminate the need for callers to call load_memory_config separately
4. Update all callers to use the new unified function
"""
import json
import os
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional
from uuid import UUID

import redis
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, load_only

from app.core.config import settings
from app.core.logging_config import get_config_logger, get_logger
from app.core.memory.agent.logger_file.log_streamer import LogStreamer
from app.core.memory.agent.utils.type_classifier import status_typle
from app.core.memory.analytics.hot_memory_tags import (
    generate_interest_distribution,
    get_interest_distribution,
)
from app.db import get_db_context, get_db_read
from app.models.knowledge_model import Knowledge, KnowledgeType
from app.repositories.end_user_repository import get_tenant_id_by_end_user_id
from app.repositories.neo4j.neo4j_connector import Neo4jConnector
from app.schemas.memory_agent_schema import Write_UserInput
from app.services.memory_config_service import MemoryConfigService

logger = get_logger(__name__)
config_logger = get_config_logger()

# Initialize Neo4j connector for analytics functions
_neo4j_connector = Neo4jConnector(shared_driver=True)


class MemoryAgentService:
    """Service for memory agent operations"""

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

        # Add database connection pool status
        try:
            from app.db import get_pool_status
            pool_status = get_pool_status()
            logger.info(f"Database pool status: {pool_status}")

            # Check if pool usage is too high
            if pool_status.get("usage_percent", 0) > 80:
                logger.warning(f"High database pool usage: {pool_status['usage_percent']}%")
                status = "warning"

        except Exception as e:
            logger.error(f"Failed to get pool status: {e}")
            pool_status = {"error": str(e)}

        logger.info(f"Health status: {status}")
        return {
            "status": status,
            "database_pool": pool_status
        }

    def get_log_content(self) -> str:
        """
        Read and return agent service log file content

        Returns cleaned log content using the same cleaning logic as transmission mode

        Returns cleaned log content using the same cleaning logic as transmission mode
        """
        logger.info("Reading log file")

        # Get log file path - use project root directory
        from pathlib import Path
        project_root = str(Path(__file__).resolve().parents[2])  # api directory
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
        from pathlib import Path
        project_root = str(Path(__file__).resolve().parents[2])  # api directory
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

    def get_messages_list(self, user_input: Write_UserInput) -> list[dict]:
        """
        Get standardized message list from user input.
        
        Args:
            user_input: Write_UserInput object
        
        Returns:
            list[dict]: Message list, each message contains role, content, and optionally files
            
        Raises:
            ValueError: If messages is empty or format is incorrect
        """
        from app.core.logging_config import get_api_logger
        logger = get_api_logger()

        if len(user_input.messages) == 0:
            logger.error("Validation failed: Message list cannot be empty")
            raise ValueError("Message list cannot be empty")

        result = []
        for idx, msg in enumerate(user_input.messages):
            if msg.role not in ['user', 'assistant']:
                logger.error(f"Validation failed: Message {idx} invalid role: {msg.role}")
                raise ValueError(f"Role must be 'user' or 'assistant', got: {msg.role}. Message index: {idx}")

            if not msg.content or not msg.content.strip():
                logger.error(f"Validation failed: Message {idx} content is empty")
                raise ValueError(f"Message content cannot be empty. Message index: {idx}, role: {msg.role}")

            msg_dict = {"role": msg.role, "content": msg.content}
            if msg.dialog_at:
                msg_dict["dialog_at"] = msg.dialog_at
            if msg.files:
                msg_dict["files"] = [f.model_dump(exclude_none=True) for f in msg.files]
            result.append(msg_dict)

        logger.info(f"Validation successful: Structured message list, count: {len(result)}")
        return result

    async def classify_message_type(
            self,
            message: str,
            config_id: UUID,
            db: Session,
            workspace_id: Optional[UUID] = None
    ) -> Dict:
        """
        Determine the type of user message (read or write)
        Updated to eliminate global variables in favor of explicit parameters.

        Args:
            message: User message to classify
            config_id: Configuration ID to load LLM model from database
            db: Database session
            workspace_id: Workspace ID for fallback lookup (optional)

        Returns:
            Type classification result
        """
        logger.info("Classifying message type")

        # Load configuration to get LLM model ID
        config_service = MemoryConfigService(db)
        memory_config = config_service.load_memory_config(
            config_id=config_id
        )

        status = await status_typle(message, memory_config.llm_model_id, tenant_id=memory_config.tenant_id)
        logger.debug(f"Message type: {status}")
        return status

    async def generate_summary_from_retrieve(
            self,
            end_user_id: str,
            retrieve_info: str,
            history: List[Dict],
            query: str,
            config_id: str,
    ) -> str:
        """
        基于检索信息、历史对话和查询生成最终答案
        
        使用 Retrieve_Summary_prompt.jinja2 模板调用大模型生成答案
        
        Args:
            retrieve_info: 检索到的信息
            history: 历史对话记录
            query: 用户查询
            config_id: 配置ID
            
        Returns:
            生成的答案文本
        """
        # Always get workspace_id from end_user for fallback, even if config_id is provided
        with get_db_read() as db:
            try:
                config_service = MemoryConfigService(db)
                config_id = config_service.get_config_id_by_end_user(end_user_id)
                logger.info(f"Resolved config from end_user: config_id = {config_id}")

                memory_config = config_service.load_memory_config(
                    config_id=config_id
                )
                tenant_id = get_tenant_id_by_end_user_id(db, end_user_id)
                model_config = config_service.get_model_config(str(memory_config.llm_model_id), tenant_id)
            except Exception as e:
                if "No memory configuration found" in str(e):
                    raise  # Re-raise our specific error
                logger.error(f"Failed to get connected config for end_user_id {end_user_id}: {e}")
                if config_id is None:
                    raise ValueError(f"Unable to determine memory configuration for end_user_id {end_user_id}: {e}")
                # If config_id was provided, continue without workspace_id fallback
                raise e

        logger.info(f"Generating summary from retrieve info for query: {query[:50]}...")

        try:
            from app.core.models import RedBearLLM, RedBearModelConfig
            from app.core.memory.agent.utils.llm_tools import PROJECT_ROOT_
            from app.core.memory.agent.utils.template_tools import TemplateService
            from app.models.models_model import ModelType

            llm = RedBearLLM(
                RedBearModelConfig(
                    model_name=model_config["model_name"],
                    provider=model_config["provider"],
                    api_key=model_config["api_key"],
                    base_url=model_config["base_url"],
                    is_omni=model_config["is_omni"],
                    capability=model_config["capability"],
                ),
                type=ModelType.CHAT
            )

            template_root = os.path.join(PROJECT_ROOT_, 'memory', 'agent', 'utils', 'prompt')
            template_service = TemplateService(template_root)
            system_prompt = await template_service.render_template(
                template_name='direct_summary_prompt.jinja2',
                operation_name='retrieve_summary',
                query=query,
                history=history,
                retrieve_info=retrieve_info
            )

            from langchain_core.prompts import ChatPromptTemplate
            prompt = ChatPromptTemplate.from_template("{input}")
            chain = prompt | llm
            response = await chain.ainvoke({"input": system_prompt})
            answer = response.content if hasattr(response, 'content') else str(response)

            logger.info(f"Successfully generated summary: {answer[:100] if answer else 'None'}...")
            return answer if answer else "信息不足，无法回答。"

        except Exception as e:
            logger.error(f"生成摘要失败: {str(e)}", exc_info=True)
            return "信息不足，无法回答。"

    async def get_knowledge_type_stats(
            self,
            db: Session,
            end_user_id: Optional[str] = None,
            only_active: bool = True,
            current_workspace_id: Optional[uuid.UUID] = None
    ) -> Dict[str, Any]:
        """
        统计知识库类型分布，包含：
        1. PostgreSQL 中的知识库类型：General, Web, Third-party, Folder（根据 workspace_id 过滤）
        2. total: 所有类型的总和

        参数：
        - end_user_id: 用户组ID（可选，保留参数以保持接口兼容性）
        - only_active: 是否仅统计有效记录
        - current_workspace_id: 当前工作空间ID（可选，未提供时知识库统计为 0）
        - db: 数据库会话

        返回格式：
        {
            "General": count,
            "Web": count,
            "Third-party": count,
            "Folder": count,
            "total": sum_of_all
        }
        """
        result = {}

        # 1. 统计 PostgreSQL 中的知识库类型
        try:
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
                logger.info("未提供 workspace_id，知识库类型统计全部为 0")

        except Exception as e:
            logger.error(f"知识库类型统计失败: {e}")
            raise Exception(f"知识库类型统计失败: {e}")

        # 2. 统计 Neo4j 中的 memory 总量已移除
        # memory 字段不再返回

        # 3. 计算知识库类型总和（不包括 memory）
        result["total"] = (
                result.get("General", 0) +
                result.get("Web", 0) +
                result.get("Third-party", 0) +
                result.get("Folder", 0)
        )

        return result

    async def get_interest_distribution_by_user(
            self,
            end_user_id: Optional[str] = None,
            limit: int = 5,
            language: str = "zh"
    ) -> List[Dict[str, Any]]:
        """
        获取指定用户的兴趣分布标签。
        
        与热门标签不同，此接口专注于识别用户的兴趣活动（运动、爱好、学习等），
        过滤掉纯物品、工具、地点等不代表用户主动参与活动的名词。

        参数：
        - end_user_id: 用户ID（必填）
        - limit: 返回标签数量限制
        - language: 输出语言（"zh" 中文, "en" 英文）

        返回格式：
        [
            {"name": "兴趣活动名", "frequency": 频次},
            ...
        ]
        """
        try:
            tags = await get_interest_distribution(end_user_id, limit=limit, by_user=False, language=language)
            return [{"name": tag, "frequency": freq} for tag, freq in tags]
        except Exception as e:
            logger.error(f"兴趣分布标签查询失败: {e}")
            raise Exception(f"兴趣分布标签查询失败: {e}")

    async def generate_interest_distribution_by_user(
            self,
            end_user_id: Optional[str] = None,
            limit: int = 5,
            language: str = "zh",
    ) -> tuple[List[Dict[str, Any]], bool]:
        """生成兴趣分布，并返回是否应写入缓存。"""
        try:
            generation = await generate_interest_distribution(
                end_user_id=end_user_id,
                limit=limit,
                by_user=False,
                language=language,
            )
            items = [
                {"name": name, "frequency": frequency}
                for name, frequency in generation.items
            ]
            return items, generation.cacheable
        except Exception as e:
            error_type = type(e).__name__
            logger.error(
                "兴趣分布标签生成失败: error_type=%s error=%r",
                error_type,
                e,
            )
            raise Exception(
                f"兴趣分布标签生成失败: {error_type}: {e!r}"
            ) from e

    async def get_user_profile(
            self,
            end_user_id,
            current_user_id: Optional[str] = None,
            llm_id: Optional[str] = None,
            tenant_id=None,
            db: Session = None
    ) -> Dict[str, Any]:
        """
        获取用户详情，包含：
        1. 用户名字（直接使用 end_user_name)
        2. 用户标签（从摘要中用LLM总结3个标签）
        3. 热门记忆标签（从hot_memory_tags获取前4个）

        参数：
        - end_user_id: 用户ID（可选）
        - current_user_id: 当前登录用户的ID（保留参数）
        - llm_id: LLM模型ID（用于生成标签，可选，如果不提供则跳过标签生成）
        - db: 数据库会话（可选）

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
            if end_user_id and db:
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
                "WHERE ($end_user_id IS NULL OR s.end_user_id = $end_user_id) AND s.statement IS NOT NULL "
                "RETURN s.statement AS statement "
                "ORDER BY s.created_at DESC LIMIT 100"
            )
            rows = await connector.execute_query(query, end_user_id=end_user_id)
            statements = [r.get("statement", "") for r in rows if r.get("statement")]

            # 查询该用户的热门实体
            entity_query = (
                "MATCH (e:ExtractedEntity) "
                "WHERE ($end_user_id IS NULL OR e.end_user_id = $end_user_id) AND e.entity_type <> '生命体' AND e.name IS NOT NULL "
                "RETURN e.name AS name, count(e) AS frequency "
                "ORDER BY frequency DESC LIMIT 20"
            )
            entity_rows = await connector.execute_query(entity_query, end_user_id=end_user_id)
            entities = [f"{r['name']} ({r['frequency']})" for r in entity_rows]

            await connector.close()

            if not statements or not llm_id:
                result["tags"] = []
                if not llm_id and statements:
                    logger.warning("llm_id not provided, skipping tag generation")
            else:
                # 构建摘要文本
                summary_text = f"用户语句样本：{' | '.join(statements[:20])}\n核心实体：{', '.join(entities)}"
                logger.debug(f"User data found: {len(statements)} statements, {len(entities)} entities")

                # 使用LLM提取标签
                with get_db_context() as db:
                    from app.core.memory.pipelines.base_pipeline import ModelClientMixin
                    llm_client = ModelClientMixin.get_llm_client(db, uuid.UUID(llm_id), tenant_id=tenant_id)

                # 定义标签提取的结构
                class UserTags(BaseModel):
                    tags: list[str] = Field(...,
                                            description="3个描述用户特征的标签，如：产品设计师、旅行爱好者、摄影发烧友")

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

                user_tags = await llm_client.call_structured(messages, UserTags)

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
                "WHERE ($end_user_id IS NULL OR e.end_user_id = $end_user_id) AND e.entity_type <> '生命体' "
                "AND e.name IS NOT NULL AND NOT e.name IN $names_to_exclude "
                "RETURN e.name AS name, count(e) AS frequency "
                "ORDER BY frequency DESC LIMIT 4"
            )
            hot_tag_rows = await connector.execute_query(
                hot_tag_query,
                end_user_id=end_user_id,
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
        from pathlib import Path
        project_root = str(Path(__file__).resolve().parents[2])  # api directory
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


# TODO: move to memory_config_service.py
def get_end_user_connected_config(end_user_id: str, db: Session) -> Dict[str, Any]:
    """
    获取终端用户所在工作空间当前生效的记忆配置。

    Args:
        end_user_id: 终端用户ID
        db: 数据库会话

    Returns:
        包含 memory_config_id, workspace_id 和相关信息的字典

    Raises:
        ValueError: 当终端用户不存在或应用未发布时
    """
    from app.models.app_model import App
    from app.repositories.end_user_repository import EndUserRepository
    from app.services.memory_config_service import MemoryConfigService

    logger.info(f"Getting workspace memory config for end_user_id: {end_user_id}")

    # TODO: check sources for enduserid, should be one of these three: chat, draft, apikey
    # 1. 获取 end_user 及其 app_id
    end_user = EndUserRepository(db).get_end_user_by_id(UUID(end_user_id))
    if not end_user:
        logger.warning(f"End user not found: {end_user_id}")
        raise ValueError(f"终端用户不存在: {end_user_id}")

    app_id = end_user.app_id
    logger.debug(f"Found end_user app_id: {app_id}")

    # 2. 获取应用以确定 workspace_id
    # app_id 为 None 是合法状态（例如 service-API-key 创建的 end_user），
    # 后续会通过 end_user.workspace_id 走 workspace 默认 config 兜底。
    # 仅在 app_id 有值但查不到 App 行时才告警。
    app = None
    if app_id:
        app = db.query(App).filter(App.id == app_id).first()
        if not app:
            # 孤儿 end_user（app_id 指向已删除的 App）：降级为 debug，
            # 不影响主流程，仍会通过 end_user.workspace_id 走 workspace 默认 config 兜底。
            logger.debug(f"App not found: {app_id}")
    # TODO: temp fix for draft run
    # if not app.current_release_id:
    #     logger.warning(f"No current release for app: {app_id}")
    #     raise ValueError(f"应用未发布: {app_id}")

    # 3. 统一按工作空间生效配置获取记忆配置
    memory_config_service = MemoryConfigService(db)
    memory_config = memory_config_service.get_config_with_fallback(
        memory_config_id=None,
        workspace_id=end_user.workspace_id
    )

    memory_config_id = str(memory_config.config_id) if memory_config else None

    result = {
        "end_user_id": str(end_user_id),
        "memory_config_id": memory_config_id,
        "workspace_id": str(end_user.workspace_id)
    }

    logger.info(
        f"Successfully retrieved workspace memory config: memory_config_id = {memory_config_id}, "
        f"workspace_id = {end_user.workspace_id}"
    )
    return result


def get_end_users_connected_configs_batch(end_user_ids: List[str], db: Session) -> Dict[str, Dict[str, Any]]:
    """
    批量获取多个终端用户关联的记忆配置。

    逻辑：
    1. 统一使用工作空间生效记忆配置

    实现说明：
    - 之前是 4 次串行查询（end_users → apps → 直接配置 → 工作空间默认配置），
      冷链路上的 RT 累计成本较高；
    - 现在合并为最多 2 次：
        a) 一次 JOIN：EndUser LEFT JOIN App，一次性拿到 workspace_id
        b) 一次 MemoryConfig 查询：按 workspace_id 取工作空间默认配置。

    Args:
        end_user_ids: 终端用户ID列表
        db: 数据库会话

    Returns:
        字典，key 为 end_user_id，value 为包含 memory_config_id 和 memory_config_name 的字典
    """
    from sqlalchemy import and_

    from app.models.memory_config_model import MemoryConfig
    from app.repositories.end_user_repository import EndUserRepository

    logger.info(f"Batch getting connected configs for {len(end_user_ids)} end_users")

    result: Dict[str, Dict[str, Any]] = {}

    if not end_user_ids:
        return result

    # 1) 一次 JOIN 拿齐 (end_user_id, workspace_id)
    repo = EndUserRepository(db)
    rows = repo.get_config_batch_by_ids([UUID(uid) if isinstance(uid, str) else uid for uid in end_user_ids])

    # found_ids 用于补齐"未找到的用户"
    found_ids = set()
    workspace_ids: set = set()

    # row_index: end_user_id -> workspace_id
    row_index: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        end_user_id = str(row.end_user_id)
        found_ids.add(end_user_id)

        workspace_id = row.app_workspace_id or row.end_user_workspace_id
        row_index[end_user_id] = {
            "workspace_id": workspace_id,
        }

        if workspace_id:
            workspace_ids.add(workspace_id)

    # 未找到的用户直接补空结果
    for missing_id in set(end_user_ids) - found_ids:
        result[missing_id] = {"memory_config_id": None, "memory_config_name": None}

    # 2) 一次 MemoryConfig 查询：工作空间默认配置
    workspace_default_configs: Dict[Any, Any] = {}

    if workspace_ids:
        configs = (
            db.query(MemoryConfig)
            .options(load_only(
                MemoryConfig.config_id,
                MemoryConfig.config_name,
                MemoryConfig.workspace_id,
                MemoryConfig.is_default,
                MemoryConfig.state,
            ))
            .filter(
                and_(
                    MemoryConfig.workspace_id.in_(workspace_ids),
                    MemoryConfig.is_default.is_(True),
                    MemoryConfig.state.is_(True),
                )
            )
            .all()
        )

        for cfg in configs:
            if cfg.is_default and cfg.state and cfg.workspace_id in workspace_ids:
                workspace_default_configs[cfg.workspace_id] = cfg

    # 3) 拼装最终结果
    for end_user_id, data in row_index.items():
        memory_config = workspace_default_configs.get(data["workspace_id"])

        if memory_config:
            result[end_user_id] = {
                "memory_config_id": str(memory_config.config_id),
                "memory_config_name": memory_config.config_name,
            }
        else:
            result[end_user_id] = {"memory_config_id": None, "memory_config_name": None}

    logger.info(f"Successfully retrieved {len(result)} connected configs")
    return result

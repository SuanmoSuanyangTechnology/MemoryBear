"""
试运行服务

提供 Agent 试运行功能，允许用户在不发布应用的情况下测试配置。
"""
import asyncio
import datetime
import json
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.rag.nlp.search import knowledge_retrieval
from app.models import AgentConfig, ModelApiKey, ModelConfig
from app.schemas.prompt_schema import PromptMessageRole, render_prompt_message
from app.services.langchain_tool_server import Search
from app.services.memory_agent_service import MemoryAgentService
from app.services.model_parameter_merger import ModelParameterMerger
from langchain.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

logger = get_business_logger()
class KnowledgeRetrievalInput(BaseModel):
    """知识库检索工具输入参数"""
    query: str = Field(description="需要检索的问题或关键词")


class WebSearchInput(BaseModel):
    """网络搜索工具输入参数"""
    query: str = Field(description="需要搜索的问题或关键词")


class LongTermMemoryInput(BaseModel):
    """长期记忆工具输入参数"""
    question: str = Field(description="经过优化重写的查询问题。请将用户的原始问题重写为更合适的检索形式，包含关键词，上下文和具体描述，注意错词检查并且改写")

def create_long_term_memory_tool(memory_config: Dict[str, Any], end_user_id: str, storage_type: Optional[str] = None,user_rag_memory_id: Optional[str] = None):
    """创建记忆工具,


    Args:
        memory_config: 记忆配置
        end_user_id: 用户ID
        storage_type: 存储类型（可选）

    Returns:
        长期记忆工具
    """
    # search_switch = memory_config.get("search_switch", "2")
    config_id= memory_config.get("memory_content",'17')
    logger.info(f"创建长期记忆工具，配置: end_user_id={end_user_id}, config_id={config_id}, storage_type={storage_type}")
    @tool(args_schema=LongTermMemoryInput)
    def long_term_memory(question: str) -> str:
        """
        从用户的历史记忆中检索相关信息。这是一个强大的工具，可以帮助你了解用户的背景、偏好和历史对话内容。
        
         以下场景不需要使用此工具：
        1. 情绪/社交问候场景（如"你好"、"谢谢"、"再见"等简单寒暄）
        2. 纯任务性场景（如"帮我写代码"、"翻译这段文字"等不需要历史上下文的任务）
        3. 处理外部内容时（如用户提供的文本、代码、RAG数据等，这些内容本身已经包含所需信息）
        
        除上述场景外的所有其他情况都应该使用此工具，特别是：
        - 用户询问个人信息或历史对话内容
        - 需要了解用户偏好、习惯或背景
        - 用户提到"之前"、"上次"、"记得"等涉及历史的词汇
        - 需要个性化回复或基于历史上下文的建议
        - 用户询问关于自己的任何信息

         需要对question改写/优化：
            需要重点关注一以下几点
            - 相关的关键词，保持原问题的核心语义不变， 根据上下文，使问题更具体、更清晰，将模糊的表达转换为明确的搜索词
            - 使用同义词或相关术语扩展查询
        Args:
            question: question改写之后的内容

        Returns:
            检索到的历史记忆内容
        """
        logger.info(f" 长期记忆工具被调用！question={question}, user={end_user_id}")
        try:
            from app.db import get_db
            db = next(get_db())
            try:
                memory_content = asyncio.run(
                    MemoryAgentService().read_memory(
                        group_id=end_user_id,
                        message=question,
                        history=[],
                        search_switch="1",
                        config_id=config_id,
                        db=db,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id
                    )
                )
            finally:
                db.close()
            logger.info(f'用户ID：Agent:{end_user_id}')
            logger.debug("调用长期记忆 API", extra={"question": question, "end_user_id": end_user_id})

            logger.info(
                "长期记忆检索成功",
                extra={
                    "end_user_id": end_user_id,
                    "content_length": len(str(memory_content))
                }
            )

            return f"检索到以下历史记忆：\n\n{memory_content}"
        except Exception as e:
            logger.error("长期记忆检索失败", extra={"error": str(e), "error_type": type(e).__name__})
            return f"记忆检索失败: {str(e)}"
    return long_term_memory


def create_web_search_tool(web_search_config: Dict[str, Any]):
    """创建网络搜索工具

    Args:
        web_search_config: 网络搜索配置

    Returns:
        网络搜索工具
    """
    logger.info("创建网络搜索工具")

    @tool(args_schema=WebSearchInput)
    def web_search_tool(query: str) -> str:
        """从互联网搜索最新信息。当用户的问题需要实时信息、最新新闻或网络资料时，使用此工具进行搜索。

        Args:
            query: 需要搜索的问题或关键词

        Returns:
            搜索到的相关网络信息
        """
        try:
            logger.info(f"执行网络搜索: {query}")

            # 调用搜索服务
            search_result = Search(query)
            logger.info(
                "网络搜索成功",
                extra={
                    "query": query,
                    "result_length": len(search_result)
                }
            )

            return f"搜索到以下网络信息：\n\n{search_result}"

        except Exception as e:
            logger.error("网络搜索失败", extra={"error": str(e), "error_type": type(e).__name__})
            return f"搜索失败: {str(e)}"

    return web_search_tool


def create_knowledge_retrieval_tool(kb_config,kb_ids,user_id):
    """从知识库中检索相关信息。当用户的问题需要参考知识库、文档或历史记录时，使用此工具进行检索。

    Args:
        query: 需要检索的问题或关键词

    Returns:
        检索到的相关知识内容
    """
    logger.info(f"创建知识库检索工具，用户：{user_id}")
    @tool(args_schema=KnowledgeRetrievalInput)
    def knowledge_retrieval_tool(query: str) -> str:
        """从知识库中检索相关信息。当用户的问题需要参考知识库、文档或历史记录时，使用此工具进行检索。

        Args:
            query: 需要检索的问题或关键词

        Returns:
            检索到的相关知识内容
        """


        try:

            retrieve_chunks_result = knowledge_retrieval(query, kb_config)
            if retrieve_chunks_result:
                retrieval_knowledge = [i.page_content for i in retrieve_chunks_result]
                context = '\n\n'.join(retrieval_knowledge)
                logger.info(
                    "知识库检索成功",
                    extra={
                        "kb_ids": kb_ids,
                        "result_count": len(retrieval_knowledge),
                        "total_length": len(context)
                    }
                )

                return f"检索到以下相关信息：\n\n{context}"
            else:
                logger.warning("知识库检索未找到结果")
                return "未找到相关信息"
        except Exception as e:
            logger.error("知识库检索失败", extra={"error": str(e), "error_type": type(e).__name__})
            return f"检索失败: {str(e)}"

    return knowledge_retrieval_tool

class DraftRunService:
    """试运行服务类"""

    def __init__(self, db: Session):
        """初始化试运行服务

        Args:
            db: 数据库会话
        """
        self.db = db

    async def run(
        self,
        *,
        agent_config: AgentConfig,
        model_config: ModelConfig,
        message: str,
        workspace_id: uuid.UUID,
        conversation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        storage_type: Optional[str] = None,
        user_rag_memory_id: Optional[str] = None,
        web_search: bool = True,
        memory: bool = True
    ) -> Dict[str, Any]:
        """执行试运行（使用 LangChain Agent）

        Args:
            agent_config: Agent 配置
            model_config: 模型配置
            message: 用户消息
            workspace_id: 工作空间ID（必须，用于会话隔离）
            conversation_id: 会话ID（用于多轮对话）
            user_id: 用户ID
            variables: 自定义变量参数值

        Returns:
            Dict: 包含 AI 回复和元数据的字典
        """
        memory_flag=False

        print('===========',storage_type)

        print(user_id)
        if variables == None: variables = {}
        from app.core.agent.langchain_agent import LangChainAgent

        start_time = time.time()

        try:
            # 1. 获取 API Key 配置
            api_key_config = await self._get_api_key(model_config.id)
            logger.debug(
                "API Key 配置获取成功",
                extra={
                    "model_name": api_key_config["model_name"],
                    "has_api_key": bool(api_key_config["api_key"]),
                    "has_api_base": bool(api_key_config.get("api_base"))
                }
            )

            # 2. 合并模型参数
            effective_params = ModelParameterMerger.get_effective_parameters(
                model_config=model_config,
                agent_config=agent_config
            )


            items_params=variables
            system_prompt = render_prompt_message(
                agent_config.system_prompt,  # 修正拼写错误
                PromptMessageRole.USER,
                items_params
            )

            # 3. 处理系统提示词（支持变量替换）
            system_prompt = system_prompt.get_text_content() or "你是一个专业的AI助手"
            print('系统提示词：',system_prompt)

            # 4. 准备工具列表
            tools = []

            # 添加网络搜索工具
            if web_search:
                if agent_config.tools:
                    web_search_config = agent_config.tools.get("web_search", {})
                    web_search_enable = web_search_config.get("enabled", False)

                    if web_search_enable:
                        logger.info("网络搜索已启用")
                        # 创建网络搜索工具
                        search_tool = create_web_search_tool(web_search_config)
                        tools.append(search_tool)

                        logger.debug(
                            "已添加网络搜索工具",
                            extra={
                                "tool_count": len(tools)
                            }
                        )

            # 添加知识库检索工具
            if agent_config.knowledge_retrieval:
                kb_config = agent_config.knowledge_retrieval
                knowledge_bases = kb_config.get("knowledge_bases", [])
                kb_ids = bool(knowledge_bases and knowledge_bases[0].get("kb_id"))
                if kb_ids:
                    # 创建知识库检索工具
                    kb_tool = create_knowledge_retrieval_tool(kb_config,kb_ids,user_id)
                    tools.append(kb_tool)

                    logger.debug(
                        "已添加知识库检索工具",
                        extra={
                            "kb_ids": kb_ids,
                            "tool_count": len(tools)
                        }
                    )

            # 添加长期记忆工具
            if memory:
                if agent_config.memory and agent_config.memory.get("enabled"):
                    memory_flag=True

                    memory_config = agent_config.memory
                    if user_id:
                        # 创建长期记忆工具
                        memory_tool = create_long_term_memory_tool(memory_config, user_id,storage_type,user_rag_memory_id)
                        tools.append(memory_tool)

                        logger.debug(
                            "已添加长期记忆工具",
                            extra={
                                "user_id": user_id,
                                "tool_count": len(tools)
                            }
                        )

            # 4. 创建 LangChain Agent
            agent = LangChainAgent(
                model_name=api_key_config["model_name"],
                api_key=api_key_config["api_key"],
                provider=api_key_config.get("provider", "openai"),
                api_base=api_key_config.get("api_base"),
                temperature=effective_params.get("temperature", 0.7),
                max_tokens=effective_params.get("max_tokens", 2000),
                system_prompt=system_prompt,
                tools=tools,
            )

            # 5. 处理会话ID（创建或验证）
            conversation_id = await self._ensure_conversation(
                conversation_id=conversation_id,
                app_id=agent_config.app_id,
                workspace_id=workspace_id,
                user_id=user_id
            )

            # 6. 加载历史消息
            history = []
            if agent_config.memory and agent_config.memory.get("enabled"):
                history = await self._load_conversation_history(
                    conversation_id=conversation_id,
                    max_history=agent_config.memory.get("max_history", 10)
                )

            # 6. 知识库检索
            context = None

            logger.debug(
                "准备调用 LangChain Agent",
                extra={
                    "model": api_key_config["model_name"],
                    "has_history": bool(history),
                    "has_context": bool(context)
                }
            )

            memory_config_= agent_config.memory
            config_id = memory_config_.get("memory_content")

            # 7. 调用 Agent
            result = await agent.chat(
                message=message,
                history=history,
                context=context,
                end_user_id=user_id,
                config_id=config_id,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                memory_flag=memory_flag
            )

            elapsed_time = time.time() - start_time

            # 8. 保存会话消息
            if agent_config.memory and agent_config.memory.get("enabled"):
                await self._save_conversation_message(
                    conversation_id=conversation_id,
                    user_message=message,
                    assistant_message=result["content"],
                    app_id=agent_config.app_id,
                    user_id=user_id
                )

            response = {
                "message": result["content"],
                "conversation_id": conversation_id,
                "usage": result.get("usage", {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }),
                "elapsed_time": elapsed_time
            }

            logger.info(
                "试运行完成",
                extra={
                    "model": model_config.name,
                    "elapsed_time": elapsed_time,
                    "message_length": len(result["content"]),
                    "total_tokens": result.get("usage", {}).get("total_tokens", 0)
                }
            )

            return response

        except Exception as e:
            logger.error("LangChain Agent 调用失败", extra={"error": str(e), "error_type": type(e).__name__})
            raise BusinessException(f"Agent 调用失败: {str(e)}", BizCode.INTERNAL_ERROR, cause=e)

    async def run_stream(
        self,
        *,
        agent_config: AgentConfig,
        model_config: ModelConfig,
        message: str,
        workspace_id: uuid.UUID,
        conversation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        storage_type: Optional[str] = None,
        user_rag_memory_id: Optional[str] = None,
        web_search: bool = True,  # 布尔类型默认值
        memory: bool = True  # 布尔类型默认值

    ) -> AsyncGenerator[str, None]:
        """执行试运行（流式返回，使用 LangChain Agent）

        Args:
            agent_config: Agent 配置
            model_config: 模型配置
            message: 用户消息
            workspace_id: 工作空间ID（必须，用于会话隔离）
            conversation_id: 会话ID（用于多轮对话）
            user_id: 用户ID
            variables: 自定义变量参数值

        Yields:
            str: SSE 格式的事件数据
        """
        memory_flag=False
        if variables==None:variables={}

        from app.core.agent.langchain_agent import LangChainAgent

        start_time = time.time()

        try:
            # 1. 获取 API Key 配置
            api_key_config = await self._get_api_key(model_config.id)

            # 2. 合并模型参数
            effective_params = ModelParameterMerger.get_effective_parameters(
                model_config=model_config,
                agent_config=agent_config
            )

            items_params=variables

            system_prompt = render_prompt_message(
                agent_config.system_prompt,  # 修正拼写错误
                PromptMessageRole.USER,
                items_params
            )

            # 3. 处理系统提示词（支持变量替换）
            system_prompt = system_prompt.get_text_content() or "你是一个专业的AI助手"

            # 4. 准备工具列表
            tools = []

            # 添加网络搜索工具
            if web_search:
                if agent_config.tools:
                    web_search_config = agent_config.tools.get("web_search", {})
                    web_search_enable = web_search_config.get("enabled", False)

                    if web_search_enable:
                        logger.info("网络搜索已启用")
                        # 创建网络搜索工具
                        search_tool = create_web_search_tool(web_search_config)
                        tools.append(search_tool)

                        logger.debug(
                            "已添加网络搜索工具",
                            extra={
                                "tool_count": len(tools)
                            }
                        )

            # 添加知识库检索工具
            if agent_config.knowledge_retrieval:
                kb_config = agent_config.knowledge_retrieval
                knowledge_bases = kb_config.get("knowledge_bases", [])
                kb_ids = bool(knowledge_bases and knowledge_bases[0].get("kb_id"))
                if kb_ids:
                    # 创建知识库检索工具
                    kb_tool = create_knowledge_retrieval_tool(kb_config, kb_ids, user_id)
                    tools.append(kb_tool)

                    logger.debug(
                        "已添加知识库检索工具",
                        extra={
                            "kb_ids": kb_ids,
                            "tool_count": len(tools)
                        }
                    )
            # 添加长期记忆工具
            if memory:
                if agent_config.memory and agent_config.memory.get("enabled"):
                    memory_flag= True
                    memory_config = agent_config.memory
                    if user_id:
                        # 创建长期记忆工具
                        memory_tool = create_long_term_memory_tool(memory_config, user_id,storage_type,user_rag_memory_id)
                        tools.append(memory_tool)

                        logger.debug(
                            "已添加长期记忆工具",
                            extra={
                                "user_id": user_id,
                                "tool_count": len(tools)
                            }
                        )


            # 4. 创建 LangChain Agent
            agent = LangChainAgent(
                model_name=api_key_config["model_name"],
                api_key=api_key_config["api_key"],
                provider=api_key_config.get("provider", "openai"),
                api_base=api_key_config.get("api_base"),
                temperature=effective_params.get("temperature", 0.7),
                max_tokens=effective_params.get("max_tokens", 2000),
                system_prompt=system_prompt,
                tools=tools,
                streaming=True
            )

            # 5. 处理会话ID（创建或验证）
            conversation_id = await self._ensure_conversation(
                conversation_id=conversation_id,
                app_id=agent_config.app_id,
                workspace_id=workspace_id,
                user_id=user_id
            )

            # 6. 加载历史消息
            history = []
            if agent_config.memory and agent_config.memory.get("enabled"):
                history = await self._load_conversation_history(
                    conversation_id=conversation_id,
                    max_history=agent_config.memory.get("max_history", 10)
                )

            # 7. 知识库检索
            context = None

            # 8. 发送开始事件
            yield self._format_sse_event("start", {
                "conversation_id": conversation_id,
                "timestamp": time.time()
            })

            memory_config_ = agent_config.memory
            config_id = memory_config_.get("memory_content")

            # 9. 流式调用 Agent
            full_content = ""
            async for chunk in agent.chat_stream(
                message=message,
                history=history,
                context=context,
                end_user_id=user_id,
                config_id=config_id,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                memory_flag=memory_flag
            ):
                full_content += chunk
                # 发送消息块事件
                yield self._format_sse_event("message", {
                    "content": chunk
                })

            elapsed_time = time.time() - start_time

            # 10. 保存会话消息
            if agent_config.memory and agent_config.memory.get("enabled"):
                await self._save_conversation_message(
                    conversation_id=conversation_id,
                    user_message=message,
                    assistant_message=full_content,
                    app_id=agent_config.app_id,
                    user_id=user_id
                )

            # 11. 发送结束事件
            yield self._format_sse_event("end", {
                "conversation_id": conversation_id,
                "elapsed_time": elapsed_time,
                "message_length": len(full_content)
            })

            logger.info(
                "流式试运行完成",
                extra={
                    "model": model_config.name,
                    "elapsed_time": elapsed_time,
                    "message_length": len(full_content)
                }
            )

        except Exception as e:
            logger.error("流式 Agent 调用失败", extra={"error": str(e)})
            # 发送错误事件
            yield self._format_sse_event("error", {
                "error": str(e),
                "timestamp": time.time()
            })

    def _format_sse_event(self, event: str, data: Dict[str, Any]) -> str:
        """格式化 SSE 事件

        Args:
            event: 事件类型
            data: 事件数据

        Returns:
            str: SSE 格式的字符串
        """
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def _get_api_key(self, model_config_id: uuid.UUID) -> Dict[str, str]:
        """获取模型的 API Key

        Args:
            model_config_id: 模型配置ID

        Returns:
            Dict: 包含 model_name, api_key, api_base 的字典

        Raises:
            BusinessException: 当没有可用的 API Key 时
        """
        stmt = (
            select(ModelApiKey)
            .where(
                ModelApiKey.model_config_id == model_config_id,
                ModelApiKey.is_active == True
            )
            .order_by(ModelApiKey.priority.desc())
            .limit(1)
        )

        api_key = self.db.scalars(stmt).first()

        if not api_key:
            raise BusinessException("没有可用的 API Key", BizCode.AGENT_CONFIG_MISSING)

        return {
            "model_name": api_key.model_name,
            "provider": api_key.provider,
            "api_key": api_key.api_key,
            "api_base": api_key.api_base
        }

    async def _ensure_conversation(
        self,
        conversation_id: Optional[str],
        app_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user_id: Optional[str]
    ) -> str:
        """确保会话存在（创建或验证）

        Args:
            conversation_id: 会话ID（可选）
            app_id: 应用ID
            workspace_id: 工作空间ID（必须）
            user_id: 用户ID

        Returns:
            str: 会话ID

        Raises:
            BusinessException: 当指定的会话不存在时
        """
        from app.models import Conversation as ConversationModel
        from app.schemas.conversation_schema import ConversationCreate
        from app.services.conversation_service import ConversationService

        conversation_service = ConversationService(self.db)

        # 如果没有提供会话ID，创建新会话
        if not conversation_id:
            logger.info(
                "创建新的草稿会话",
                extra={"workspace_id": str(workspace_id)}
            )

            # 获取配置快照
            config_snapshot = await self._get_config_snapshot(app_id)

            # 创建新会话
            new_conv_id = str(uuid.uuid4())
            new_conversation = ConversationModel(
                id=uuid.UUID(new_conv_id),
                app_id=app_id,
                workspace_id=workspace_id,
                user_id=user_id,
                is_draft=True,
                title="草稿会话",
                config_snapshot=config_snapshot
            )
            self.db.add(new_conversation)
            self.db.commit()
            self.db.refresh(new_conversation)

            logger.info(
                "创建草稿会话成功",
                extra={
                    "conversation_id": new_conv_id,
                    "workspace_id": str(workspace_id)
                }
            )

            return new_conv_id

        # 如果提供了会话ID，验证其存在性和工作空间归属
        try:
            conv_uuid = uuid.UUID(conversation_id)
            conversation = conversation_service.get_conversation(conv_uuid)

            # 验证会话属于当前工作空间
            if conversation.workspace_id != workspace_id:
                logger.warning(
                    "会话不属于当前工作空间",
                    extra={
                        "conversation_id": conversation_id,
                        "conversation_workspace_id": str(conversation.workspace_id),
                        "current_workspace_id": str(workspace_id)
                    }
                )
                raise BusinessException(
                    "会话不属于当前工作空间",
                    BizCode.PERMISSION_DENIED
                )

            logger.debug(
                "使用现有会话",
                extra={
                    "conversation_id": conversation_id,
                    "workspace_id": str(workspace_id)
                }
            )
            return conversation_id
        except BusinessException:
            raise
        except Exception as e:
            logger.error(
                "会话不存在或无效",
                extra={"conversation_id": conversation_id, "error": str(e)}
            )
            raise BusinessException(
                f"会话不存在: {conversation_id}",
                BizCode.NOT_FOUND,
                cause=e
            )

    async def _load_conversation_history(
        self,
        conversation_id: str,
        max_history: int = 10
    ) -> List[Dict[str, str]]:
        """加载会话历史消息

        Args:
            conversation_id: 会话ID
            max_history: 最大历史消息数量

        Returns:
            List[Dict]: 历史消息列表
        """
        try:
            from app.services.conversation_service import ConversationService

            conversation_service = ConversationService(self.db)
            history = conversation_service.get_conversation_history(
                conversation_id=uuid.UUID(conversation_id),
                max_history=max_history
            )

            logger.debug(
                "加载会话历史",
                extra={
                    "conversation_id": conversation_id,
                    "max_history": max_history,
                    "loaded_count": len(history)
                }
            )

            return history

        except Exception as e:
            # 新会话没有历史记录是正常的
            logger.debug("加载会话历史失败（可能是新会话）", extra={"error": str(e)})
            return []

    async def _save_conversation_message(
        self,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
        app_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None
    ) -> None:
        """保存会话消息（会话已通过 _ensure_conversation 确保存在）

        Args:
            conversation_id: 会话ID
            user_message: 用户消息
            assistant_message: AI 回复消息
            app_id: 应用ID（未使用，保留用于兼容性）
            user_id: 用户ID（未使用，保留用于兼容性）
        """
        try:
            from app.services.conversation_service import ConversationService

            conversation_service = ConversationService(self.db)
            conv_uuid = uuid.UUID(conversation_id)

            # 保存消息（会话已经存在）
            # 保存用户消息
            conversation_service.add_message(
                conversation_id=conv_uuid,
                role="user",
                content=user_message
            )
            # 保存助手消息
            conversation_service.add_message(
                conversation_id=conv_uuid,
                role="assistant",
                content=assistant_message
            )

            logger.debug(
                "保存会话消息",
                extra={
                    "conversation_id": conversation_id,
                    "user_message_length": len(user_message),
                    "assistant_message_length": len(assistant_message)
                }
            )

        except Exception as e:
            logger.warning("保存会话消息失败", extra={"error": str(e)})

    async def _get_config_snapshot(self, app_id: uuid.UUID) -> Dict[str, Any]:
        """获取当前配置快照

        Args:
            app_id: 应用ID

        Returns:
            Dict: 配置快照
        """
        try:
            from app.models import AgentConfig, ModelConfig

            # 获取 Agent 配置
            stmt = select(AgentConfig).where(AgentConfig.app_id == app_id)
            agent_cfg = self.db.scalars(stmt).first()

            if not agent_cfg:
                return {}

            # 获取模型配置
            model_config = None
            if agent_cfg.default_model_config_id:
                model_config = self.db.get(ModelConfig, agent_cfg.default_model_config_id)

            # 构建快照（确保所有值都可序列化）
            def safe_serialize(value):
                """安全序列化值"""
                if value is None:
                    return None
                if isinstance(value, (str, int, float, bool)):
                    return value
                if isinstance(value, (dict, list)):
                    return value
                # 对于 Pydantic 模型或其他对象，尝试转换为字典
                if hasattr(value, 'dict'):
                    return value.dict()
                if hasattr(value, '__dict__'):
                    return value.__dict__
                return str(value)

            snapshot = {
                "agent_config": {
                    "system_prompt": agent_cfg.system_prompt,
                    "model_parameters": safe_serialize(agent_cfg.model_parameters),
                    "knowledge_retrieval": safe_serialize(agent_cfg.knowledge_retrieval),
                    "memory": safe_serialize(agent_cfg.memory),
                    "variables": safe_serialize(agent_cfg.variables),
                    "tools": safe_serialize(agent_cfg.tools)
                },
                "model_config": {
                    "model_name": model_config.name if model_config else None,
                    "provider": model_config.provider if model_config else None,
                    "type": model_config.type if model_config else None
                } if model_config else None,
                "snapshot_time": datetime.datetime.now().isoformat()
            }

            return snapshot

        except Exception as e:
            # 对于多 Agent 应用，没有直接的 AgentConfig 是正常的
            logger.debug("获取配置快照失败（可能是多 Agent 应用）", extra={"error": str(e)})
            return {}

    def _replace_variables(
        self,
        text: str,
        values: Dict[str, Any],
        definitions: List[Dict[str, Any]]
    ) -> str:
        """替换文本中的变量

        Args:
            text: 原始文本
            values: 变量值
            definitions: 变量定义

        Returns:
            str: 替换后的文本
        """
        result = text

        # 创建变量定义映射
        var_defs = {var["name"]: var for var in definitions}

        for var_name, var_value in values.items():
            # 检查变量是否在定义中
            if var_name not in var_defs:
                logger.warning(f"未定义的变量: {var_name}")
                continue

            # 替换变量（支持多种格式）
            placeholders = [
                f"{{{{{var_name}}}}}",  # {{var_name}}
                f"{{{var_name}}}",      # {var_name}
                f"${{{var_name}}}",     # ${var_name}
            ]

            for placeholder in placeholders:
                if placeholder in result:
                    result = result.replace(placeholder, str(var_value))

        return result

    # ==================== 多模型对比试运行 ====================

    async def run_compare(
        self,
        *,
        agent_config: AgentConfig,
        models: List[Dict[str, Any]],
        message: str,
        workspace_id: uuid.UUID,
        conversation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        parallel: bool = True,
        timeout: int = 60,
        storage_type: Optional[str] = None,
        user_rag_memory_id: Optional[str] = None,
        web_search: bool = True,
        memory: bool = True,
    ) -> Dict[str, Any]:
        """多模型对比试运行

        Args:
            agent_config: Agent 配置
            models: 模型配置列表，每项包含 model_config, parameters, label, model_config_id
            message: 用户消息
            workspace_id: 工作空间ID
            conversation_id: 会话ID
            user_id: 用户ID
            variables: 变量参数
            parallel: 是否并行执行
            timeout: 超时时间（秒）

        Returns:
            Dict: 对比结果
        """
        logger.info(
            "多模型对比试运行",
            extra={
                "model_count": len(models),
                "parallel": parallel
            }
        )

        async def run_single_model(model_info):
            """运行单个模型"""
            try:
                start_time = time.time()

                # 临时修改参数（不使用 deepcopy 避免 SQLAlchemy 会话问题）
                original_params = agent_config.model_parameters
                agent_config.model_parameters = model_info["parameters"]

                # 使用模型自己的 conversation_id，如果没有则使用全局的
                model_conversation_id = model_info.get("conversation_id") or conversation_id
                try:
                    result = await asyncio.wait_for(
                        self.run(
                            agent_config=agent_config,
                            model_config=model_info["model_config"],
                            message=message,
                            workspace_id=workspace_id,
                            conversation_id=model_conversation_id,
                            user_id=user_id,
                            variables=variables,
                            storage_type=storage_type,
                            user_rag_memory_id=user_rag_memory_id,
                            web_search=web_search,
                            memory=memory
                        ),
                        timeout=timeout
                    )
                finally:
                    # 恢复原始参数
                    agent_config.model_parameters = original_params

                elapsed = time.time() - start_time
                usage = result.get("usage", {})

                return {
                    "model_config_id": model_info["model_config_id"],
                    "model_name": model_info["model_config"].name,
                    "label": model_info["label"],
                    "conversation_id":result['conversation_id'],
                    "parameters_used": model_info["parameters"],
                    "message": result.get("message"),
                    "usage": usage,
                    "elapsed_time": elapsed,
                    "tokens_per_second": (
                        usage.get("completion_tokens", 0) / elapsed
                        if elapsed > 0 and usage.get("completion_tokens") else None
                    ),
                    "cost_estimate": self._estimate_cost(usage, model_info["model_config"]),
                    "error": None
                }

            except TimeoutError:
                logger.warning(
                    "模型运行超时",
                    extra={
                        "model_config_id": str(model_info["model_config_id"]),
                        "timeout": timeout
                    }
                )
                return {
                    "model_config_id": model_info["model_config_id"],
                    "model_name": model_info["model_config"].name,
                    "conversation_id": conversation_id,
                    "label": model_info["label"],
                    "parameters_used": model_info["parameters"],
                    "elapsed_time": timeout,
                    "error": f"执行超时（{timeout}秒）"
                }
            except Exception as e:
                logger.error(
                    "模型运行失败",
                    extra={
                        "model_config_id": str(model_info["model_config_id"]),
                        "error": str(e)
                    }
                )
                return {
                    "model_config_id": model_info["model_config_id"],
                    "model_name": model_info["model_config"].name,
                    "label": model_info["label"],
                    "conversation_id": conversation_id,
                    "parameters_used": model_info["parameters"],
                    "elapsed_time": 0,
                    "error": str(e)
                }

        # 执行所有模型（并行或串行）
        if parallel:
            logger.debug(f"并行执行 {len(models)} 个模型")
            results = await asyncio.gather(
                *[run_single_model(m) for m in models],
                return_exceptions=False
            )
        else:
            logger.debug(f"串行执行 {len(models)} 个模型")
            results = []
            for model_info in models:
                result = await run_single_model(model_info)
                results.append(result)

        # 统计分析
        successful = [r for r in results if not r.get("error")]
        failed = [r for r in results if r.get("error")]

        fastest = min(successful, key=lambda x: x["elapsed_time"]) if successful else None
        cheapest = min(
            successful,
            key=lambda x: x.get("cost_estimate") or float("inf")
        ) if successful else None

        logger.info(
            "多模型对比完成",
            extra={
                "successful": len(successful),
                "failed": len(failed),
                "total_time": sum(r.get("elapsed_time", 0) for r in results)
            }
        )

        return {
            "results": results,
            "total_elapsed_time": sum(r.get("elapsed_time", 0) for r in results),
            "successful_count": len(successful),
            "failed_count": len(failed),
            "fastest_model": fastest["label"] if fastest else None,
            "cheapest_model": cheapest["label"] if cheapest else None
        }

    def _estimate_cost(self, usage: Dict[str, Any], model_config) -> Optional[float]:
        """估算成本

        Args:
            usage: Token 使用情况
            model_config: 模型配置

        Returns:
            Optional[float]: 估算成本（美元）
        """
        if not usage:
            return None

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # 简化成本估算：暂时返回 None
        # TODO: 实现基于模型名称或配置的成本估算
        # 需要从 ModelApiKey 获取实际的模型名称，或者在 ModelConfig 中添加 model 字段
        return None

    def _with_parameters(self, agent_config: AgentConfig, parameters: Dict[str, Any]) -> AgentConfig:
        """创建一个带有覆盖参数的 agent_config（浅拷贝，只修改 model_parameters）

        Args:
            agent_config: 原始 Agent 配置
            parameters: 要覆盖的参数

        Returns:
            AgentConfig: 修改后的配置（注意：这是同一个对象，只是临时修改了 model_parameters）
        """
        # 保存原始参数
        original_params = agent_config.model_parameters
        # 设置新参数
        agent_config.model_parameters = parameters
        return agent_config, original_params

    async def run_compare_stream(
        self,
        *,
        agent_config: AgentConfig,
        models: List[Dict[str, Any]],
        message: str,
        workspace_id: uuid.UUID,
        conversation_id: Optional[str] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        storage_type: Optional[str] = None,
        user_rag_memory_id: Optional[str] = None,
        web_search: bool = True,
        memory: bool = True,
        parallel: bool = True,
        timeout: int = 60
    ) -> AsyncGenerator[str, None]:
        """多模型对比试运行（流式返回）

        参考 run_compare 的实现，支持并行或串行执行

        Args:
            agent_config: Agent 配置
            models: 模型配置列表，每项包含 model_config, parameters, label, model_config_id
            message: 用户消息
            workspace_id: 工作空间ID
            conversation_id: 会话ID
            user_id: 用户ID
            variables: 变量参数
            storage_type: 存储类型
            user_rag_memory_id: RAG 记忆 ID
            web_search: 是否启用网络搜索
            memory: 是否启用记忆
            parallel: 是否并行执行
            timeout: 超时时间（秒）

        Yields:
            str: SSE 格式的事件数据
        """
        logger.info(
            "多模型对比流式试运行",
            extra={"model_count": len(models), "parallel": parallel}
        )

        # 发送开始事件
        yield self._format_sse_event("compare_start", {
            "conversation_id": conversation_id,
            "model_count": len(models),
            "parallel": parallel,
            "timestamp": time.time()
        })

        results = []

        async def run_single_model_stream(idx: int, model_info: Dict[str, Any], event_queue: asyncio.Queue):
            """运行单个模型（流式）并将事件放入队列"""
            model_label = model_info["label"]
            model_config_id = str(model_info["model_config_id"])
            # 使用模型自己的 conversation_id，如果没有则使用全局的
            model_conversation_id = model_info.get("conversation_id") or conversation_id

            try:
                # 发送模型开始事件
                await event_queue.put(self._format_sse_event("model_start", {
                    "model_index": idx,
                    "model_config_id": model_config_id,
                    "model_name": model_info["model_config"].name,
                    "label": model_label,
                    "conversation_id": model_conversation_id,
                    "timestamp": time.time()
                }))

                start_time = time.time()
                full_content = ""
                returned_conversation_id = model_conversation_id

                # 临时修改参数
                original_params = agent_config.model_parameters
                agent_config.model_parameters = model_info["parameters"]

                try:
                    # 流式调用单个模型
                    async for event_str in self.run_stream(
                        agent_config=agent_config,
                        model_config=model_info["model_config"],
                        message=message,
                        workspace_id=workspace_id,
                        conversation_id=model_conversation_id,
                        user_id=user_id,
                        variables=variables,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id,
                        web_search=web_search,
                        memory=memory
                    ):
                        # 解析原始事件
                        try:
                            lines = event_str.strip().split('\n')
                            event_type = None
                            event_data = None

                            for line in lines:
                                if line.startswith('event: '):
                                    event_type = line[7:].strip()
                                elif line.startswith('data: '):
                                    event_data = json.loads(line[6:])

                            # 从 start 事件中获取实际的 conversation_id
                            if event_type == "start" and event_data:
                                conv_id = event_data.get("conversation_id")
                                if conv_id:
                                    returned_conversation_id = conv_id

                            # 累积消息内容
                            if event_type == "message" and event_data:
                                chunk = event_data.get("content", "")
                                full_content += chunk

                                # 转发消息块事件（带模型标识）
                                await event_queue.put(self._format_sse_event("model_message", {
                                    "model_index": idx,
                                    "model_config_id": model_config_id,
                                    "label": model_label,
                                    "conversation_id": returned_conversation_id,
                                    "content": chunk
                                }))
                        except Exception as e:
                            logger.warning(f"解析流式事件失败: {e}")
                finally:
                    # 恢复原始参数
                    agent_config.model_parameters = original_params

                elapsed = time.time() - start_time

                # 构建结果（参考 run_compare）
                result = {
                    "model_config_id": model_info["model_config_id"],
                    "model_name": model_info["model_config"].name,
                    "label": model_label,
                    "conversation_id": returned_conversation_id,
                    "parameters_used": model_info["parameters"],
                    "message": full_content,
                    "elapsed_time": elapsed,
                    "error": None
                }

                # 发送模型完成事件
                await event_queue.put(self._format_sse_event("model_end", {
                    "model_index": idx,
                    "model_config_id": model_config_id,
                    "label": model_label,
                    "conversation_id": returned_conversation_id,
                    "elapsed_time": elapsed,
                    "message_length": len(full_content),
                    "timestamp": time.time()
                }))

                return result

            except TimeoutError:
                logger.warning(f"模型运行超时: {model_label}")
                result = {
                    "model_config_id": model_info["model_config_id"],
                    "model_name": model_info["model_config"].name,
                    "label": model_label,
                    "conversation_id": model_conversation_id,
                    "parameters_used": model_info["parameters"],
                    "elapsed_time": timeout,
                    "error": f"执行超时（{timeout}秒）"
                }

                await event_queue.put(self._format_sse_event("model_error", {
                    "model_index": idx,
                    "model_config_id": model_config_id,
                    "label": model_label,
                    "conversation_id": model_conversation_id,
                    "error": result["error"],
                    "timestamp": time.time()
                }))

                return result

            except Exception as e:
                logger.error(f"模型运行失败: {model_label}, error: {e}")
                result = {
                    "model_config_id": model_info["model_config_id"],
                    "model_name": model_info["model_config"].name,
                    "label": model_label,
                    "conversation_id": model_conversation_id,
                    "parameters_used": model_info["parameters"],
                    "elapsed_time": 0,
                    "error": str(e)
                }

                await event_queue.put(self._format_sse_event("model_error", {
                    "model_index": idx,
                    "model_config_id": model_config_id,
                    "label": model_label,
                    "conversation_id": model_conversation_id,
                    "error": str(e),
                    "timestamp": time.time()
                }))

                return result

        if parallel:
            # 并行执行所有模型（参考 run_compare）
            logger.debug(f"并行执行 {len(models)} 个模型（流式）")

            # 创建事件队列
            event_queue = asyncio.Queue()

            # 启动所有模型的并行任务
            tasks = [
                asyncio.create_task(run_single_model_stream(idx, model_info, event_queue))
                for idx, model_info in enumerate(models)
            ]

            # 持续从队列中取出事件并转发
            completed_tasks = set()
            while len(completed_tasks) < len(tasks):
                try:
                    # 尝试从队列获取事件
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                    yield event
                except TimeoutError:
                    # 检查是否有任务完成
                    for task in tasks:
                        if task.done() and task not in completed_tasks:
                            completed_tasks.add(task)
                            try:
                                result = await task
                                if result:
                                    results.append(result)
                            except Exception as e:
                                logger.error(f"获取任务结果失败: {e}")
                    continue

            # 清空队列中剩余的事件
            while not event_queue.empty():
                try:
                    event = event_queue.get_nowait()
                    yield event
                except asyncio.QueueEmpty:
                    break

        else:
            # 串行执行每个模型（参考 run_compare）
            logger.debug(f"串行执行 {len(models)} 个模型（流式）")

            for idx, model_info in enumerate(models):
                # 创建临时队列用于单个模型
                event_queue = asyncio.Queue()

                # 运行单个模型
                result = await run_single_model_stream(idx, model_info, event_queue)
                if result:
                    results.append(result)

                # 转发该模型的所有事件
                while not event_queue.empty():
                    try:
                        event = event_queue.get_nowait()
                        yield event
                    except asyncio.QueueEmpty:
                        break

        # 统计分析（参考 run_compare）
        successful = [r for r in results if not r.get("error")]
        failed = [r for r in results if r.get("error")]

        fastest = min(successful, key=lambda x: x["elapsed_time"]) if successful else None
        cheapest = min(
            successful,
            key=lambda x: x.get("cost_estimate") or float("inf")
        ) if successful else None

        # 构建结果摘要（包含完整的 message）
        results_summary = []
        for r in results:
            results_summary.append({
                "model_config_id": str(r["model_config_id"]),
                "model_name": r["model_name"],
                "label": r["label"],
                "conversation_id": r.get("conversation_id"),
                "message": r.get("message"),  # 包含完整消息
                "elapsed_time": r.get("elapsed_time", 0),
                "error": r.get("error")
            })

        # 发送对比完成事件（参考 run_compare 的返回格式）
        yield self._format_sse_event("compare_end", {
            "conversation_id": conversation_id,
            "results": results_summary,  # 包含完整结果
            "total_elapsed_time": sum(r.get("elapsed_time", 0) for r in results),
            "successful_count": len(successful),
            "failed_count": len(failed),
            "fastest_model": fastest["label"] if fastest else None,
            "cheapest_model": cheapest["label"] if cheapest else None,
            "timestamp": time.time()
        })

        logger.info(
            "多模型对比流式完成",
            extra={
                "successful": len(successful),
                "failed": len(failed),
                "total_time": sum(r.get("elapsed_time", 0) for r in results)
            }
        )


async def draft_run(
    db: Session,
    *,
    agent_config: AgentConfig,
    model_config: ModelConfig,
    message: str,
    user_id: Optional[str] = None,
    kb_ids: Optional[List[str]] = None,
    similarity_threshold: float = 0.7,
    top_k: int = 3
) -> Dict[str, Any]:
    """试运行 Agent（便捷函数）

    Args:
        db: 数据库会话
        agent_config: Agent 配置
        model_config: 模型配置
        message: 用户消息
        user_id: 用户ID
        kb_ids: 知识库ID列表
        similarity_threshold: 相似度阈值
        top_k: 检索返回的文档数量

    Returns:
        Dict: 包含 AI 回复和元数据的字典
    """
    service = DraftRunService(db)
    return await service.run(
        agent_config=agent_config,
        model_config=model_config,
        message=message,
        user_id=user_id,
        kb_ids=kb_ids,
        similarity_threshold=similarity_threshold,
        top_k=top_k
    )


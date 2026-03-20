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

from langchain.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.core.agent.agent_middleware import AgentMiddleware
from app.core.agent.langchain_agent import LangChainAgent
from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.rag.nlp.search import knowledge_retrieval
from app.db import get_db_context
from app.models import AgentConfig, ModelConfig, ModelType
from app.repositories.tool_repository import ToolRepository
from app.schemas.app_schema import FileInput
from app.schemas.model_schema import ModelInfo
from app.schemas.prompt_schema import PromptMessageRole, render_prompt_message
from app.services import task_service
from app.services.conversation_service import ConversationService
from app.services.langchain_tool_server import Search
from app.services.memory_agent_service import MemoryAgentService
from app.services.model_parameter_merger import ModelParameterMerger
from app.services.model_service import ModelApiKeyService
from app.services.multimodal_service import MultimodalService
from app.services.tool_service import ToolService
from app.schemas import FileType

logger = get_business_logger()


class KnowledgeRetrievalInput(BaseModel):
    """知识库检索工具输入参数"""
    query: str = Field(description="需要检索的问题或关键词")


class WebSearchInput(BaseModel):
    """网络搜索工具输入参数"""
    query: str = Field(description="需要搜索的问题或关键词")


class LongTermMemoryInput(BaseModel):
    """长期记忆工具输入参数"""
    question: str = Field(
        description="经过优化重写的查询问题。请将用户的原始问题重写为更合适的检索形式，包含关键词，上下文和具体描述，注意错词检查并且改写")


def create_long_term_memory_tool(
        memory_config: Dict[str, Any],
        end_user_id: str,
        storage_type: Optional[str] = None,
        user_rag_memory_id: Optional[str] = None
):
    """创建记忆工具,


    Args:
        memory_config: 记忆配置
        end_user_id: 用户ID
        storage_type: 存储类型（可选）
        user_rag_memory_id: 用户RAG记忆ID（可选）

    Returns:
        长期记忆工具
    """
    # search_switch = memory_config.get("search_switch", "2")
    # 兼容新旧字段名：优先使用 memory_config_id，回退到 memory_content
    config_id = memory_config.get("memory_config_id") or memory_config.get("memory_content", None)
    logger.info(f"创建长期记忆工具，配置: end_user_id={end_user_id}, config_id={config_id}, storage_type={storage_type}")

    @tool(args_schema=LongTermMemoryInput)
    def long_term_memory(question: str) -> str:
        """
        从用户的历史记忆中检索相关信息。用于了解用户的背景、偏好和历史对话内容。

        **何时使用此工具：**
        - 用户明确询问历史信息（如"我之前说过什么"、"上次我们聊了什么"）
        - 用户询问个人信息或偏好（如"我喜欢什么"、"我的习惯是什么"）
        - 需要基于历史上下文提供个性化建议

        **何时不使用此工具：**
        - 简单问候（如"你好"、"谢谢"、"再见"）
        - 纯任务性请求（如"写代码"、"翻译文字"、"分析图片"）
        - 用户已提供完整信息（如提供了文本、图片、文档等内容）
        - 创作性任务（如"写诗"、"编故事"、"创作谜语"）

        **重要：如果用户的问题可以直接回答，不要调用此工具。只在确实需要历史信息时才使用。**

        Args:
            question: 需要检索的问题（保持原问题的核心语义，使用清晰的关键词，第三人称描述的偏好、行为通常指用户本人，比如（我，本人，在下，自己，咱，鄙人，吴，余）通指用户）

        Returns:
            检索到的历史记忆内容
        """
        logger.info(f" 长期记忆工具被调用！question={question}, user={end_user_id}")
        try:
            with get_db_context() as db:
                memory_content = asyncio.run(
                    MemoryAgentService().read_memory(
                        end_user_id=end_user_id,
                        message=question,
                        history=[],
                        search_switch="2",
                        config_id=config_id,
                        db=db,
                        storage_type=storage_type,
                        user_rag_memory_id=user_rag_memory_id
                    )
                )
                task = celery_app.send_task(
                    "app.core.memory.agent.read_message",
                    args=[end_user_id, question, [], "1", config_id, storage_type, user_rag_memory_id]
                )
                result = task_service.get_task_memory_read_result(task.id)
                status = result.get("status")
                logger.info(f"读取任务状态：{status}")
                if memory_content:
                    memory_content = memory_content['answer']
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


def create_knowledge_retrieval_tool(kb_config, kb_ids, user_id):
    """从知识库中检索相关信息。当用户的问题需要参考知识库、文档或历史记录时，使用此工具进行检索。

    Args:
        kb_config: 知识库配置
        kb_ids: 知识库ID列表
        user_id: 用户ID

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


class AgentRunService:
    """Agent运行服务类"""

    def __init__(self, db: Session):
        """Agent运行服务

        Args:
            db: 数据库会话
        """
        self.db = db

    @staticmethod
    def prepare_variables(
            input_vars: dict | None,
            variables_config: dict
    ) -> dict:
        input_vars = input_vars or {}
        for variable in variables_config:
            if variable.get("required") and variable.get("name") not in input_vars:
                raise ValueError(f"The required parameter '{variable.get('name')}' was not provided")
        return input_vars

    def load_tools_config(self, tools_config, web_search, tenant_id) -> list:
        """加载工具配置"""
        tools = []
        if web_search:
            search_tool = create_web_search_tool({})
            tools.append(search_tool)
        if not tools_config:
            return tools
        tool_service = ToolService(self.db)

        if tools_config and isinstance(tools_config, list):
            for tool_config in tools_config:
                if tool_config.get("enabled", False):
                    # 根据工具名称查找工具实例
                    tool_instance = tool_service.get_tool_instance(tool_config.get("tool_id", ""), tenant_id)
                    if tool_instance:
                        # 转换为LangChain工具
                        langchain_tool = tool_instance.to_langchain_tool(tool_config.get("operation", None))
                        tools.append(langchain_tool)
        logger.debug(
            "已添加网络搜索工具",
            extra={
                "tool_count": len(tools)
            }
        )
        return tools

    def load_skill_config(
            self,
            skills_config: dict | None,
            message: str, tenant_id
    ) -> tuple[list, str]:
        if not skills_config:
            return [], ""

        tools = []
        skill_prompts = ""
        skill_enable = skills_config.get("enabled", False)
        if skill_enable:
            middleware = AgentMiddleware(skills=skills_config)
            skill_tools, skill_configs, tool_to_skill_map = middleware.load_skill_tools(self.db, tenant_id)
            tools.extend(skill_tools)
            logger.debug(f"已加载 {len(skill_tools)} 个技能工具")

            if skill_configs:
                tools, activated_skill_ids = middleware.filter_tools(tools, message, skill_configs,
                                                                     tool_to_skill_map)
                logger.debug(f"过滤后剩余 {len(tools)} 个工具")
                skill_prompts = AgentMiddleware.get_active_prompts(
                    activated_skill_ids, skill_configs
                )

        return tools, skill_prompts

    def load_knowledge_retrieval_config(
            self,
            knowledge_retrieval_config: dict | None,
            user_id
    ) -> list:
        if not knowledge_retrieval_config:
            return []

        tools = []
        knowledge_bases = knowledge_retrieval_config.get("knowledge_bases", [])
        kb_ids = bool(knowledge_bases and knowledge_bases[0].get("kb_id"))
        if kb_ids:
            # 创建知识库检索工具
            kb_tool = create_knowledge_retrieval_tool(knowledge_retrieval_config, kb_ids, user_id)
            tools.append(kb_tool)

            logger.debug(
                "已添加知识库检索工具",
                extra={
                    "kb_ids": kb_ids,
                    "tool_count": len(tools)
                }
            )
        return tools

    def load_memory_config(
            self,
            memory_config: dict | None,
            user_id,
            storage_type,
            user_rag_memory_id
    ) -> tuple[list, bool]:
        """加载长期记忆配置"""
        if not memory_config:
            return [], False

        tools = []
        if memory_config.get("enabled"):
            if user_id:
                # 创建长期记忆工具
                memory_tool = create_long_term_memory_tool(memory_config, user_id, storage_type,
                                                           user_rag_memory_id)
                tools.append(memory_tool)

                logger.debug(
                    "已添加长期记忆工具",
                    extra={
                        "user_id": user_id,
                        "tool_count": len(tools)
                    }
                )
        return tools, bool(memory_config.get("enabled"))

    @staticmethod
    def _validate_file_upload(
            features_config: Dict[str, Any],
            files: Optional[List[FileInput]]
    ) -> None:
        """校验上传文件是否符合 file_upload 配置"""
        if not files or not features_config:
            return
        fu = features_config.get("file_upload", {})
        if not (isinstance(fu, dict) and fu.get("enabled")):
            raise BusinessException("该应用未开启文件上传功能", BizCode.BAD_REQUEST)
        max_count = fu.get("max_file_count", 5)
        if len(files) > max_count:
            raise BusinessException(f"文件数量超过限制（最多 {max_count} 个）", BizCode.BAD_REQUEST)

        # 校验传输方式
        allowed_methods = fu.get("allowed_transfer_methods", ["local_file", "remote_url"])
        for f in files:
            if f.transfer_method.value not in allowed_methods:
                raise BusinessException(
                    f"不支持的文件传输方式：{f.transfer_method.value}，允许的方式：{', '.join(allowed_methods)}",
                    BizCode.BAD_REQUEST
                )

        # 各类型对应的开关和大小限制配置键
        type_cfg = {
            "image":    ("image_enabled",    "image_max_size_mb",    20,  "图片"),
            "audio":    ("audio_enabled",    "audio_max_size_mb",    50,  "音频"),
            "document": ("document_enabled", "document_max_size_mb", 100, "文档"),
            "video":    ("video_enabled",    "video_max_size_mb",    500, "视频"),
        }

        for f in files:
            ftype = str(f.type)  # 如 "image", "audio", "document", "video"
            cfg = type_cfg.get(ftype)
            if cfg is None:
                continue
            enabled_key, size_key, default_max_mb, label = cfg

            # 校验类型开关
            if not fu.get(enabled_key):
                raise BusinessException(f"该应用未开启{label}文件上传", BizCode.BAD_REQUEST)

            # 校验文件大小（仅当内容已加载时）
            content = f.get_content()
            if content is not None:
                max_mb = fu.get(size_key, default_max_mb)
                size_mb = len(content) / (1024 * 1024)
                if size_mb > max_mb:
                    raise BusinessException(
                        f"{label}文件大小超过限制（最大 {max_mb}MB，当前 {size_mb:.1f}MB）",
                        BizCode.BAD_REQUEST
                    )

    @staticmethod
    def _inject_opening_statement(
            features_config: Dict[str, Any],
            system_prompt: str,
            is_new_conversation: bool
    ) -> str:
        """首轮对话时将开场白注入 system_prompt"""
        if not is_new_conversation:
            return system_prompt
        opening = features_config.get("opening_statement", {})
        if not (isinstance(opening, dict) and opening.get("enabled") and opening.get("statement")):
            return system_prompt
        statement = opening["statement"]
        return f"{system_prompt}\n\n[对话开场白]\n{statement}"

    @staticmethod
    def _filter_citations(
            features_config: Dict[str, Any],
            citations: List[Any]
    ) -> List[Any]:
        """根据 citation 开关决定是否返回引用来源"""
        citation_cfg = features_config.get("citation", {})
        if isinstance(citation_cfg, dict) and citation_cfg.get("enabled"):
            return citations
        return []

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
            memory: bool = True,
            sub_agent: bool = False,
            files: Optional[List[FileInput]] = None  # 新增：多模态文件
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
            storage_type: 存储类型（可选）
            user_rag_memory_id: 用户RAG记忆ID（可选）
            web_search: 是否启用网络搜索（默认True）
            memory: 是否启用长期记忆（默认True）
            sub_agent: 是否为子代理调用（默认False）
            files: 多模态文件列表（可选）

        Returns:
            Dict: 包含 AI 回复和元数据的字典
        """
        start_time = time.time()
        tools_config: dict | list | None = agent_config.tools
        skills_config: dict | None = agent_config.skills
        knowledge_retrieval_config: dict | None = agent_config.knowledge_retrieval
        memory_config: dict | None = agent_config.memory
        features_config: dict = agent_config.features or {}

        # 从 features 中读取功能开关（优先级高于参数默认值）
        web_search_feature = features_config.get("web_search", {})
        if not isinstance(web_search_feature, dict) or not web_search_feature.get("enabled"):
            web_search = False

        # file_upload 校验
        self._validate_file_upload(features_config, files)

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

            if sub_agent:
                variables = self.prepare_variables(variables, agent_config.variables)
            else:
                # FIXME: subagent input valid
                variables = variables or {}

            system_prompt = render_prompt_message(
                agent_config.system_prompt,
                PromptMessageRole.USER,
                variables
            )

            # 3. 处理系统提示词（支持变量替换）
            system_prompt = system_prompt.get_text_content() or "你是一个专业的AI助手"

            # opening_statement：首轮对话注入开场白
            is_new_conversation = not conversation_id
            system_prompt = self._inject_opening_statement(features_config, system_prompt, is_new_conversation)

            # 4. 准备工具列表
            tools = []

            tenant_id = ToolRepository.get_tenant_id_by_workspace_id(self.db, str(workspace_id))

            # 从配置中获取启用的工具
            tools.extend(self.load_tools_config(tools_config, web_search, tenant_id))
            skill_tools, skill_prompts = self.load_skill_config(skills_config, message, tenant_id)
            tools.extend(skill_tools)
            if skill_prompts:
                system_prompt = f"{system_prompt}\n\n{skill_prompts}"
            tools.extend(self.load_knowledge_retrieval_config(knowledge_retrieval_config, user_id))
            # 添加长期记忆工具
            memory_flag = False
            if memory:
                memory_tools, memory_flag = self.load_memory_config(
                    memory_config, user_id, storage_type, user_rag_memory_id
                )
                tools.extend(memory_tools)

            # 4. 创建 LangChain Agent
            agent = LangChainAgent(
                model_name=api_key_config["model_name"],
                api_key=api_key_config["api_key"],
                provider=api_key_config.get("provider", "openai"),
                api_base=api_key_config.get("api_base"),
                is_omni=api_key_config.get("is_omni", False),
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

            model_info = ModelInfo(
                model_name=api_key_config["model_name"],
                provider=api_key_config["provider"],
                api_key=api_key_config["api_key"],
                api_base=api_key_config["api_base"],
                capability=api_key_config["capability"],
                is_omni=api_key_config["is_omni"],
                model_type=model_config.type
            )

            # 6. 加载历史消息
            history = await self._load_conversation_history(
                conversation_id=conversation_id,
                api_config=model_info,
                max_history=10
            )

            # 6. 处理多模态文件
            processed_files = None
            if files:
                # 获取 provider 信息
                provider = api_key_config.get("provider", "openai")
                multimodal_service = MultimodalService(self.db, model_info)
                processed_files = await multimodal_service.process_files(user_id, files)
                logger.info(f"处理了 {len(processed_files)} 个文件，provider={provider}")

            # 7. 知识库检索
            context = None

            logger.debug(
                "准备调用 LangChain Agent",
                extra={
                    "model": api_key_config["model_name"],
                    "has_history": bool(history),
                    "has_context": bool(context),
                    "has_files": bool(processed_files)
                }
            )

            memory_config_ = agent_config.memory
            # 兼容新旧字段名：优先使用 memory_config_id，回退到 memory_content
            config_id = memory_config_.get("memory_config_id") or memory_config_.get("memory_content", None)

            # 8. 调用 Agent（支持多模态）
            result = await agent.chat(
                message=message,
                history=history,
                context=context,
                end_user_id=user_id,
                config_id=config_id,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                memory_flag=memory_flag,
                files=processed_files  # 传递处理后的文件
            )

            elapsed_time = time.time() - start_time

            ModelApiKeyService.record_api_key_usage(self.db, api_key_config.get("api_key_id"))

            # 9. 生成 TTS audio_url（在保存消息前生成，以便一并存入 meta_data）
            audio_url = await self._generate_tts(
                features_config, result["content"], api_key_config,
                tenant_id=tenant_id, workspace_id=workspace_id
            ) if not sub_agent else None

            # 10. 保存会话消息
            if not sub_agent:
                await self._save_conversation_message(
                    conversation_id=conversation_id,
                    user_message=message,
                    assistant_message=result["content"],
                    app_id=agent_config.app_id,
                    user_id=user_id,
                    meta_data={
                        "usage": result.get("usage", {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0
                        })
                    },
                    files=files,
                    audio_url=audio_url
                )

            response = {
                "message": result["content"],
                "conversation_id": conversation_id,
                "usage": result.get("usage", {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                }),
                "elapsed_time": elapsed_time,
                "suggested_questions": await self._generate_suggested_questions(
                    features_config, result["content"], api_key_config, effective_params
                ) if not sub_agent else [],
                "citations": self._filter_citations(features_config, result.get("citations", [])),
                "audio_url": audio_url,
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
            memory: bool = True,  # 布尔类型默认值
            sub_agent: bool = False,  # 是否是作为子Agent运行
            files: Optional[List[FileInput]] = None  # 新增：多模态文件

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
        tools_config: dict | list | None = agent_config.tools
        skills_config: dict | None = agent_config.skills
        knowledge_retrieval_config: dict | None = agent_config.knowledge_retrieval
        memory_config: dict | None = agent_config.memory
        features_config: dict = agent_config.features or {}

        # 从 features 中读取功能开关
        web_search_feature = features_config.get("web_search", {})
        if not (isinstance(web_search_feature, dict) and web_search_feature.get("enabled")):
            web_search = False

        # file_upload 校验
        self._validate_file_upload(features_config, files)

        start_time = time.time()

        try:
            # 1. 获取 API Key 配置
            api_key_config = await self._get_api_key(model_config.id)
            if not sub_agent:
                variables = self.prepare_variables(variables, agent_config.variables)
            else:
                # FIXME: subagent input valid
                variables = variables or {}

            # 2. 合并模型参数
            effective_params = ModelParameterMerger.get_effective_parameters(
                model_config=model_config,
                agent_config=agent_config
            )

            items_params = variables

            system_prompt = render_prompt_message(
                agent_config.system_prompt,  # 修正拼写错误
                PromptMessageRole.USER,
                items_params
            )

            # 3. 处理系统提示词（支持变量替换）
            system_prompt = system_prompt.get_text_content() or "你是一个专业的AI助手"

            # opening_statement：首轮对话注入开场白
            is_new_conversation = not conversation_id
            system_prompt = self._inject_opening_statement(features_config, system_prompt, is_new_conversation)

            # 4. 准备工具列表
            tools = []

            tenant_id = ToolRepository.get_tenant_id_by_workspace_id(self.db, str(workspace_id))

            # 从配置中获取启用的工具
            tools.extend(self.load_tools_config(tools_config, web_search, tenant_id))
            skill_tools, skill_prompts = self.load_skill_config(skills_config, message, tenant_id)
            tools.extend(skill_tools)
            if skill_prompts:
                system_prompt = f"{system_prompt}\n\n{skill_prompts}"
            tools.extend(self.load_knowledge_retrieval_config(knowledge_retrieval_config, user_id))

            # 添加长期记忆工具
            memory_flag = False
            if memory:
                memory_tools, memory_flag = self.load_memory_config(memory_config, user_id, storage_type,
                                                                    user_rag_memory_id)
                tools.extend(memory_tools)

            # 4. 创建 LangChain Agent
            agent = LangChainAgent(
                model_name=api_key_config["model_name"],
                api_key=api_key_config["api_key"],
                provider=api_key_config.get("provider", "openai"),
                api_base=api_key_config.get("api_base"),
                is_omni=api_key_config.get("is_omni", False),
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
                user_id=user_id,
                sub_agent=sub_agent
            )

            model_info = ModelInfo(
                model_name=api_key_config["model_name"],
                provider=api_key_config["provider"],
                api_key=api_key_config["api_key"],
                api_base=api_key_config["api_base"],
                capability=api_key_config["capability"],
                is_omni=api_key_config["is_omni"],
                model_type=model_config.type
            )

            # 6. 加载历史消息
            history = await self._load_conversation_history(
                conversation_id=conversation_id,
                api_config=model_info,
                max_history=memory_config.get("max_history", 10)
            )

            # 6. 处理多模态文件
            processed_files = None
            if files:
                # 获取 provider 信息
                provider = api_key_config.get("provider", "openai")
                multimodal_service = MultimodalService(self.db, model_info)
                processed_files = await multimodal_service.process_files(user_id, files)
                logger.info(f"处理了 {len(processed_files)} 个文件，provider={provider}")

            # 7. 知识库检索
            context = None

            # 8. 发送开始事件
            yield self._format_sse_event("start", {
                "conversation_id": conversation_id,
                "timestamp": time.time()
            })

            memory_config_ = agent_config.memory
            # 兼容新旧字段名：优先使用 memory_config_id，回退到 memory_content
            config_id = memory_config_.get("memory_config_id") or memory_config_.get("memory_content", None)

            # 9. 流式调用 Agent（支持多模态），同时并行启动 TTS
            full_content = ""
            total_tokens = 0

            # 启动流式 TTS（文本边输出边合成）
            text_queue: asyncio.Queue = asyncio.Queue()
            stream_audio_url, tts_task = await self._generate_tts_streaming(
                features_config, api_key_config,
                text_queue=text_queue,
                tenant_id=tenant_id, workspace_id=workspace_id
            ) if not sub_agent else (None, None)

            async for chunk in agent.chat_stream(
                    message=message,
                    history=history,
                    context=context,
                    end_user_id=user_id,
                    config_id=config_id,
                    storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id,
                    memory_flag=memory_flag,
                    files=processed_files
            ):
                if isinstance(chunk, int):
                    total_tokens = chunk
                else:
                    full_content += chunk
                    yield self._format_sse_event("message", {"content": chunk})
                    if tts_task is not None:
                        await text_queue.put(chunk)

            # 文本结束，通知 TTS
            if tts_task is not None:
                await text_queue.put(None)

            elapsed_time = time.time() - start_time
            ModelApiKeyService.record_api_key_usage(self.db, api_key_config.get("api_key_id"))

            if sub_agent:
                yield self._format_sse_event("sub_usage", {"total_tokens": total_tokens})

            # 11. 保存会话消息
            if not sub_agent:
                await self._save_conversation_message(
                    conversation_id=conversation_id,
                    user_message=message,
                    assistant_message=full_content,
                    app_id=agent_config.app_id,
                    user_id=user_id,
                    meta_data={
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens}
                    },
                    files=files,
                    audio_url=stream_audio_url
                )

            # 12. 发送结束事件（包含 suggested_questions 和 tts）
            end_data: Dict[str, Any] = {
                "conversation_id": conversation_id,
                "elapsed_time": elapsed_time,
                "message_length": len(full_content)
            }
            if not sub_agent:
                end_data["suggested_questions"] = await self._generate_suggested_questions(
                    features_config, full_content, api_key_config, effective_params
                )
                end_data["audio_url"] = stream_audio_url
                end_data["citations"] = self._filter_citations(features_config, [])
            yield self._format_sse_event("end", end_data)

            logger.info(
                "流式试运行完成",
                extra={
                    "model": model_config.name,
                    "elapsed_time": elapsed_time,
                    "message_length": len(full_content)
                }
            )

        except Exception as e:
            logger.error("流式 Agent 调用失败", extra={"error": str(e)}, exc_info=True)
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

    async def _get_api_key(self, model_config_id: uuid.UUID) -> Dict:
        """获取模型的 API Key

        Args:
            model_config_id: 模型配置ID

        Returns:
            Dict: 包含 model_name, api_key, api_base 的字典

        Raises:
            BusinessException: 当没有可用的 API Key 时
        """
        # api_keys = ModelApiKeyRepository.get_by_model_config(self.db, model_config_id)
        # stmt = (
        #     select(ModelApiKey).join(
        #         ModelConfig, ModelApiKey.model_configs
        #     )
        #     .where(
        #         ModelConfig.id == model_config_id,
        #         ModelApiKey.is_active.is_(True)
        #     )
        #     .order_by(ModelApiKey.priority.desc())
        #     .limit(1)
        # )
        #
        # api_key = self.db.scalars(stmt).first()
        # api_key = api_keys[0] if api_keys else None
        api_key = ModelApiKeyService.get_available_api_key(self.db, model_config_id)

        if not api_key:
            raise BusinessException("没有可用的 API Key", BizCode.AGENT_CONFIG_MISSING)

        return {
            "model_name": api_key.model_name,
            "provider": api_key.provider,
            "api_key": api_key.api_key,
            "api_base": api_key.api_base,
            "api_key_id": api_key.id,
            "is_omni": api_key.is_omni,
            "capability": api_key.capability
        }

    async def _ensure_conversation(
            self,
            conversation_id: Optional[str],
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            user_id: Optional[str],
            sub_agent: bool = False
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

            # 验证会话属于当前工作空间（或属于共享应用的源工作空间）
            # sub_agent 内部调用时跳过校验，已在上层验证过
            if not sub_agent and conversation.workspace_id != workspace_id:
                # 检查是否是共享应用的会话（被共享者 workspace 访问源应用）
                from app.models import AppShare
                from sqlalchemy import select as sa_select
                share = self.db.scalars(
                    sa_select(AppShare).where(
                        AppShare.source_app_id == app_id,
                        AppShare.target_workspace_id == workspace_id
                    )
                ).first()

                # 情况2：sub_agent 内部调用时，workspace_id 是源应用的 workspace，
                # 而会话是被共享者创建的，只要会话属于同一个 app 即可放行
                same_app = (conversation.app_id == app_id)

                if not share and not same_app:
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
            api_config: ModelInfo | None = None,
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

            conversation_service = ConversationService(self.db)
            # 获取 API 配置用于多模态处理
            history = await conversation_service.get_conversation_history(
                conversation_id=uuid.UUID(conversation_id),
                max_history=max_history,
                api_config=api_config
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
            meta_data: dict,
            app_id: Optional[uuid.UUID] = None,
            user_id: Optional[str] = None,
            files: Optional[List[FileInput]] = None,
            audio_url: Optional[str] = None
    ) -> None:
        """保存会话消息（会话已通过 _ensure_conversation 确保存在）

        Args:
            conversation_id: 会话ID
            user_message: 用户消息
            assistant_message: AI 回复消息
            app_id: 应用ID（未使用，保留用于兼容性）
            user_id: 用户ID（未使用，保留用于兼容性）
            meta_data: token消耗
        """
        try:
            from app.services.conversation_service import ConversationService

            conversation_service = ConversationService(self.db)
            conv_uuid = uuid.UUID(conversation_id)

            # 保存消息（会话已经存在）
            human_meta = {
                "files": []
            }
            if files:
                for f in files:
                    # url = await MultimodalService(self.db).get_file_url(f)
                    human_meta["files"].append({
                        "type": f.type,
                        "url": f.url
                    })
            # 保存用户消息
            conversation_service.add_message(
                conversation_id=conv_uuid,
                role="user",
                content=user_message,
                meta_data=human_meta
            )
            # 保存助手消息（含 audio_url）
            if audio_url:
                meta_data["audio_url"] = audio_url
            conversation_service.add_message(
                conversation_id=conv_uuid,
                role="assistant",
                content=assistant_message,
                meta_data=meta_data
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
            logger.debug("获取配置快照失败（可能是多 Agent 应用）", exc_info=True, extra={"error": str(e)})
            return {}

    async def _generate_suggested_questions(
            self,
            features_config: Dict[str, Any],
            assistant_message: str,
            api_key_config: Dict[str, Any],
            effective_params: Dict[str, Any]
    ) -> List[str]:
        """根据 suggested_questions_after_answer 配置生成下一步建议问题"""
        sq_config = features_config.get("suggested_questions_after_answer", {})
        if not isinstance(sq_config, dict) or not sq_config.get("enabled"):
            return []
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage, SystemMessage
            llm = ChatOpenAI(
                model=api_key_config["model_name"],
                api_key=api_key_config["api_key"],
                base_url=api_key_config.get("api_base"),
                temperature=0.5,
                max_tokens=200,
            )
            prompt = (
                f"根据以下AI回复，生成3个用户可能继续追问的简短问题，每行一个，不加序号：\n\n{assistant_message}"
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            lines = [l.strip() for l in resp.content.strip().split("\n") if l.strip()]
            return lines[:3]
        except Exception as e:
            logger.warning(f"生成建议问题失败: {e}")
            return []

    async def _generate_tts(
            self,
            features_config: Dict[str, Any],
            text: str,
            api_key_config: Dict[str, Any],
            tenant_id: Optional[uuid.UUID] = None,
            workspace_id: Optional[uuid.UUID] = None,
    ) -> Optional[str]:
        """先注册文件元数据并返回 audio_url，再后台流式写入音频内容"""
        tts_config = features_config.get("text_to_speech", {})
        if not isinstance(tts_config, dict) or not tts_config.get("enabled"):
            return None
        if not text or not text.strip():
            return None

        from app.models.file_metadata_model import FileMetadata
        from app.services.file_storage_service import FileStorageService, generate_file_key

        provider = api_key_config.get("provider", "openai")
        api_key = api_key_config.get("api_key")
        api_base = api_key_config.get("api_base")
        voice = tts_config.get("voice")
        file_ext, content_type = ".mp3", "audio/mpeg"

        file_id = uuid.uuid4()
        file_key = generate_file_key(tenant_id, workspace_id, file_id, file_ext)

        # 先写入 pending 状态的元数据，立即返回 URL
        db_file = FileMetadata(
            id=file_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            file_key=file_key,
            file_name=f"tts_{file_id}{file_ext}",
            file_ext=file_ext,
            file_size=0,
            content_type=content_type,
            status="pending",
        )
        self.db.add(db_file)
        self.db.commit()

        server_url = settings.FILE_LOCAL_SERVER_URL
        audio_url = f"{server_url}/storage/permanent/{file_id}"

        # 后台任务：流式生成并写入存储，完成后更新状态
        async def _stream_to_storage():
            try:
                storage_service = FileStorageService()
                if provider == "dashscope":
                    stream = self._tts_dashscope_stream(
                        api_key=api_key,
                        text=text,
                        voice=voice or "longxiaochun",
                        tts_config=tts_config,
                    )
                else:
                    stream = self._tts_openai_stream(
                        api_key=api_key,
                        api_base=api_base,
                        text=text,
                        voice=voice or "alloy",
                    )

                total_size = await storage_service.upload_stream(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    file_id=file_id,
                    file_ext=file_ext,
                    stream=stream,
                    content_type=content_type,
                )

                # 更新元数据状态
                with get_db_context() as bg_db:
                    record = bg_db.get(FileMetadata, file_id)
                    if record:
                        record.status = "completed"
                        record.file_size = total_size
                        bg_db.commit()
                logger.debug(f"TTS 流式写入完成，provider={provider}, file_key={file_key}")
            except Exception as e:
                logger.warning(f"TTS 流式写入失败: {e}")
                with get_db_context() as bg_db:
                    record = bg_db.get(FileMetadata, file_id)
                    if record:
                        record.status = "failed"
                        bg_db.commit()

        asyncio.create_task(_stream_to_storage())
        return audio_url

    async def _generate_tts_streaming(
            self,
            features_config: Dict[str, Any],
            api_key_config: Dict[str, Any],
            text_queue: asyncio.Queue,
            tenant_id: Optional[uuid.UUID] = None,
            workspace_id: Optional[uuid.UUID] = None,
    ) -> tuple[Optional[str], Optional[asyncio.Task]]:
        """文本流式输入并行合成音频。
        返回 (audio_url, task)，audio_url 立即可用，task 完成后文件内容就绪。
        调用方向 text_queue put 文本 chunk，结束时 put None。
        """
        tts_config = features_config.get("text_to_speech", {})
        if not isinstance(tts_config, dict) or not tts_config.get("enabled"):
            return None, None

        from app.models.file_metadata_model import FileMetadata
        from app.services.file_storage_service import FileStorageService, generate_file_key

        provider = api_key_config.get("provider", "openai")
        api_key = api_key_config.get("api_key")
        api_base = api_key_config.get("api_base")
        voice = tts_config.get("voice")
        file_ext, content_type = ".mp3", "audio/mpeg"

        file_id = uuid.uuid4()
        file_key = generate_file_key(tenant_id, workspace_id, file_id, file_ext)

        db_file = FileMetadata(
            id=file_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            file_key=file_key,
            file_name=f"tts_{file_id}{file_ext}",
            file_ext=file_ext,
            file_size=0,
            content_type=content_type,
            status="pending",
        )
        self.db.add(db_file)
        self.db.commit()

        server_url = settings.FILE_LOCAL_SERVER_URL
        audio_url = f"{server_url}/storage/permanent/{file_id}"

        async def _run():
            try:
                storage_service = FileStorageService()
                if provider == "dashscope":
                    audio_stream = self._tts_dashscope_stream_from_queue(
                        api_key=api_key,
                        voice=voice or "longxiaochun",
                        tts_config=tts_config,
                        text_queue=text_queue,
                    )
                else:
                    audio_stream = self._tts_openai_stream_from_queue(
                        api_key=api_key,
                        api_base=api_base,
                        voice=voice or "alloy",
                        text_queue=text_queue,
                    )
                total_size = await storage_service.upload_stream(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    file_id=file_id,
                    file_ext=file_ext,
                    stream=audio_stream,
                    content_type=content_type,
                )
                with get_db_context() as bg_db:
                    record = bg_db.get(FileMetadata, file_id)
                    if record:
                        record.status = "completed"
                        record.file_size = total_size
                        bg_db.commit()
                logger.debug(f"TTS 流式合成完成，provider={provider}, file_key={file_key}")
            except Exception as e:
                logger.warning(f"TTS 流式合成失败: {e}")
                with get_db_context() as bg_db:
                    record = bg_db.get(FileMetadata, file_id)
                    if record:
                        record.status = "failed"
                        bg_db.commit()

        task = asyncio.create_task(_run())
        return audio_url, task

    @staticmethod
    async def _tts_openai_stream_from_queue(
            api_key: str,
            api_base: Optional[str],
            voice: str,
            text_queue: asyncio.Queue,
    ):
        """OpenAI TTS：收集全部文本后流式合成（OpenAI 不支持增量输入）"""
        from openai import AsyncOpenAI
        # 收集全部文本（此时文本流已并行输出，等待时间短）
        parts = []
        while True:
            chunk = await text_queue.get()
            if chunk is None:
                break
            parts.append(chunk)
        full_text = "".join(parts)
        if not full_text.strip():
            return
        client = AsyncOpenAI(api_key=api_key, base_url=api_base)
        async with client.audio.speech.with_streaming_response.create(
            model="tts-1",
            voice=voice,
            input=full_text[:4096],
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=4096):
                yield chunk

    @staticmethod
    async def _tts_dashscope_stream_from_queue(
            api_key: str,
            voice: str,
            tts_config: Dict[str, Any],
            text_queue: asyncio.Queue,
    ):
        """DashScope TTS：文本流式输入，实现真正并行合成"""
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat, ResultCallback

        model = tts_config.get("model") or "cosyvoice-v2"
        is_v2 = model.endswith("-v2")
        if is_v2 and not voice.endswith("_v2"):
            voice = voice + "_v2"
        elif not is_v2 and voice.endswith("_v2"):
            voice = voice[:-3]

        audio_queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        class _Callback(ResultCallback):
            def on_data(self, data: bytes):
                if data:
                    loop.call_soon_threadsafe(audio_queue.put_nowait, data)
            def on_complete(self):
                loop.call_soon_threadsafe(audio_queue.put_nowait, None)
            def on_error(self, message):
                loop.call_soon_threadsafe(audio_queue.put_nowait, RuntimeError(str(message)))
            def on_open(self): pass
            def on_close(self): pass

        dashscope.api_key = api_key
        synthesizer = SpeechSynthesizer(
            model=model,
            voice=voice,
            format=AudioFormat.MP3_22050HZ_MONO_256KBPS,
            callback=_Callback(),
        )

        async def _feed_text():
            """从 text_queue 取文本按句子切分后喂给 synthesizer"""
            import re
            buf = ""
            sentence_end = re.compile(r'[\u3002\uff01\uff1f\.!?\n]')
            while True:
                chunk = await text_queue.get()
                if chunk is None:
                    if buf.strip():
                        await asyncio.to_thread(synthesizer.streaming_call, buf)
                    await asyncio.to_thread(synthesizer.streaming_complete)
                    break
                buf += chunk
                # 按句子切分喂入
                while sentence_end.search(buf):
                    m = sentence_end.search(buf)
                    sentence = buf[:m.end()]
                    buf = buf[m.end():]
                    await asyncio.to_thread(synthesizer.streaming_call, sentence)

        asyncio.create_task(_feed_text())

        while True:
            item = await audio_queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    @staticmethod
    async def _tts_openai_stream(
            api_key: str,
            api_base: Optional[str],
            text: str,
            voice: str,
    ):
        """OpenAI 兼容 TTS 流式生成，yield bytes chunks"""
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=api_base)
        async with client.audio.speech.with_streaming_response.create(
            model="tts-1",
            voice=voice,
            input=text[:4096],
        ) as response:
            async for chunk in response.iter_bytes(chunk_size=4096):
                yield chunk

    @staticmethod
    async def _tts_dashscope_stream(
            api_key: str,
            text: str,
            voice: str,
            tts_config: Dict[str, Any],
    ):
        """DashScope TTS 流式生成，yield bytes chunks"""
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer, AudioFormat, ResultCallback

        model = tts_config.get("model") or "cosyvoice-v2"
        is_v2 = model.endswith("-v2")
        if is_v2 and not voice.endswith("_v2"):
            voice = voice + "_v2"
        elif not is_v2 and voice.endswith("_v2"):
            voice = voice[:-3]

        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        class _Callback(ResultCallback):
            def on_data(self, data: bytes):
                if data:
                    loop.call_soon_threadsafe(queue.put_nowait, data)
            def on_complete(self):
                loop.call_soon_threadsafe(queue.put_nowait, None)
            def on_error(self, message):
                loop.call_soon_threadsafe(queue.put_nowait, RuntimeError(str(message)))
            def on_open(self): pass
            def on_close(self): pass

        def _sync_stream():
            dashscope.api_key = api_key
            synthesizer = SpeechSynthesizer(
                model=model,
                voice=voice,
                format=AudioFormat.MP3_22050HZ_MONO_256KBPS,
                callback=_Callback(),
            )
            synthesizer.streaming_call(text[:4096])
            synthesizer.streaming_complete()

        asyncio.create_task(asyncio.to_thread(_sync_stream))
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item

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
                f"{{{var_name}}}",  # {var_name}
                f"${{{var_name}}}",  # ${var_name}
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
            files: list[FileInput] | None = None
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

        # 提前校验文件上传（与 run() 内部保持一致）
        features_config: dict = agent_config.features or {}
        if hasattr(features_config, 'model_dump'):
            features_config = features_config.model_dump()
        # self._validate_file_upload(features_config, files)

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
                            memory=memory,
                            files=files
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
                    "conversation_id": result['conversation_id'],
                    "parameters_used": model_info["parameters"],
                    "message": result.get("message"),
                    "usage": usage,
                    "elapsed_time": elapsed,
                    "tokens_per_second": (
                        usage.get("completion_tokens", 0) / elapsed
                        if elapsed > 0 and usage.get("completion_tokens") else None
                    ),
                    "cost_estimate": self._estimate_cost(usage, model_info["model_config"]),
                    "audio_url": result.get("audio_url"),
                    "citations": result.get("citations", []),
                    "suggested_questions": result.get("suggested_questions", []),
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
            "results": [{
                **r,
                "audio_url": r.get("audio_url"),
                "citations": r.get("citations", []),
                "suggested_questions": r.get("suggested_questions", []),
            } for r in results],
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
            timeout: int = 60,
            files: list[FileInput] | None = None
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
            files: 多模态文件

        Yields:
            str: SSE 格式的事件数据
        """
        logger.info(
            "多模型对比流式试运行",
            extra={"model_count": len(models), "parallel": parallel}
        )

        # 提前校验文件上传
        # features_config: dict = agent_config.features or {}
        # if hasattr(features_config, 'model_dump'):
        #     features_config = features_config.model_dump()
        # self._validate_file_upload(features_config, files)

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
                audio_url = None
                citations = []
                suggested_questions = []

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
                            memory=memory,
                            files=files
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

                            # 从 end 事件中提取 features 输出字段
                            if event_type == "end" and event_data:
                                audio_url = event_data.get("audio_url")
                                citations = event_data.get("citations", [])
                                suggested_questions = event_data.get("suggested_questions", [])

                            if event_type == "error" and event_data:
                                await event_queue.put(self._format_sse_event("model_error", {
                                    "model_index": idx,
                                    "model_config_id": model_config_id,
                                    "label": model_label,
                                    "conversation_id": returned_conversation_id,
                                    "error": event_data.get("error", "未知错误")
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
                    "audio_url": audio_url,
                    "citations": citations,
                    "suggested_questions": suggested_questions,
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
                    "audio_url": audio_url,
                    "citations": citations,
                    "suggested_questions": suggested_questions,
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
                "message": r.get("message"),
                "elapsed_time": r.get("elapsed_time", 0),
                "audio_url": r.get("audio_url"),
                "citations": r.get("citations", []),
                "suggested_questions": r.get("suggested_questions", []),
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

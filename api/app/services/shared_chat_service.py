"""基于分享链接的聊天服务"""
import uuid
import time
import asyncio
from typing import Optional, Dict, Any, AsyncGenerator
from sqlalchemy.orm import Session
from app.services.memory_konwledges_server import write_rag
from app.models import ReleaseShare, AppRelease, Conversation
from app.services.conversation_service import ConversationService
from app.services.draft_run_service import create_web_search_tool
from app.services.release_share_service import ReleaseShareService
from app.core.exceptions import BusinessException, ResourceNotFoundException
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.services.multi_agent_service import MultiAgentService
from app.models import MultiAgentConfig
from app.repositories import knowledge_repository
import json
from app.services.task_service import get_task_memory_write_result
from app.tasks import write_message_task
logger = get_business_logger()


class SharedChatService:
    """基于分享链接的聊天服务"""
    
    def __init__(self, db: Session):
        self.db = db
        self.conversation_service = ConversationService(db)
        self.share_service = ReleaseShareService(db)
    
    def _get_release_by_share_token(
        self,
        share_token: str,
        password: Optional[str] = None
    ) -> tuple[ReleaseShare, AppRelease]:
        """通过 share_token 获取发布版本"""
        # 获取分享配置
        share = self.share_service.repo.get_by_share_token(share_token)
        if not share:
            raise ResourceNotFoundException("分享链接", share_token)
        
        # 验证分享是否启用
        if not share.is_enabled:
            raise BusinessException("该分享链接已被禁用", BizCode.SHARE_DISABLED)
        
        # 验证密码
        if share.require_password:
            if not password:
                raise BusinessException("需要提供访问密码", BizCode.PASSWORD_REQUIRED)
            
            if not self.share_service.verify_password(share_token, password):
                raise BusinessException("访问密码错误", BizCode.INVALID_PASSWORD)
        
        # 获取发布版本
        release = self.db.get(AppRelease, share.release_id)
        if not release:
            raise ResourceNotFoundException("发布版本", str(share.release_id))
        
        # 更新访问统计
        try:
            self.share_service.repo.increment_view_count(share.id)
        except Exception as e:
            logger.warning(f"更新访问统计失败: {str(e)}")
        
        return share, release
    
    def create_or_get_conversation(
        self,
        share_token: str,
        conversation_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
        password: Optional[str] = None
    ) -> Conversation:
        """创建或获取会话"""
        share, release = self._get_release_by_share_token(share_token, password)
        
        # 如果提供了 conversation_id，尝试获取现有会话
        if conversation_id:
            try:
                conversation = self.conversation_service.get_conversation(
                    conversation_id=conversation_id,
                    workspace_id=release.app.workspace_id
                )
                
                # 验证会话是否属于该应用
                if conversation.app_id != release.app_id:
                    raise BusinessException("会话不属于该应用", BizCode.INVALID_CONVERSATION)
                
                return conversation
            except ResourceNotFoundException:
                logger.warning(
                    "会话不存在，将创建新会话",
                    extra={"conversation_id": str(conversation_id)}
                )
        
        # 创建新会话（使用发布版本的配置）
        conversation = self.conversation_service.create_conversation(
            app_id=release.app_id,
            workspace_id=release.app.workspace_id,
            user_id=user_id,
            is_draft=False,  # 分享链接使用发布版本
            config_snapshot=release.config
        )
        
        logger.info(
            "为分享链接创建新会话",
            extra={
                "conversation_id": str(conversation.id),
                "share_token": share_token,
                "release_id": str(release.id)
            }
        )
        
        return conversation
    
    async def chat(
        self,
        share_token: str,
        message: str,
        conversation_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        password: Optional[str] = None,
        web_search: bool = False,
        memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """聊天（非流式）"""
        actual_config_id = None
        config_id=actual_config_id
        from app.core.agent.langchain_agent import LangChainAgent
        from app.services.draft_run_service import create_knowledge_retrieval_tool, create_long_term_memory_tool
        from app.services.model_parameter_merger import ModelParameterMerger
        from app.schemas.prompt_schema import render_prompt_message, PromptMessageRole
        from sqlalchemy import select
        from app.models import ModelApiKey

        
        start_time = time.time()
        actual_config_id=None
        config_id=actual_config_id
        
        if variables is None:
            variables = {}
        
        # 获取发布版本和配置
        share, release = self._get_release_by_share_token(share_token, password)
        
        # 获取 Agent 配置
        config = release.config or {}

        
        # 获取模型配置ID
        model_config_id = release.default_model_config_id
        if not model_config_id:
            raise BusinessException("发布版本未配置模型", BizCode.AGENT_CONFIG_MISSING)
        
        # 获取模型配置
        from app.models import ModelConfig
        model_config = self.db.get(ModelConfig, model_config_id)
        if not model_config:
            raise ResourceNotFoundException("模型配置", str(model_config_id))
        
        # 获取 API Key
        stmt = (
            select(ModelApiKey)
            .where(
                ModelApiKey.model_config_id == model_config_id,
                ModelApiKey.is_active == True
            )
            .order_by(ModelApiKey.priority.desc())
            .limit(1)
        )
        api_key_obj = self.db.scalars(stmt).first()
        if not api_key_obj:
            raise BusinessException("没有可用的 API Key", BizCode.AGENT_CONFIG_MISSING)
        
        # 获取或创建会话
        conversation = self.create_or_get_conversation(
            share_token=share_token,
            conversation_id=conversation_id,
            user_id=user_id,
            password=password
        )
        
        # 处理系统提示词（支持变量替换）
        system_prompt = config.get("system_prompt", "你是一个专业的AI助手")
        if variables:
            system_prompt_rendered = render_prompt_message(
                system_prompt,
                PromptMessageRole.USER,
                variables
            )
            system_prompt = system_prompt_rendered.get_text_content() or system_prompt
        
        # 准备工具列表
        tools = []
        
        # 添加知识库检索工具
        knowledge_retrieval = config.get("knowledge_retrieval")
        if knowledge_retrieval:
            knowledge_bases = knowledge_retrieval.get("knowledge_bases", [])
            kb_ids = [kb.get("kb_id") for kb in knowledge_bases if kb.get("kb_id")]
            if kb_ids:
                kb_tool = create_knowledge_retrieval_tool(knowledge_retrieval, kb_ids,user_id)
                tools.append(kb_tool)

        # 添加长期记忆工具
        memory_flag=False
        if memory==True:
            memory_config = config.get("memory", {})
            if memory_config.get("enabled") and user_id:
                memory_flag=True
                memory_tool = create_long_term_memory_tool(memory_config, user_id)
                tools.append(memory_tool)

        web_tools=config.get("tools")
        web_search_choice = web_tools.get("web_search", {})
        web_search_enable = web_search_choice.get("enabled",False)
        if web_search==True:
            if web_search_enable==True:
                search_tool = create_web_search_tool({})
                tools.append(search_tool)

                logger.debug(
                    "已添加网络搜索工具",
                    extra={
                        "tool_count": len(tools)
                    }
                )
        
        # 获取模型参数
        model_parameters = config.get("model_parameters", {})
        
        # 创建 LangChain Agent
        agent = LangChainAgent(
            model_name=api_key_obj.model_name,
            api_key=api_key_obj.api_key,
            provider=api_key_obj.provider,
            api_base=api_key_obj.api_base,
            temperature=model_parameters.get("temperature", 0.7),
            max_tokens=model_parameters.get("max_tokens", 2000),
            system_prompt=system_prompt,
            tools=tools,

        )
        
        # 加载历史消息
        history = []
        memory_config={"enabled":True,'max_history':10}
        if memory_config.get("enabled"):
            messages = self.conversation_service.get_messages(
                conversation_id=conversation.id,
                limit=memory_config.get("max_history", 10)
            )
            history = [
                {"role": msg.role, "content": msg.content}
                for msg in messages
            ]
        
        # 调用 Agent
        result = await agent.chat(
            message=message,
            history=history,
            context=None,
            end_user_id=user_id,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            config_id=config_id,
            memory_flag=memory_flag
        )
        
        # 保存消息
        self.conversation_service.save_conversation_messages(
            conversation_id=conversation.id,
            user_message=message,
            assistant_message=result["content"]
        )
        # self.conversation_service.add_message(
        #     conversation_id=conversation.id,
        #     role="user",
        #     content=message
        # )
        
        # self.conversation_service.add_message(
        #     conversation_id=conversation.id,
        #     role="assistant",
        #     content=result["content"],
        #     meta_data={
        #         "model": api_key_obj.model_name,
        #         "usage": result.get("usage", {})
        #     }
        # )
        
        elapsed_time = time.time() - start_time

        
        return {
            "conversation_id": conversation.id,
            "message": result["content"],
            "usage": result.get("usage", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }),
            "elapsed_time": elapsed_time
        }
    
    async def chat_stream(
        self,
        share_token: str,
        message: str,
        conversation_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        password: Optional[str] = None,
        web_search: bool = False,
        memory: bool = True,
        storage_type:Optional[str] = None,
        user_rag_memory_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """聊天（流式）"""
        from app.core.agent.langchain_agent import LangChainAgent
        from app.services.draft_run_service import create_knowledge_retrieval_tool, create_long_term_memory_tool
        from app.schemas.prompt_schema import render_prompt_message, PromptMessageRole
        from sqlalchemy import select
        from app.models import ModelApiKey
        import json
        
        start_time = time.time()
        actual_config_id=None
        config_id=actual_config_id

        
        if variables is None:
            variables = {}
        memory_config = {"enabled": memory, "memory_content": "17", "max_history": 10}
        
        try:
            # 获取发布版本和配置
            share, release = self._get_release_by_share_token(share_token, password)
            
            # 获取 Agent 配置
            config = release.config or {}
            agent_config_data = config.get("agent_config", {})
            
            # 获取模型配置ID
            model_config_id = release.default_model_config_id
            if not model_config_id:
                raise BusinessException("发布版本未配置模型", BizCode.AGENT_CONFIG_MISSING)
            
            # 获取模型配置
            from app.models import ModelConfig
            model_config = self.db.get(ModelConfig, model_config_id)
            if not model_config:
                raise ResourceNotFoundException("模型配置", str(model_config_id))
            
            # 获取 API Key
            stmt = (
                select(ModelApiKey)
                .where(
                    ModelApiKey.model_config_id == model_config_id,
                    ModelApiKey.is_active == True
                )
                .order_by(ModelApiKey.priority.desc())
                .limit(1)
            )
            api_key_obj = self.db.scalars(stmt).first()
            if not api_key_obj:
                raise BusinessException("没有可用的 API Key", BizCode.AGENT_CONFIG_MISSING)
            
            # 获取或创建会话
            conversation = self.create_or_get_conversation(
                share_token=share_token,
                conversation_id=conversation_id,
                user_id=user_id,
                password=password
            )
            
            # 处理系统提示词（支持变量替换）
            system_prompt = config.get("system_prompt", "你是一个专业的AI助手")
            if variables:
                system_prompt_rendered = render_prompt_message(
                    system_prompt,
                    PromptMessageRole.USER,
                    variables
                )
                system_prompt = system_prompt_rendered.get_text_content() or system_prompt
            
            # 准备工具列表
            tools = []
            
            # 添加知识库检索工具
            knowledge_retrieval = config.get("knowledge_retrieval")
            if knowledge_retrieval:
                knowledge_bases = knowledge_retrieval.get("knowledge_bases", [])
                kb_ids = [kb.get("kb_id") for kb in knowledge_bases if kb.get("kb_id")]
                if kb_ids:
                    kb_tool = create_knowledge_retrieval_tool(knowledge_retrieval, kb_ids,user_id)
                    tools.append(kb_tool)
            
            # 添加长期记忆工具
            memory_flag=False
            if memory:
                memory_config = config.get("memory", {})
                if memory_config.get("enabled") and user_id:
                    memory_flag = True
                    memory_tool = create_long_term_memory_tool(memory_config, user_id)
                    tools.append(memory_tool)

            web_tools = config.get("tools")
            web_search_choice = web_tools.get("web_search", {})
            web_search_enable = web_search_choice.get("enabled", False)
            if web_search == True:
                if web_search_enable == True:
                    search_tool = create_web_search_tool({})
                    tools.append(search_tool)

                    logger.debug(
                        "已添加网络搜索工具",
                        extra={
                            "tool_count": len(tools)
                        }
                    )

            # 获取模型参数
            model_parameters = config.get("model_parameters", {})
            
            # 创建 LangChain Agent
            agent = LangChainAgent(
                model_name=api_key_obj.model_name,
                api_key=api_key_obj.api_key,
                provider=api_key_obj.provider,
                api_base=api_key_obj.api_base,
                temperature=model_parameters.get("temperature", 0.7),
                max_tokens=model_parameters.get("max_tokens", 2000),
                system_prompt=system_prompt,
                tools=tools,
                streaming=True
            )
            
            # 加载历史消息
            history = []
            memory_config = {"enabled": True, 'max_history': 10}
            if memory_config.get("enabled"):
                messages = self.conversation_service.get_messages(
                    conversation_id=conversation.id,
                    limit=memory_config.get("max_history", 10)
                )
                history = [
                    {"role": msg.role, "content": msg.content}
                    for msg in messages
                ]
            
            # 发送开始事件
            yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation.id)}, ensure_ascii=False)}\n\n"
            
            # 流式调用 Agent
            full_content = ""
            async for chunk in agent.chat_stream(
                message=message,
                history=history,
                context=None,
                end_user_id=user_id,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                config_id=config_id,
                memory_flag=memory_flag
            ):
                full_content += chunk
                # 发送消息块事件
                yield f"event: message\ndata: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
            
            elapsed_time = time.time() - start_time
            
            # 保存消息
            self.conversation_service.add_message(
                conversation_id=conversation.id,
                role="user",
                content=message
            )
            
            self.conversation_service.add_message(
                conversation_id=conversation.id,
                role="assistant",
                content=full_content,
                meta_data={
                    "model": api_key_obj.model_name,
                    "usage": {}
                }
            )

            
            # 发送结束事件
            end_data = {"elapsed_time": elapsed_time, "message_length": len(full_content)}
            yield f"event: end\ndata: {json.dumps(end_data, ensure_ascii=False)}\n\n"
            
            logger.info(
                "流式聊天完成",
                extra={
                    "conversation_id": str(conversation.id),
                    "elapsed_time": elapsed_time,
                    "message_length": len(full_content)
                }
            )
            
        except (GeneratorExit, asyncio.CancelledError):
            # 生成器被关闭或任务被取消，正常退出
            logger.debug("流式聊天被中断")
            raise
        except Exception as e:
            logger.error(f"流式聊天失败: {str(e)}", exc_info=True)
            # 发送错误事件
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
    
    def get_conversation_messages(
        self,
        share_token: str,
        conversation_id: uuid.UUID,
        password: Optional[str] = None
    ) -> Conversation:
        """获取会话消息"""
        share, release = self._get_release_by_share_token(share_token, password)
        
        # 获取会话
        conversation = self.conversation_service.get_conversation(
            conversation_id=conversation_id,
            workspace_id=release.app.workspace_id
        )
        
        # 验证会话是否属于该应用
        if conversation.app_id != release.app_id:
            raise BusinessException("会话不属于该应用", BizCode.INVALID_CONVERSATION)
        
        return conversation
    
    def list_conversations(
        self,
        share_token: str,
        user_id: Optional[str] = None,
        password: Optional[str] = None,
        page: int = 1,
        pagesize: int = 20
    ) -> tuple[list[Conversation], int]:
        """列出会话"""
        share, release = self._get_release_by_share_token(share_token, password)
        
        conversations, total = self.conversation_service.list_conversations(
            app_id=release.app_id,
            workspace_id=release.app.workspace_id,
            user_id=user_id,
            is_draft=False,  # 只显示发布版本的会话
            page=page,
            pagesize=pagesize
        )
        
        return conversations, total
    
    async def multi_agent_chat(
        self,
        share_token: str,
        message: str,
        conversation_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        password: Optional[str] = None,
        web_search: bool = False,
        memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """多 Agent 聊天（非流式）"""
        from app.services.multi_agent_service import MultiAgentService
        from app.models import MultiAgentConfig


        
        start_time = time.time()
        actual_config_id=None
        config_id=actual_config_id
        
        if variables is None:
            variables = {}
        
        # 获取发布版本和配置
        share, release = self._get_release_by_share_token(share_token, password)
        
        # 获取或创建会话
        conversation = self.create_or_get_conversation(
            share_token=share_token,
            conversation_id=conversation_id,
            user_id=user_id,
            password=password
        )
        
        # 获取多 Agent 配置
        multi_agent_config = self.db.query(MultiAgentConfig).filter(
            MultiAgentConfig.app_id == release.app_id,
            MultiAgentConfig.is_active == True
        ).first()
        
        if not multi_agent_config:
            raise BusinessException("多 Agent 配置不存在", BizCode.AGENT_CONFIG_MISSING)
        
        # 构建多 Agent 运行请求
        from app.schemas.multi_agent_schema import MultiAgentRunRequest
        
        multi_agent_request = MultiAgentRunRequest(
            message=message,
            conversation_id=conversation.id,
            user_id=user_id,
            variables=variables,
            use_llm_routing=True,
            web_search=web_search,
            memory=memory
        )
        
        # 使用多 Agent 服务执行
        multi_agent_service = MultiAgentService(self.db)
        result = await multi_agent_service.run(
            app_id=release.app_id,
            request=multi_agent_request
        )
        
        elapsed_time = time.time() - start_time
        
        # 保存消息
        self.conversation_service.add_message(
            conversation_id=conversation.id,
            role="user",
            content=message
        )
        
        self.conversation_service.add_message(
            conversation_id=conversation.id,
            role="assistant",
            content=result.get("message", ""),
            meta_data={
                "mode": result.get("mode"),
                "elapsed_time": result.get("elapsed_time"),
                "sub_results": result.get("sub_results")
            }
        )


        
        return {
            "conversation_id": conversation.id,
            "message": result.get("message", ""),
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            },
            "elapsed_time": elapsed_time
        }
    
    async def multi_agent_chat_stream(
        self,
        share_token: str,
        message: str,
        conversation_id: Optional[uuid.UUID] = None,
        user_id: Optional[str] = None,
        variables: Optional[Dict[str, Any]] = None,
        password: Optional[str] = None,
        web_search: bool = False,
        memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id:Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """多 Agent 聊天（流式）"""

        
        start_time = time.time()
        actual_config_id=None
        config_id=actual_config_id
        
        if variables is None:
            variables = {}
        
        try:
            # 获取发布版本和配置
            share, release = self._get_release_by_share_token(share_token, password)
            
            # 获取或创建会话
            conversation = self.create_or_get_conversation(
                share_token=share_token,
                conversation_id=conversation_id,
                user_id=user_id,
                password=password
            )
            
            # 获取多 Agent 配置
            multi_agent_config = self.db.query(MultiAgentConfig).filter(
                MultiAgentConfig.app_id == release.app_id,
                MultiAgentConfig.is_active == True
            ).first()
            
            if not multi_agent_config:
                raise BusinessException("多 Agent 配置不存在", BizCode.AGENT_CONFIG_MISSING)
            
            # 获取 storage_type 和 user_rag_memory_id
            workspace_id = release.app.workspace_id
            storage_type = 'neo4j'  # 默认值
            user_rag_memory_id = ''
            
            try:
                # 获取工作空间的存储类型（不需要用户权限检查，因为是公开分享）
                from app.models import Workspace
                workspace = self.db.get(Workspace, workspace_id)
                if workspace and workspace.storage_type:
                    storage_type = workspace.storage_type
                
                # 获取 USER_RAG_MERORY 知识库 ID
                knowledge = knowledge_repository.get_knowledge_by_name(
                    db=self.db,
                    name="USER_RAG_MERORY",
                    workspace_id=workspace_id
                )
                if knowledge:
                    user_rag_memory_id = str(knowledge.id)
            except Exception as e:
                logger.warning(f"获取 storage_type 或 user_rag_memory_id 失败，使用默认值: {str(e)}")
            
            # 发送开始事件
            yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation.id)}, ensure_ascii=False)}\n\n"
            
            # 构建多 Agent 运行请求
            from app.schemas.multi_agent_schema import MultiAgentRunRequest
            
            multi_agent_request = MultiAgentRunRequest(
                message=message,
                conversation_id=conversation.id,
                user_id=user_id,
                variables=variables,
                use_llm_routing=True,
                web_search=web_search,
                memory=memory
            )
            
            # 使用多 Agent 服务流式执行
            multi_agent_service = MultiAgentService(self.db)
            full_content = ""
            
            async for event in multi_agent_service.run_stream(
                app_id=release.app_id,
                request=multi_agent_request,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id
            ):
                # 直接转发事件
                yield event
                
                # 尝试提取内容（用于保存）
                if "data:" in event:
                    try:
                        data_line = event.split("data: ", 1)[1].strip()
                        data = json.loads(data_line)
                        if "content" in data:
                            full_content += data["content"]
                    except:
                        pass
            
            elapsed_time = time.time() - start_time
            
            # 保存消息
            self.conversation_service.add_message(
                conversation_id=conversation.id,
                role="user",
                content=message
            )
            
            self.conversation_service.add_message(
                conversation_id=conversation.id,
                role="assistant",
                content=full_content,
                meta_data={
                    "elapsed_time": elapsed_time
                }
            )
            
            logger.info(
                "多 Agent 流式聊天完成",
                extra={
                    "conversation_id": str(conversation.id),
                    "elapsed_time": elapsed_time,
                    "message_length": len(full_content)
                }
            )

            
        except (GeneratorExit, asyncio.CancelledError):
            # 生成器被关闭或任务被取消，正常退出
            logger.debug("多 Agent 流式聊天被中断")
            raise
        except Exception as e:
            logger.error(f"多 Agent 流式聊天失败: {str(e)}", exc_info=True)
            # 发送错误事件
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

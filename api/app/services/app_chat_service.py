"""基于分享链接的聊天服务"""
import asyncio
import json
import time
import uuid
from typing import Optional, Dict, Any, AsyncGenerator, Annotated, List
from datetime import datetime

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.agent.langchain_agent import LangChainAgent
from app.core.utils.datetime_utils import parse_timestamp_to_utc_naive, utcnow_naive
from app.core.logging_config import get_business_logger
from app.core.exceptions import BusinessException
from app.core.error_codes import BizCode
from app.db import get_db, get_async_db_context
from app.models import (
    App,
    MultiAgentConfig, AgentConfig, ModelType, WorkflowConfig,
    ModelCapability, AgentExecution, Message, Conversation)
from app.repositories.agent_execution_repository import AgentExecutionRepository
from app.repositories.tool_repository import ToolRepository
from app.schemas import DraftRunRequest
from app.schemas.app_schema import FileInput, FileType, TransferMethod
from app.schemas.model_schema import ModelInfo
from app.schemas.prompt_schema import render_prompt_message, PromptMessageRole
from app.services.annotation_service import AnnotationService
from app.services.conversation_service import ConversationService
from app.services.context_engine_manager import ContextEngineManager
from app.core.config import settings
from app.services.draft_run_service import AgentRunService
from app.services.model_service import ModelApiKeyService
from app.services.multi_agent_orchestrator import MultiAgentOrchestrator
from app.services.multimodal_service import MultimodalService
from app.services.workflow_service import WorkflowService
from app.models.file_metadata_model import FileMetadata
from app.services.tool_orchestrator import ToolOrchestrator

logger = get_business_logger()


class CustomJsonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        # 原生抛出你看到的那段报错
        return super().default(obj)


def assert_not_opening_statement(db, message_id: uuid.UUID) -> None:
    """开场白（会话第一条 assistant 消息、无 parent_message_id 且无前置用户消息）
    不支持重新生成——重新生成依赖父用户消息复现上下文。

    必须在 StreamingResponse 之前调用：SSE 一旦开流（200 OK）后，generator 内抛出的
    异常无法被 FastAPI 转成干净的 HTTP 错误，前端 handleSSE 也无法弹 toast，会直接白屏。
    在此提前抛 BusinessException → 400 → 前端 handleSSE 自动 message.warning(msg)。

    软删除的 user 消息也算作"前置提问"——后续 _locate_or_restore_parent_user_message
    会把它恢复为 is_deleted=False 后继续重新生成。直接过滤掉会让"用户误删提问后想重新
    生成"被误判为开场白并报错。
    """
    from sqlalchemy import select
    from app.models import Message
    msg = db.get(Message, message_id)
    # 仅关心 assistant 消息；非 assistant 或不存在交给后续 regenerate 内的校验处理
    if not msg or msg.role != "assistant":
        return
    # 有父用户消息则不是开场白
    if msg.parent_message_id:
        return
    # 无父消息时复用 regenerate_stream 的回退逻辑：查前置用户消息；都没有即为开场白
    preceding_user = db.scalars(
        select(Message).where(
            Message.conversation_id == msg.conversation_id,
            Message.role == "user",
            Message.created_at <= msg.created_at,
        ).order_by(Message.created_at.desc()).limit(1)
    ).first()
    if not preceding_user:
        raise BusinessException("该消息是开场白，不支持重新生成", BizCode.BAD_REQUEST)


class AppChatService:
    """基于分享链接的聊天服务"""

    def __init__(self, db: Session | AsyncSession):
        self.db = db
        self.conversation_service = ConversationService(db)
        self.agent_service = AgentRunService(db)
        self.workflow_service = WorkflowService(db)

    def _uses_async_session(self) -> bool:
        return isinstance(self.db, AsyncSession)

    def _resolve_tenant_id(self, workspace_id: Optional[str]) -> Optional[uuid.UUID]:
        if not workspace_id:
            return None
        return ToolRepository.get_tenant_id_by_workspace_id(self.db, str(workspace_id))

    async def _resolve_tenant_id_async(self, workspace_id: Optional[str]) -> Optional[uuid.UUID]:
        if not workspace_id:
            return None
        if self._uses_async_session():
            return await ToolRepository.get_tenant_id_by_workspace_id_async(self.db, str(workspace_id))
        return self._resolve_tenant_id(workspace_id)

    async def _resolve_app_tenant_id_async(self, app_id: uuid.UUID) -> Optional[uuid.UUID]:
        if not self._uses_async_session():
            return self._resolve_app_tenant_id(app_id)

        async with get_async_db_context() as db:
            app = await db.get(App, app_id)
            if not app:
                return None
            return await ToolRepository.get_tenant_id_by_workspace_id_async(db, str(app.workspace_id))

    def _resolve_app_tenant_id(self, app_id: uuid.UUID) -> Optional[uuid.UUID]:
        app = self.db.get(App, app_id)
        if not app:
            return None
        return self._resolve_tenant_id(str(app.workspace_id))

    async def _db_get(self, model, identity):
        if self._uses_async_session():
            return await self.db.get(model, identity)
        return self.db.get(model, identity)

    async def _fetch_completed_file_metadata(self, local_ids: list[uuid.UUID]) -> dict[str, FileMetadata]:
        if not local_ids:
            return {}
        async with get_async_db_context() as db:
            result = await db.execute(
                select(FileMetadata).where(
                    FileMetadata.id.in_(local_ids),
                    FileMetadata.status == "completed",
                )
            )
            rows = result.scalars().all()
        return {str(row.id): row for row in rows}

    async def _conversation_has_messages(self, conversation_id: uuid.UUID) -> bool:
        if self._uses_async_session():
            result = await self.db.execute(
                select(Message.id)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.is_deleted.is_not(True),
                )
                .order_by(Message.created_at)
                .limit(1)
            )
            return result.first() is not None
        existing_messages = self.conversation_service.message_repo.get_message_by_conversation_id(
            conversation_id=conversation_id,
            limit=1,
        )
        return len(existing_messages) > 0

    async def _record_api_key_usage(self, api_key_id: uuid.UUID | None) -> bool:
        if not api_key_id:
            return False
        if self._uses_async_session():
            async with get_async_db_context() as db:
                return await ModelApiKeyService.record_api_key_usage_bridge_async(db, api_key_id)
        return await ModelApiKeyService.record_api_key_usage_bridge_async(self.db, api_key_id)

    async def _release_db_connection(self) -> None:
        if self._uses_async_session():
            await self.db.close()
        else:
            self.db.close()

    @staticmethod
    def _append_history_message(
            history: Optional[List[dict]],
            *,
            role: str,
            content: Any,
    ) -> List[dict]:
        next_history = list(history or [])
        next_history.append({"role": role, "content": content})
        return next_history

    async def _create_agent_execution(self, repo: AgentExecutionRepository, execution: AgentExecution) -> uuid.UUID:
        if self._uses_async_session():
            async with get_async_db_context() as db:
                db.add(execution)
                await db.commit()
                await db.refresh(execution)
                return execution.id
        created = repo.create(execution)
        self.db.commit()
        return created.id

    async def _update_agent_execution(
            self,
            repo: AgentExecutionRepository,
            execution_id: uuid.UUID,
            **kwargs,
    ) -> None:
        if self._uses_async_session():
            async with get_async_db_context() as db:
                await AgentExecutionRepository(db).update_completed_async(execution_id, **kwargs)
            return
        repo.update_completed(execution_id, **kwargs)



    async def _persist_final_agent_execution(
            self,
            *,
            app_id: uuid.UUID,
            conversation_id: uuid.UUID,
            agent_config_id: uuid.UUID | None,
            started_at_ts: float,
            status: str,
            steps: list,
            meta_data: dict,
            elapsed_time: Optional[float] = None,
            token_usage: Optional[dict] = None,
            error_message: Optional[str] = None,
            message_id: Optional[uuid.UUID] = None,
    ) -> uuid.UUID:
        if self._uses_async_session():
            async with get_async_db_context() as db:
                app_obj = await db.get(App, app_id)
                execution = AgentExecution(
                    app_id=app_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    agent_config_id=agent_config_id,
                    release_id=app_obj.current_release_id if app_obj else None,
                    triggered_by=None,
                    steps=steps,
                    status=status,
                    started_at=parse_timestamp_to_utc_naive(started_at_ts),
                    completed_at=utcnow_naive(),
                    elapsed_time=elapsed_time,
                    token_usage=token_usage,
                    error_message=error_message,
                    meta_data=meta_data,
                )
                db.add(execution)
                await db.commit()
                await db.refresh(execution)
                return execution.id

        app_obj = await self._db_get(App, app_id)
        execution = AgentExecution(
            app_id=app_id,
            conversation_id=conversation_id,
            message_id=message_id,
            agent_config_id=agent_config_id,
            release_id=app_obj.current_release_id if app_obj else None,
            triggered_by=None,
            steps=steps,
            status=status,
            started_at=parse_timestamp_to_utc_naive(started_at_ts),
            completed_at=utcnow_naive(),
            elapsed_time=elapsed_time,
            token_usage=token_usage,
            error_message=error_message,
            meta_data=meta_data,
        )
        self.db.add(execution)
        self.db.commit()
        return execution.id





    async def _check_annotation_match_async(
            self,
            app_id: uuid.UUID,
            message: str,
            source: str = "",
    ) -> Optional[dict]:
        if not self._uses_async_session():
            return self._check_annotation_match(app_id, message, source)

        try:
            from app.core.models.base import RedBearModelConfig
            from app.models.annotation_model import AppAnnotation, AppAnnotationHitLog, AppAnnotationSetting

            async with get_async_db_context() as db:
                result = await db.execute(
                    select(AppAnnotationSetting).where(AppAnnotationSetting.app_id == app_id).limit(1)
                )
                setting = result.scalar_one_or_none()
                if not setting or not setting.enabled or not setting.model_config_id:
                    return None

                result = await db.execute(
                    select(AppAnnotation).where(
                        AppAnnotation.app_id == app_id,
                        AppAnnotation.is_active == 1,
                    )
                )
                annotations = list(result.scalars().all())
                if not annotations:
                    return None

                tenant_id = await self._resolve_app_tenant_id_async(app_id)
                api_key_obj = await ModelApiKeyService.get_available_api_key_async(
                    db,
                    setting.model_config_id,
                    tenant_id=tenant_id,
                )
                if not api_key_obj:
                    return None
                threshold = setting.similarity_threshold

            config = RedBearModelConfig(
                model_name=api_key_obj.model_name,
                provider=api_key_obj.provider,
                api_key=api_key_obj.api_key,
                base_url=api_key_obj.api_base or None,
                timeout=60,
                max_retries=3,
            )

            query_embedding = await asyncio.to_thread(AnnotationService.generate_embedding, message, config)
            best_match = None
            best_similarity = 0.0
            for annotation in annotations:
                if not annotation.embedding:
                    continue
                similarity = AnnotationService.cosine_similarity(query_embedding, annotation.embedding)
                if similarity > best_similarity:
                    best_similarity = similarity
                    best_match = annotation

            if not best_match or best_similarity < threshold:
                return None

            async with get_async_db_context() as db:
                annotation = await db.get(AppAnnotation, best_match.id)
                if not annotation:
                    return None
                annotation.hit_count = int(annotation.hit_count or 0) + 1
                db.add(
                    AppAnnotationHitLog(
                        annotation_id=annotation.id,
                        app_id=app_id or annotation.app_id,
                        source=source,
                        query=message,
                        matched_question=annotation.question,
                        answer=annotation.answer,
                        similarity=best_similarity,
                    )
                )
                await db.commit()

            return {
                "annotation_id": str(best_match.id),
                "question": best_match.question,
                "answer": best_match.answer,
                "similarity": best_similarity,
            }
        except Exception as e:
            logger.error(f"标注匹配失败: {e}")
            return None

    def _check_annotation_match(self, app_id: uuid.UUID, message: str, source: str = "") -> Optional[dict]:
        """检查是否命中标注

        Args:
            app_id: 应用ID
            message: 用户消息
            source: 来源（用于记录命中来源）

        Returns:
            命中返回标注结果字典，未命中返回None
        """
        try:
            from app.services.annotation_service import AnnotationService
            service = AnnotationService(self.db)
            setting = service.get_setting(app_id)
            if not setting or not setting.enabled:
                return None
            if not setting.model_config_id:
                return None

            annotations = service.repo.get_all_active_by_app(app_id)
            if not annotations:
                return None

            from app.models.models_model import ModelConfig
            model_cfg = self.db.query(ModelConfig).filter(
                ModelConfig.id == setting.model_config_id
            ).first()
            if not model_cfg:
                return None

            tenant_id = self._resolve_app_tenant_id(app_id)
            api_key_obj = ModelApiKeyService.get_available_api_key(
                self.db,
                setting.model_config_id,
                tenant_id=tenant_id,
            )
            if not api_key_obj:
                return None

            from app.core.models.base import RedBearModelConfig
            model_config = RedBearModelConfig(
                model_name=api_key_obj.model_name,
                provider=api_key_obj.provider,
                api_key=api_key_obj.api_key,
                base_url=api_key_obj.api_base or None,
                timeout=60,
                max_retries=3,
            )

            result = service.find_best_match(
                query=message,
                annotations=annotations,
                threshold=setting.similarity_threshold,
                model_config=model_config,
                app_id=app_id,
                source=source,
            )
            return result
        except Exception as e:
            logger.error(f"标注匹配失败: {e}")
            return None

    async def agent_chat(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: AgentConfig,
            files: list[FileInput],
            user_id: str,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            workspace_id: Optional[str] = None,
            source: str = "",
            history: Optional[List[Dict[str, Any]]] = None,
            skip_save: bool = False,
            parent_message_id: Optional[uuid.UUID] = None,
            version: int = 1,
    ) -> Dict[str, Any]:
        """聊天（非流式）"""
        start_time = time.time()
        message_id = uuid.uuid4()
        user_message_id = uuid.uuid4()

        # 检查标注命中
        from app.models.annotation_model import HitLogSource
        annotation_match = await self._check_annotation_match_async(
            config.app_id,
            message,
            source=source or HitLogSource.EXTERNAL
        )
        if annotation_match:
            message_id = uuid.uuid4()
            user_message_id = uuid.uuid4()
            await self.conversation_service.add_message_async(
                message_id=user_message_id,
                conversation_id=conversation_id,
                role="user",
                content=message,
                meta_data={"files": []}
            )
            await self.conversation_service.add_message_async(
                message_id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=annotation_match["answer"],
                meta_data={"usage": {}}
            )
            elapsed_time = time.time() - start_time
            return {
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "user_message_id": str(user_message_id),
                "message": annotation_match["answer"],
                "reasoning_content": None,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "elapsed_time": elapsed_time,
                "suggested_questions": [],
                "citations": [],
                "audio_url": None,
                "audio_status": None
            }

        # 应用 features 配置
        features_config: dict = config.features or {}
        if hasattr(features_config, 'model_dump'):
            features_config = features_config.model_dump()
        web_search_feature = features_config.get("web_search", {})
        if not (isinstance(web_search_feature, dict) and web_search_feature.get("enabled")):
            web_search = False

        # 校验文件上传
        self.agent_service._validate_file_upload(features_config, files)

        variables = self.agent_service.prepare_variables(variables, config.variables)

        # 获取模型配置ID
        model_config_id = config.default_model_config_id
        tenant_id = await self._resolve_tenant_id_async(workspace_id)
        api_key_obj = await ModelApiKeyService.get_available_api_key_bridge_async(
            self.db,
            model_config_id,
            tenant_id=tenant_id,
        )
        # 处理系统提示词（支持变量替换）
        system_prompt = config.system_prompt
        if variables:
            system_prompt_rendered = render_prompt_message(
                system_prompt,
                PromptMessageRole.USER,
                variables
            )
            system_prompt = system_prompt_rendered.get_text_content() or system_prompt

        # 准备工具列表
        tools = []

        # 获取工具服务
        base_tools = await self.agent_service.load_tools_config(config.tools, web_search, tenant_id, user_id, workspace_id)
        tools.extend(base_tools)
        skill_tools, skill_prompts = await self.agent_service.load_skill_config(
            config.skills, message, tenant_id, user_id, workspace_id
        )
        tools.extend(skill_tools)
        if skill_prompts:
            system_prompt = f"{system_prompt}\n\n{skill_prompts}"
        kb_tools, citations_collector = await self.agent_service.load_knowledge_retrieval_config(
            config.knowledge_retrieval, user_id
        )
        tools.extend(kb_tools)
        if memory:
            memory_tools, _ = await self.agent_service.load_memory_config(
                config.memory, user_id, uuid.UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id, storage_type, user_rag_memory_id
            )
            tools.extend(memory_tools)

        # 获取模型参数
        model_parameters = config.model_parameters

        model_info = ModelInfo(
            model_name=api_key_obj.model_name,
            provider=api_key_obj.provider,
            api_key=api_key_obj.api_key,
            api_base=api_key_obj.api_base,
            capability=api_key_obj.capability,
            is_omni=api_key_obj.is_omni,
            model_type=ModelType.LLM
        )

        # 加载历史消息（包含开场白）
        used_context_engine = False
        if history is None:
            context_engine_manager = ContextEngineManager(self.db)
            prepared_input = await context_engine_manager.prepare_app_agent_input(
                features=features_config,
                conversation_id=conversation_id,
                system_prompt=system_prompt,
                current_input=message,
                current_provider=api_key_obj.provider,
                current_is_omni=api_key_obj.is_omni,
                legacy_max_history=settings.AGENT_MAX_HISTORY,
                model_config_id=config.default_model_config_id,
            )
            if prepared_input:
                system_prompt, history = prepared_input
                used_context_engine = True
            else:
                history = await self.conversation_service.get_conversation_history(
                    conversation_id=conversation_id,
                    max_history=settings.AGENT_MAX_HISTORY,
                    current_provider=api_key_obj.provider,
                    current_is_omni=api_key_obj.is_omni
                )

        # 如果是新会话且有开场白，作为第一条 assistant 消息写入数据库
        is_new_conversation = not await self._conversation_has_messages(conversation_id)
        if is_new_conversation:
            opening, suggested_questions = self.agent_service._get_opening_statement(features_config, True, variables)
            if opening:
                await self.conversation_service.add_message_async(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=opening,
                    meta_data={"suggested_questions": suggested_questions}
                )
                # 重新加载历史（包含刚写入的开场白）
                history = await self.conversation_service.get_conversation_history(
                    conversation_id=conversation_id,
                    max_history=settings.AGENT_MAX_HISTORY,
                    current_provider=api_key_obj.provider,
                    current_is_omni=api_key_obj.is_omni
                )

        # 处理多模态文件
        processed_files = None
        if files:
            multimodal_service = MultimodalService(self.db, model_info)
            fu_config = features_config.get("file_upload", {})
            if hasattr(fu_config, "model_dump"):
                fu_config = fu_config.model_dump()
            doc_img_recognition = isinstance(fu_config, dict) and fu_config.get("document_image_recognition", False)
            processed_files = await multimodal_service.process_files(
                files, document_image_recognition=doc_img_recognition,
                workspace_id=workspace_id
            )
            logger.info(f"处理了 {len(processed_files)} 个文件")
            if doc_img_recognition and ModelCapability.VISION in (api_key_obj.capability or []) and any(
                f.type == FileType.DOCUMENT for f in files
            ):
                system_prompt += (
                    "\n\n文档文字中包含图片位置标记如 [图片 第2页 第1张]: <img src=\"url\"...>，"
                    "请在回答中用 Markdown 格式 ![图片描述](url) 展示对应图片。"
                    "重要：图片 URL 中包含 UUID（如 /storage/permanent/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx），"
                    "必须将 src 属性的值原封不动复制到 Markdown 的括号中，不得增删任何字符。"
                )

        # 弱模型：用 ReAct prompt 驱动多轮工具调用，将轨迹注入 system_prompt
        capability = api_key_obj.capability or []
        orchestrator_node_executions = []
        _api_key_config = {
            "model_name": api_key_obj.model_name,
            "api_key": api_key_obj.api_key,
            "provider": api_key_obj.provider,
            "api_base": api_key_obj.api_base,
            "is_omni": api_key_obj.is_omni,
            "capability": capability,
        }
        if ModelCapability.FUNCTION_CALL not in capability and tools:
            system_prompt, orchestrator_node_executions = await ToolOrchestrator.create_and_run(
                tools=tools,
                system_prompt=system_prompt,
                message=message,
                history=history,
                api_key_config=_api_key_config,
                model_config=model_info,
                effective_params=model_parameters,
                processed_files=processed_files,
            )
            tools = []

        # 创建 LangChain Agent
        agent = LangChainAgent(
            model_name=api_key_obj.model_name,
            api_key=api_key_obj.api_key,
            provider=api_key_obj.provider,
            api_base=api_key_obj.api_base,
            is_omni=api_key_obj.is_omni,
            temperature=model_parameters.get("temperature", 0.7),
            max_tokens=model_parameters.get("max_tokens", 2000),
            system_prompt=system_prompt,
            tools=tools,
            deep_thinking=model_parameters.get("deep_thinking", False),
            thinking_budget_tokens=model_parameters.get("thinking_budget_tokens"),
            json_output=model_parameters.get("json_output", False),
            capability=capability,
        )

        # 为需要运行时上下文的工具注入上下文
        for t in tools:
            if hasattr(t, 'tool_instance') and hasattr(t.tool_instance, 'set_runtime_context'):
                t.tool_instance.set_runtime_context(
                    user_id=user_id or "anonymous",
                    conversation_id=str(conversation_id) if conversation_id else None,
                    uploaded_files=processed_files or []
                )

        # 创建 Agent 执行记录（pending 状态，对齐工作流行为）
        from app.models.app_model import App
        agent_exec_repo = AgentExecutionRepository(self.db)
        app_obj = await self._db_get(App, config.app_id)
        agent_execution = AgentExecution(
            app_id=config.app_id,
            conversation_id=conversation_id,
            message_id=None,
            agent_config_id=config.id,
            release_id=app_obj.current_release_id if app_obj else None,
            triggered_by=None,
            steps=[],
            status="running",
            started_at=parse_timestamp_to_utc_naive(start_time),
            meta_data={
                "model": api_key_obj.model_name,
                "provider": api_key_obj.provider,
            },
        )
        agent_execution_id = await self._create_agent_execution(agent_exec_repo, agent_execution)

        try:
            # 调用 Agent（支持多模态）
            result = await agent.chat(
                message=message,
                history=history,
                context=None,
                files=processed_files
            )
        except Exception as e:
            # Agent 执行失败，更新记录为 failed
            elapsed_time = time.time() - start_time
            await self._update_agent_execution(
                agent_exec_repo,
                execution_id=agent_execution_id,
                steps=[],
                status="failed",
                elapsed_time=elapsed_time,
                error_message=str(e)[:2000],
            )
            raise

        await self._record_api_key_usage(api_key_obj.id)

        elapsed_time = time.time() - start_time

        # suggested_questions
        suggested_questions = []
        sq_config = features_config.get("suggested_questions_after_answer", {})
        if isinstance(sq_config, dict) and sq_config.get("enabled"):
            suggested_questions = await self.agent_service._generate_suggested_questions(
                features_config, result["content"],
                _api_key_config, {}
            )

        audio_url = await self.agent_service._generate_tts(
            features_config, result["content"],
            {"model_name": api_key_obj.model_name, "api_key": api_key_obj.api_key,
             "api_base": api_key_obj.api_base, "provider": api_key_obj.provider},
            tenant_id=tenant_id, workspace_id=workspace_id
        )

        # 过滤 citations（只调用一次）
        filtered_citations = self.agent_service._filter_citations(features_config, citations_collector)

        # 构建用户消息内容（含多模态文件）
        human_meta = {
            "files": [],
            "history_files": {}
        }
        assistant_meta = {
            "model": api_key_obj.model_name,
            "usage": result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
            "audio_url": None,
            "citations": filtered_citations,
            "suggested_questions": suggested_questions,
            "reasoning_content": result.get("reasoning_content")
        }
        if files:
            local_ids = [f.upload_file_id for f in files
                         if f.transfer_method.value == "local_file" and f.upload_file_id
                         and (not f.name or not f.size)]
            meta_map = {}
            if local_ids:
                meta_map = await self._fetch_completed_file_metadata(local_ids)
            for f in files:
                name, size = f.name, f.size
                if f.transfer_method.value == "local_file" and f.upload_file_id and (not name or not size):
                    meta = meta_map.get(str(f.upload_file_id))
                    if meta:
                        name = name or meta.file_name
                        size = size or meta.file_size
                human_meta["files"].append({
                    "type": f.type,
                    "url": f.url,
                    "name": name,
                    "size": size,
                    "file_type": f.file_type,
                })

        if processed_files:
            human_meta["history_files"] = {
                "content": processed_files,
                "provider": api_key_obj.provider,
                "is_omni": api_key_obj.is_omni
            }

        if audio_url:
            assistant_meta["audio_url"] = audio_url
        # 长期记忆写入由 conversation_service.add_message → MemoryWriteDispatcher 统一接管，
        # 这里不再触发老的 write_long_term 路径。
        if not skip_save:
            await self.conversation_service.add_message_async(
                message_id=user_message_id,
                conversation_id=conversation_id,
                role="user",
                content=message,
                meta_data=human_meta,
                should_memorize=memory,
            )
            await self.conversation_service.add_message_async(
                message_id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=result["content"],
                meta_data=assistant_meta,
                should_memorize=memory,
            )
            if used_context_engine:
                _ctx_kwargs = dict(
                    features=features_config,
                    conversation_id=conversation_id,
                    current_provider=api_key_obj.provider,
                    current_is_omni=api_key_obj.is_omni,
                    legacy_max_history=settings.AGENT_MAX_HISTORY,
                    model_config_id=config.default_model_config_id,
                )
                async def _run_after_turn(kwargs=_ctx_kwargs):
                    async with get_async_db_context() as db2:
                        await ContextEngineManager(db2).after_app_turn(**kwargs)
                asyncio.create_task(_run_after_turn())
        else:
            new_msg = Message(
                id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=result["content"],
                version=version,
                is_current=True,
                parent_message_id=parent_message_id,
                meta_data=assistant_meta,
            )
            self.db.add(new_msg)
            conv = await self._db_get(Conversation, conversation_id)
            if conv:
                conv.message_count += 1

            if self._uses_async_session():
                await self.db.commit()
            else:
                self.db.commit()
        # 更新 Agent 执行记录为 completed
        node_executions = orchestrator_node_executions + result.get("node_executions", [])
        await self._update_agent_execution(
            agent_exec_repo,
            execution_id=agent_execution_id,
            steps=node_executions,
            status="completed",
            elapsed_time=elapsed_time,
            token_usage=result.get("usage"),
            message_id=message_id,
        )

        return {
            "conversation_id": conversation_id,
            "message_id": str(message_id),
            "user_message_id": str(user_message_id),
            "message": result["content"],
            "reasoning_content": result.get("reasoning_content"),
            "usage": result.get("usage", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }),
            "elapsed_time": elapsed_time,
            "suggested_questions": suggested_questions,
            "citations": filtered_citations,
            "audio_url": audio_url,
            "audio_status": "pending" if audio_url else None
        }

    async def agent_chat_stream(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: AgentConfig,
            files: list[FileInput],
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            workspace_id: Optional[str] = None,
            source: str = "",
            history: Optional[List[Dict[str, Any]]] = None,
            skip_save: bool = False,
            parent_message_id: Optional[uuid.UUID] = None,
            version: int = 1,
    ) -> AsyncGenerator[str, None]:
        """聊天（流式）"""

        try:
            start_time = time.time()
            message_id = uuid.uuid4()
            user_message_id = uuid.uuid4()

            # 检查标注命中
            from app.models.annotation_model import HitLogSource
            annotation_match = await self._check_annotation_match_async(
                config.app_id,
                message,
                source=source or HitLogSource.EXTERNAL
            )
            if annotation_match:
                await self.conversation_service.add_message_async(
                    message_id=user_message_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=message,
                    meta_data={"files": []}
                )
                await self.conversation_service.add_message_async(
                    message_id=message_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=annotation_match["answer"],
                    meta_data={"usage": {}}
                )
                yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id), 'user_message_id': str(user_message_id)}, ensure_ascii=False)}\n\n"
                yield f"event: message\ndata: {json.dumps({'content': annotation_match['answer'], 'conversation_id': str(conversation_id)}, ensure_ascii=False)}\n\n"
                yield f"event: end\ndata: {json.dumps({'elapsed_time': time.time() - start_time, 'message_length': len(annotation_match['answer']), 'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}, ensure_ascii=False)}\n\n"
                return

            # 应用 features 配置
            features_config: dict = config.features or {}
            if hasattr(features_config, 'model_dump'):
                features_config = features_config.model_dump()
            web_search_feature = features_config.get("web_search", {})
            if not (isinstance(web_search_feature, dict) and web_search_feature.get("enabled")):
                web_search = False

            # 校验文件上传
            self.agent_service._validate_file_upload(features_config, files)

            yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id), 'user_message_id': str(user_message_id)}, ensure_ascii=False)}\n\n"

            variables = self.agent_service.prepare_variables(variables, config.variables)
            # 获取模型配置ID
            model_config_id = config.default_model_config_id
            tenant_id = await self._resolve_tenant_id_async(workspace_id)
            api_key_obj = await ModelApiKeyService.get_available_api_key_bridge_async(
                self.db,
                model_config_id,
                tenant_id=tenant_id,
            )
            # 处理系统提示词（支持变量替换）
            system_prompt = config.system_prompt
            if variables:
                system_prompt_rendered = render_prompt_message(
                    system_prompt,
                    PromptMessageRole.USER,
                    variables
                )
                system_prompt = system_prompt_rendered.get_text_content() or system_prompt

            # 准备工具列表
            tools = []

            # 获取工具服务
            base_tools = await self.agent_service.load_tools_config(config.tools, web_search, tenant_id, user_id, workspace_id)
            tools.extend(base_tools)

            skill_tools, skill_prompts = await self.agent_service.load_skill_config(config.skills, message, tenant_id, user_id, workspace_id)
            tools.extend(skill_tools)
            if skill_prompts:
                system_prompt = f"{system_prompt}\n\n{skill_prompts}"
            kb_tools, citations_collector = await self.agent_service.load_knowledge_retrieval_config(
                config.knowledge_retrieval, user_id)
            tools.extend(kb_tools)
            # 添加长期记忆工具
            if memory:
                memory_tools, _ = await self.agent_service.load_memory_config(
                    config.memory, user_id, uuid.UUID(workspace_id) if isinstance(workspace_id, str) else workspace_id, storage_type, user_rag_memory_id
                )
                tools.extend(memory_tools)

            # 获取模型参数
            model_parameters = config.model_parameters

            model_info = ModelInfo(
                model_name=api_key_obj.model_name,
                provider=api_key_obj.provider,
                api_key=api_key_obj.api_key,
                api_base=api_key_obj.api_base,
                capability=api_key_obj.capability,
                is_omni=api_key_obj.is_omni,
                model_type=ModelType.LLM
            )

            # 加载历史消息（包含开场白）
            used_context_engine = False
            if history is None:
                context_engine_manager = ContextEngineManager(self.db)
                prepared_input = await context_engine_manager.prepare_app_agent_input(
                    features=features_config,
                    conversation_id=conversation_id,
                    system_prompt=system_prompt,
                    current_input=message,
                    current_provider=api_key_obj.provider,
                    current_is_omni=api_key_obj.is_omni,
                    legacy_max_history=settings.AGENT_MAX_HISTORY,
                    model_config_id=config.default_model_config_id,
                )
                if prepared_input:
                    system_prompt, history = prepared_input
                    used_context_engine = True
                else:
                    history = await self.conversation_service.get_conversation_history(
                        conversation_id=conversation_id,
                        max_history=settings.AGENT_MAX_HISTORY,
                        current_provider=api_key_obj.provider,
                        current_is_omni=api_key_obj.is_omni
                    )

            # 新会话开场白先拼到内存 history，避免首包前写库+回查。
            is_new_conversation = not await self._conversation_has_messages(conversation_id)
            opening_statement = None
            opening_suggested_questions: List[str] = []
            if is_new_conversation:
                opening_statement, opening_suggested_questions = self.agent_service._get_opening_statement(
                    features_config, True, variables
                )
                if opening_statement:
                    history = self._append_history_message(
                        history,
                        role="assistant",
                        content=opening_statement,
                    )

            # 处理多模态文件
            processed_files = None
            if files:
                multimodal_service = MultimodalService(self.db, model_info)
                fu_config = features_config.get("file_upload", {})
                if hasattr(fu_config, "model_dump"):
                    fu_config = fu_config.model_dump()
                doc_img_recognition = isinstance(fu_config, dict) and fu_config.get("document_image_recognition", False)
                processed_files = await multimodal_service.process_files(
                    files, document_image_recognition=doc_img_recognition,
                    workspace_id=workspace_id
                )
                logger.info(f"处理了 {len(processed_files)} 个文件")
                if doc_img_recognition and ModelCapability.VISION in (api_key_obj.capability or []) and any(
                    f.type == FileType.DOCUMENT for f in files
                ):
                    system_prompt += (
                        "\n\n文档文字中包含图片位置标记如 [图片 第2页 第1张]: <img src=\"url\"...>，"
                        "请在回答中用 Markdown 格式 ![图片描述](url) 展示对应图片。"
                        "重要：图片 URL 中包含 UUID（如 /storage/permanent/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx），"
                        "必须将 src 属性的值原封不动复制到 Markdown 的括号中，不得增删任何字符。"
                    )

            # 弱模型：用 ReAct prompt 驱动多轮工具调用，将轨迹注入 system_prompt
            capability = api_key_obj.capability or []
            orchestrator_node_executions = []
            _api_key_config = {
                "model_name": api_key_obj.model_name,
                "api_key": api_key_obj.api_key,
                "provider": api_key_obj.provider,
                "api_base": api_key_obj.api_base,
                "is_omni": api_key_obj.is_omni,
                "capability": capability,
            }
            if ModelCapability.FUNCTION_CALL not in capability and tools:
                system_prompt, orchestrator_node_executions = await ToolOrchestrator.create_and_run(
                    tools=tools,
                    system_prompt=system_prompt,
                    message=message,
                    history=history,
                    api_key_config=_api_key_config,
                    model_config=model_info,
                    effective_params=model_parameters,
                    processed_files=processed_files,
                )
                # 把已完成的工具调用步骤作为事件补发给前端
                for step in orchestrator_node_executions:
                    event_type = "tool_error" if step.get("status") == "failed" else "tool_end"
                    yield f"event: tool_start\ndata: {json.dumps({'step_id': step.get('step_id'), 'name': step.get('node_name'), 'input': step.get('input'), 'meta': step.get('meta')}, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                    yield f"event: {event_type}\ndata: {json.dumps({'step_id': step.get('step_id'), 'name': step.get('node_name'), 'output': step.get('output'), 'error': step.get('error'), 'meta': step.get('meta')}, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                tools = []

            # 创建 LangChain Agent
            agent = LangChainAgent(
                model_name=api_key_obj.model_name,
                api_key=api_key_obj.api_key,
                provider=api_key_obj.provider,
                api_base=api_key_obj.api_base,
                is_omni=api_key_obj.is_omni,
                temperature=model_parameters.get("temperature", 0.7),
                max_tokens=model_parameters.get("max_tokens", 2000),
                system_prompt=system_prompt,
                tools=tools,
                streaming=True,
                deep_thinking=model_parameters.get("deep_thinking", False),
                thinking_budget_tokens=model_parameters.get("thinking_budget_tokens"),
                json_output=model_parameters.get("json_output", False),
                capability=capability,
            )

            # 为需要运行时上下文的工具注入上下文
            for t in tools:
                if hasattr(t, 'tool_instance') and hasattr(t.tool_instance, 'set_runtime_context'):
                    t.tool_instance.set_runtime_context(
                        user_id=user_id or "anonymous",
                        conversation_id=str(conversation_id) if conversation_id else None,
                        uploaded_files=processed_files or []
                    )

            # close() 前预读 ORM 属性，防止 close 后触发 DetachedInstanceError
            _api_key_id = api_key_obj.id
            _api_key_model_name = api_key_obj.model_name
            _api_key_provider = api_key_obj.provider
            _api_key_is_omni = api_key_obj.is_omni
            # LLM 推理期间不再依赖共享 session，提前归还底层连接给连接池。
            await self._release_db_connection()

            # 流式调用 Agent（支持多模态），同时并行启动 TTS
            full_content = ""
            full_reasoning = ""
            total_tokens = 0
            node_executions = []

            text_queue: asyncio.Queue = asyncio.Queue()
            api_key_config = {
                "model_name": api_key_obj.model_name,
                "api_key": api_key_obj.api_key,
                "api_base": api_key_obj.api_base,
                "provider": api_key_obj.provider,
            }
            stream_audio_url, tts_task = await self.agent_service._generate_tts_streaming(
                features_config, api_key_config,
                text_queue=text_queue,
                tenant_id=tenant_id, workspace_id=workspace_id
            )

            async for chunk in agent.chat_stream(
                    message=message,
                    history=history,
                    context=None,
                    files=processed_files
            ):
                if isinstance(chunk, int):
                    total_tokens = chunk
                elif isinstance(chunk, dict) and chunk.get("type") == "reasoning":
                    full_reasoning += chunk['content']
                    yield f"event: reasoning\ndata: {json.dumps({'content': chunk['content']}, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "node_executions":
                    node_executions = chunk.get("data", [])
                elif isinstance(chunk, dict) and chunk.get("type") == "tool_start":
                    yield f"event: tool_start\ndata: {json.dumps({'step_id': chunk.get('step_id'), 'name': chunk['name'], 'input': chunk.get('input'), 'meta': chunk.get('meta')}, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "tool_end":
                    yield f"event: tool_end\ndata: {json.dumps({'step_id': chunk.get('step_id'), 'name': chunk['name'], 'output': chunk.get('output'), 'meta': chunk.get('meta')}, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "tool_error":
                    yield f"event: tool_error\ndata: {json.dumps({'step_id': chunk.get('step_id'), 'name': chunk['name'], 'error': chunk.get('error')}, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, dict) and chunk.get("type") == "agent_log":
                    yield f"event: agent_log\ndata: {json.dumps(chunk, cls=CustomJsonEncoder, ensure_ascii=False)}\n\n"
                elif isinstance(chunk, str):
                    full_content += chunk
                    yield f"event: message\ndata: {json.dumps({'content': chunk}, ensure_ascii=False)}\n\n"
                    if tts_task is not None:
                        await text_queue.put(chunk)
                elif isinstance(chunk, dict):
                    event_type = str(chunk.get("type") or "unknown")
                    yield f"event: {event_type}\ndata: {json.dumps(chunk, ensure_ascii=False)}\n\n"

            if tts_task is not None:
                await text_queue.put(None)

            elapsed_time = time.time() - start_time
            await self._record_api_key_usage(_api_key_id)

            # 发送结束事件（包含 suggested_questions、tts、audio_status、citations）
            end_data: dict = {"elapsed_time": elapsed_time, "message_length": len(full_content), "error": None}
            sq_config = features_config.get("suggested_questions_after_answer", {})
            suggested_questions = []
            if isinstance(sq_config, dict) and sq_config.get("enabled"):
                suggested_questions = await self.agent_service._generate_suggested_questions(
                    features_config, full_content,
                    _api_key_config, {}
                )
                end_data["suggested_questions"] = suggested_questions
            end_data["audio_url"] = stream_audio_url
            # 检查TTS是否已完成（非阻塞，不取消任务）
            audio_status = "pending"
            if tts_task is not None and tts_task.done():
                # 任务已完成，检查是否有异常
                try:
                    tts_task.result()
                    audio_status = "completed"
                except Exception as e:
                    logger.warning(f"TTS任务异常: {e}")
                    audio_status = "failed"
            end_data["audio_status"] = audio_status if stream_audio_url else None
            # 过滤 citations（只调用一次）
            filtered_citations = self.agent_service._filter_citations(features_config, citations_collector)
            end_data["citations"] = filtered_citations

            human_meta = {
                "files": [],
                "history_files": {}
            }
            assistant_meta = {
                "model": _api_key_model_name,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                "audio_url": stream_audio_url,
                "citations": filtered_citations,
                "suggested_questions": suggested_questions,
                "reasoning_content": full_reasoning or None
            }

            # 长期记忆写入由 conversation_service.add_message → MemoryWriteDispatcher 统一接管，
            # 这里不再触发老的 write_long_term 路径。
            if files:
                local_ids = [f.upload_file_id for f in files
                             if f.transfer_method.value == "local_file" and f.upload_file_id
                             and (not f.name or not f.size)]
                meta_map = await self._fetch_completed_file_metadata(local_ids) if local_ids else {}
                for f in files:
                    name, size = f.name, f.size
                    if f.transfer_method.value == "local_file" and f.upload_file_id and (not name or not size):
                        meta = meta_map.get(str(f.upload_file_id))
                        if meta:
                            name = name or meta.file_name
                            size = size or meta.file_size
                    human_meta["files"].append({
                        "type": f.type,
                        "url": f.url,
                        "name": name,
                        "size": size,
                        "file_type": f.file_type,
                    })
            if processed_files:
                human_meta["history_files"] = {
                    "content": processed_files,
                    "provider": _api_key_provider,
                    "is_omni": _api_key_is_omni
                }

            if not skip_save:
                async with get_async_db_context() as db:
                    svc = ConversationService(db)
                    if opening_statement:
                        await svc.add_message_async(
                            conversation_id=conversation_id,
                            role="assistant",
                            content=opening_statement,
                            meta_data={"suggested_questions": opening_suggested_questions},
                        )
                    await svc.add_message_async(
                        message_id=user_message_id,
                        conversation_id=conversation_id,
                        role="user",
                        content=message,
                        meta_data=human_meta,
                        should_memorize=memory,
                    )
                    await svc.add_message_async(
                        message_id=message_id,
                        conversation_id=conversation_id,
                        role="assistant",
                        content=full_content,
                        meta_data=assistant_meta,
                        should_memorize=memory,
                    )
                if used_context_engine:
                    _ctx_kwargs = dict(
                        features=features_config,
                        conversation_id=conversation_id,
                        current_provider=_api_key_provider,
                        current_is_omni=_api_key_is_omni,
                        legacy_max_history=settings.AGENT_MAX_HISTORY,
                        model_config_id=config.default_model_config_id,
                    )
                    async def _run_after_turn(kwargs=_ctx_kwargs):
                        async with get_async_db_context() as db2:
                            await ContextEngineManager(db2).after_app_turn(**kwargs)
                    asyncio.create_task(_run_after_turn())
            else:
                async with get_async_db_context() as db:
                    new_msg = Message(
                        id=message_id,
                        conversation_id=conversation_id,
                        role="assistant",
                        content=full_content,
                        version=version,
                        is_current=True,
                        parent_message_id=parent_message_id,
                        meta_data=assistant_meta,
                    )
                    db.add(new_msg)
                    conv = await db.get(Conversation, conversation_id)
                    if conv:
                        conv.message_count += 1
                    await db.commit()
            # 首包后再一次性落 Agent execution，避免首包前 create + 尾部 update 双写。
            all_node_executions = orchestrator_node_executions + node_executions
            await self._persist_final_agent_execution(
                app_id=config.app_id,
                conversation_id=conversation_id,
                agent_config_id=config.id,
                started_at_ts=start_time,
                status="completed",
                steps=all_node_executions,
                meta_data={
                    "model": _api_key_model_name,
                    "provider": _api_key_provider,
                },
                elapsed_time=elapsed_time,
                token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                message_id=message_id,
            )

            yield f"event: end\ndata: {json.dumps(end_data, ensure_ascii=False)}\n\n"

            logger.info(
                "流式聊天完成",
                extra={
                    "conversation_id": str(conversation_id),
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
            # 保存失败的消息，使前端可以展示失败状态
            try:
                _human_meta = human_meta if 'human_meta' in locals() else {"files": [], "history_files": {}}
                async with get_async_db_context() as db:
                    svc = ConversationService(db)
                    await svc.add_message_async(
                        message_id=user_message_id,
                        conversation_id=conversation_id,
                        role="user",
                        content=message,
                        meta_data=_human_meta,
                    )
                    await svc.add_message_async(
                        message_id=message_id,
                        conversation_id=conversation_id,
                        role="assistant",
                        content="",
                        meta_data={"error": str(e)[:2000]},
                        status="failed",
                    )
            except Exception:
                pass
            # 失败场景也改成尾部一次写，避免依赖首包前 execution。
            try:
                elapsed_time = time.time() - start_time
                await self._persist_final_agent_execution(
                    app_id=config.app_id,
                    conversation_id=conversation_id,
                    agent_config_id=config.id,
                    started_at_ts=start_time,
                    status="failed",
                    steps=node_executions if 'node_executions' in dir() else [],
                    meta_data={
                        "model": _api_key_model_name if '_api_key_model_name' in locals() else None,
                        "provider": _api_key_provider if '_api_key_provider' in locals() else None,
                    },
                    elapsed_time=elapsed_time,
                    error_message=str(e)[:2000],
                )
            except Exception:
                pass  # 保存失败不影响错误事件发送
            # 发送错误事件
            yield f"event: end\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    async def multi_agent_chat(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: MultiAgentConfig,
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """多 Agent 聊天（非流式）"""

        start_time = time.time()
        user_message_id = uuid.uuid4()
        actual_config_id = None
        config_id = actual_config_id

        if variables is None:
            variables = {}

        # 2. 创建编排器
        orchestrator = await MultiAgentOrchestrator.create(self.db, config)

        # 3. 执行任务
        result = await orchestrator.execute(
            message=message,
            conversation_id=conversation_id,
            user_id=user_id,
            variables=variables,
            use_llm_routing=True,  # 默认启用 LLM 路由
            web_search=web_search,  # 网络搜索参数
            memory=memory  # 记忆功能参数
        )

        elapsed_time = time.time() - start_time

        # 保存消息
        await self.conversation_service.add_message_async(
            message_id=user_message_id,
            conversation_id=conversation_id,
            role="user",
            content=message
        )

        ai_message = await self.conversation_service.add_message_async(
            conversation_id=conversation_id,
            role="assistant",
            content=result.get("message", ""),
            meta_data={
                "mode": result.get("mode"),
                "elapsed_time": result.get("elapsed_time"),
                "usage": result.get("usage", {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0
                })
            }
        )

        return {
            "conversation_id": conversation_id,
            "message": result.get("message", ""),
            "message_id": str(ai_message.id),
            "user_message_id": str(user_message_id),
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            },
            "elapsed_time": elapsed_time
        }

    async def multi_agent_chat_stream(
            self,
            message: str,
            conversation_id: uuid.UUID,
            config: MultiAgentConfig,
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """多 Agent 聊天（流式）"""

        start_time = time.time()

        if variables is None:
            variables = {}

        try:
            message_id = uuid.uuid4()
            user_message_id = uuid.uuid4()
            # 发送开始事件
            yield f"event: start\ndata: {json.dumps({'conversation_id': str(conversation_id), 'message_id': str(message_id), 'user_message_id': str(user_message_id)}, ensure_ascii=False)}\n\n"

            full_content = ""
            total_tokens = 0

            # 2. 创建编排器
            orchestrator = await MultiAgentOrchestrator.create(self.db, config)

            # 3. 流式执行任务
            async for event in orchestrator.execute_stream(
                    message=message,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    variables=variables,
                    use_llm_routing=True,
                    web_search=web_search,  # 网络搜索参数
                    memory=memory,  # 记忆功能参数
                    storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id
            ):
                # 拦截 sub_usage 事件，累加 token
                if "event: sub_usage" in event:
                    if "data:" in event:
                        try:
                            data_line = event.split("data: ", 1)[1].strip()
                            data = json.loads(data_line)
                            total_tokens += data.get("total_tokens", 0)
                        except:
                            pass
                else:
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
            await self.conversation_service.add_message_async(
                message_id=user_message_id,
                conversation_id=conversation_id,
                role="user",
                content=message
            )

            await self.conversation_service.add_message_async(
                message_id=message_id,
                conversation_id=conversation_id,
                role="assistant",
                content=full_content,
                meta_data={
                    "elapsed_time": elapsed_time,
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": total_tokens
                    }
                }
            )

            logger.info(
                "多 Agent 流式聊天完成",
                extra={
                    "conversation_id": str(conversation_id),
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

    async def workflow_chat(
            self,
            message: Optional[str],
            conversation_id: Optional[uuid.UUID],
            config: WorkflowConfig,
            app_id: uuid.UUID,
            release_id: uuid.UUID,
            workspace_id: uuid.UUID,
            files: Optional[List[FileInput]] = None,
            user_id: Optional[str] = None,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            source: str = "",
    ) -> Dict[str, Any]:
        """聊天（非流式）"""
        payload = DraftRunRequest(
            message=message,
            variables=variables,
            conversation_id=str(conversation_id) if conversation_id else None,
            stream=True,
            user_id=user_id,
            files=files
        )
        return await self.workflow_service.run(
            app_id=app_id,
            payload=payload,
            config=config,
            workspace_id=workspace_id,
            release_id=release_id,
            source=source,
            prepared_memory_storage_type=storage_type,
            prepared_user_rag_memory_id=user_rag_memory_id,
        )

    async def workflow_chat_stream(
            self,
            message: Optional[str],
            conversation_id: Optional[uuid.UUID],
            config: WorkflowConfig,
            app_id: uuid.UUID,
            release_id: uuid.UUID,
            workspace_id: uuid.UUID,
            user_id: str = None,
            variables: Optional[Dict[str, Any]] = None,
            files: Optional[List[FileInput]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
            public=False,
            source: str = "",

    ) -> AsyncGenerator[dict, None]:
        """聊天（流式）"""
        payload = DraftRunRequest(
            message=message,
            variables=variables,
            conversation_id=str(conversation_id) if conversation_id else None,
            stream=True,
            user_id=user_id,
            files=files
        )
        async for event in self.workflow_service.run_stream(
                app_id=app_id,
                payload=payload,
                config=config,
                workspace_id=workspace_id,
                release_id=release_id,
                public=public,
                source=source,
                prepared_memory_storage_type=storage_type,
                prepared_user_rag_memory_id=user_rag_memory_id,
        ):
            yield event

    async def workflow_resume_intervention_stream(
            self,
            execution_id: str,
            app_id: uuid.UUID,
            node_id: str,
            action_id: str,
            form_data: Optional[Dict[str, Any]] = None,
            public: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """Resume a waiting-human workflow via SSE stream (page refresh case)."""
        async for event in self.workflow_service.resume_intervention_stream(
            execution_id=execution_id,
            app_id=app_id,
            node_id=node_id,
            action_id=action_id,
            form_data=form_data,
            public=public,
        ):
            yield event

    # ==================== 重新生成功能 ====================

    def _locate_or_restore_parent_user_message(self, original_msg: "Message") -> "Message":
        """定位原 assistant 消息对应的父 user 消息。

        查找顺序：
        1. 优先用已回填的 parent_message_id（若其指向的消息非 user，视为脏数据忽略）；
        2. 否则回溯同会话 created_at 早于或等于原消息的最近一条 user 消息
           （不按 is_deleted 过滤，因为该消息即使已被删除仍是本轮实际提问；
           用 <= 而非 < 是为了兼容 user/assistant 同毫秒入库的边界场景）；
        3. 仍找不到时再放宽到本会话内最近一条 user 消息（兜底，覆盖
           created_at 顺序异常的脏数据）。

        若最终定位到的父消息已被逻辑删除（用户重新生成前误删了原提问），自动恢复
        （is_deleted 置回 False）后继续，而非直接抛"无法找到原始用户消息"——
        避免误删提问导致该轮回复彻底无法重新生成。
        """
        from sqlalchemy import select
        parent_msg = None
        if original_msg.parent_message_id:
            candidate = self.db.get(Message, original_msg.parent_message_id)
            if candidate and candidate.role == "user":
                parent_msg = candidate
        if not parent_msg:
            parent_msg = self.db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == original_msg.conversation_id,
                    Message.role == "user",
                    Message.created_at <= original_msg.created_at,
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            ).first()
        if not parent_msg:
            # 兜底：同会话内最近一条 user 消息（不论时间顺序），覆盖 created_at 异常的脏数据
            parent_msg = self.db.scalars(
                select(Message)
                .where(
                    Message.conversation_id == original_msg.conversation_id,
                    Message.role == "user",
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            ).first()
        if not parent_msg:
            raise BusinessException("无法找到原始用户消息", BizCode.NOT_FOUND)
        # 回填 parent_message_id 便于后续版本化关联
        if original_msg.parent_message_id != parent_msg.id:
            original_msg.parent_message_id = parent_msg.id
            self.db.commit()
        if parent_msg.is_deleted:
            parent_msg.is_deleted = False
            self.db.commit()
            logger.info(
                "重新生成时自动恢复被删除的父 user 消息",
                extra={
                    "parent_message_id": str(parent_msg.id),
                    "assistant_message_id": str(original_msg.id),
                    "conversation_id": str(original_msg.conversation_id),
                },
            )
        return parent_msg

    async def regenerate(
            self,
            message_id: uuid.UUID,
            config,
            workspace_id: uuid.UUID,
            user_id: str,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """重新生成回复（非流式，多版本支持）

        Args:
            message_id: 原 AI 回复的消息ID
            config: AgentConfig
            workspace_id: 工作空间ID
            user_id: 用户ID
            variables: 变量参数
            web_search: 是否启用网络搜索
            memory: 是否启用长期记忆
            storage_type: 存储类型
            user_rag_memory_id: RAG 记忆ID

        Returns:
            Dict: 包含新消息ID、内容、版本号等
        """
        # 1. 获取原消息
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)

        # 2. 获取父用户消息（找不到已回填的 parent_message_id 时按 created_at 回溯；
        # 若定位到的父消息已被逻辑删除则自动恢复，见 _locate_or_restore_parent_user_message）
        parent_msg = self._locate_or_restore_parent_user_message(original_msg)
        parent_msg_id = parent_msg.id

        user_message_content = parent_msg.content if parent_msg else ""

        # 3. 查询同一 parent_message_id 下的所有版本，获取最大版本号
        from sqlalchemy import select, func
        max_version_result = self.db.scalars(
            select(func.max(Message.version))
            .where(
                Message.conversation_id == original_msg.conversation_id,
                Message.parent_message_id == parent_msg_id,
                Message.role == "assistant",
                Message.is_deleted.is_not(True),
            )
        ).first()
        max_version = max_version_result or 0
        new_version = max_version + 1

        # 4. 将同一 parent_message_id 下所有版本标记为非当前
        self.db.query(Message).filter(
            Message.conversation_id == original_msg.conversation_id,
            Message.parent_message_id == parent_msg_id,
            Message.role == "assistant",
        ).update({"is_current": False})
        self.db.commit()

        # 5. 提取父消息中的文件信息
        files = None
        if parent_msg and parent_msg.meta_data:
            meta_files = parent_msg.meta_data.get("files", [])
            if meta_files:
                files = []
                for f in meta_files:
                    try:
                        file_input = FileInput(
                            type=FileType(f.get("type", "document")),
                            transfer_method=TransferMethod.REMOTE_URL if f.get("url") else TransferMethod.LOCAL_FILE,
                            url=f.get("url"),
                            file_type=f.get("file_type"),
                            name=f.get("name"),
                            size=f.get("size"),
                        )
                        files.append(file_input)
                    except Exception as e:
                        logger.warning(f"转换文件信息失败: {e}")

        # 6. 加载上下文（到父消息为止）
        conversation_id = original_msg.conversation_id
        filtered_history = await self._load_history_before_message(
            conversation_id=conversation_id,
            before_time=parent_msg.created_at,
            max_history=settings.AGENT_MAX_HISTORY
        )

        # 7. 调用 agent_chat（传入版本参数，由 agent_chat 保存）
        result = await self.agent_chat(
            message=user_message_content,
            conversation_id=conversation_id,
            config=config,
            files=files,
            user_id=user_id,
            variables=variables,
            web_search=web_search,
            memory=memory,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            workspace_id=str(workspace_id),
            history=filtered_history,
            skip_save=True,
            parent_message_id=parent_msg_id,
            version=new_version,
        )

        logger.info(
            "重新生成回复成功",
            extra={
                "original_message_id": str(message_id),
                "new_message_id": result["message_id"],
                "version": new_version,
            }
        )

        return {
            "message_id": result["message_id"],
            "message": result["message"],
            "reasoning_content": result.get("reasoning_content"),
            "version": new_version,
            "conversation_id": str(conversation_id),
            "suggested_questions": result.get("suggested_questions", []),
            "citations": result.get("citations", []),
            "audio_url": result.get("audio_url"),
            "audio_status": result.get("audio_status"),
        }

    async def regenerate_stream(
            self,
            message_id: uuid.UUID,
            config,
            workspace_id: uuid.UUID,
            user_id: str,
            variables: Optional[Dict[str, Any]] = None,
            web_search: bool = False,
            memory: bool = True,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """重新生成回复（流式输出，多版本支持）

        Args:
            message_id: 原 AI 回复的消息ID
            config: AgentConfig
            workspace_id: 工作空间ID
            user_id: 用户ID
            variables: 变量参数
            web_search: 是否启用网络搜索
            memory: 是否启用长期记忆
            storage_type: 存储类型
            user_rag_memory_id: RAG 记忆ID

        Yields:
            str: SSE 格式的事件数据
        """
        from app.models import Message, Conversation
        from app.core.error_codes import BizCode
        from app.core.exceptions import BusinessException
        from app.schemas.app_schema import FileType, TransferMethod

        # 1. 获取原消息
        original_msg = self.db.get(Message, message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)
        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)

        # 2. 获取父用户消息（找不到已回填的 parent_message_id 时按 created_at 回溯；
        # 若定位到的父消息已被逻辑删除则自动恢复，见 _locate_or_restore_parent_user_message）
        parent_msg = self._locate_or_restore_parent_user_message(original_msg)
        parent_msg_id = parent_msg.id

        user_message_content = parent_msg.content if parent_msg else ""

        # 3. 查询同一 parent_message_id 下的所有版本，获取最大版本号
        from sqlalchemy import select, func
        max_version_result = self.db.scalars(
            select(func.max(Message.version))
            .where(
                Message.conversation_id == original_msg.conversation_id,
                Message.parent_message_id == parent_msg_id,
                Message.role == "assistant",
                Message.is_deleted.is_not(True),
            )
        ).first()
        max_version = max_version_result or 0
        new_version = max_version + 1

        # 4. 将同一 parent_message_id 下所有版本标记为非当前
        self.db.query(Message).filter(
            Message.conversation_id == original_msg.conversation_id,
            Message.parent_message_id == parent_msg_id,
            Message.role == "assistant",
        ).update({"is_current": False})
        self.db.commit()

        # 5. 提取父消息中的文件信息
        files = None
        if parent_msg and parent_msg.meta_data:
            meta_files = parent_msg.meta_data.get("files", [])
            if meta_files:
                files = []
                for f in meta_files:
                    try:
                        file_input = FileInput(
                            type=FileType(f.get("type", "document")),
                            transfer_method=TransferMethod.REMOTE_URL if f.get("url") else TransferMethod.LOCAL_FILE,
                            url=f.get("url"),
                            file_type=f.get("file_type"),
                            name=f.get("name"),
                            size=f.get("size"),
                        )
                        files.append(file_input)
                    except Exception as e:
                        logger.warning(f"转换文件信息失败: {e}")

        # 6. 加载上下文
        conversation_id = original_msg.conversation_id
        filtered_history = await self._load_history_before_message(
            conversation_id=conversation_id,
            before_time=parent_msg.created_at,
            max_history=settings.AGENT_MAX_HISTORY
        )

        # 7. 流式调用（传入版本参数，由 agent_chat_stream 保存）
        async for event_str in self.agent_chat_stream(
                message=user_message_content,
                conversation_id=conversation_id,
                config=config,
                files=files,
                user_id=user_id,
                variables=variables,
                web_search=web_search,
                memory=memory,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                workspace_id=str(workspace_id),
                history=filtered_history,
                skip_save=True,
                parent_message_id=parent_msg_id,
                version=new_version,
        ):
            yield event_str

        logger.info(
            "重新生成回复成功（流式）",
            extra={
                "original_message_id": str(message_id),
                "version": new_version,
            }
        )

    async def _load_history_before_message(
            self,
            conversation_id: uuid.UUID,
            before_time,
            max_history: int = 10
    ) -> List[Dict[str, Any]]:
        """加载指定时间之前的历史消息（用于重新生成场景）"""
        from sqlalchemy import select
        from app.models import Message

        history_msgs = self.db.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.is_current.is_not(False),
                Message.created_at < before_time,
                Message.is_deleted.is_not(True),
            )
            .order_by(Message.created_at.asc())
            .limit(max_history)
        ).all()

        filtered_history = []
        for msg in history_msgs:
            msg_dict = {
                "role": msg.role,
                "content": [{"type": "text", "text": msg.content}]
            }
            if msg.role == "user" and msg.meta_data:
                history_files = msg.meta_data.get("history_files", {})
                if history_files and history_files.get("content"):
                    msg_dict["content"].extend(history_files.get("content"))
            filtered_history.append(msg_dict)

        logger.debug(
            "加载指定时间前的历史消息",
            extra={"conversation_id": str(conversation_id), "loaded_count": len(filtered_history)}
        )

        return filtered_history


# ==================== 依赖注入函数 ====================

def get_app_chat_service(
        db: Annotated[Session, Depends(get_db)]
) -> AppChatService:
    """获取工作流服务（依赖注入）"""
    return AppChatService(db)

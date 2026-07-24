"""
试运行服务

提供 Agent 试运行功能，允许用户在不发布应用的情况下测试配置。
"""
import asyncio
import datetime
import json
import time
import uuid
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

from langchain.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.datetime_utils import to_iso_z, utcnow_naive
from app.core.agent.agent_middleware import AgentMiddleware
from app.core.agent.langchain_agent import LangChainAgent
from app.core.config import settings
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.schemas.chunk_schema import KnowledgeRetrievalCaller, RetrieveType
from app.schemas.knowledge_retrieval_schema import KnowledgeRetrievalRequest
from app.services.knowledge_retrieval_service import KnowledgeRetrievalService
from app.db import get_async_db_context
from app.models import App, AgentConfig, ModelConfig, Message, Conversation, Knowledge
from app.models.agent_execution_model import AgentExecution
from app.models.annotation_model import AppAnnotation, AppAnnotationHitLog, AppAnnotationSetting
from app.models.appshare_model import AppShare
from app.models.file_metadata_model import FileMetadata
from app.models.knowledgeshare_model import KnowledgeShare
from app.models.models_model import ModelCapability, ModelType, ModelApiKey
from app.repositories.tool_repository import ToolRepository
from app.schemas.app_schema import FileInput, Citation, FileType, TransferMethod
from app.schemas.model_schema import ModelInfo
from app.schemas.prompt_schema import PromptMessageRole, render_prompt_message
from app.services.context_engine_manager import ContextEngineManager
from app.services.annotation_service import AnnotationService
from app.services.langchain_tool_server import Search
from app.services.memory_config_service import MemoryConfigService
from app.services.model_parameter_merger import ModelParameterMerger
from app.services.model_service import ModelApiKeyService
from app.services.multimodal_service import MultimodalService
from app.services.tool_orchestrator import ToolOrchestrator
from app.services.context_assembler import (
    ContextEvidence,
    append_external_context_rule,
)
from app.services.tool_service import ToolService

logger = get_business_logger()


def _snapshot_annotations(annotations: List[AppAnnotation]) -> List[SimpleNamespace]:
    """Detach annotation values before an embedding calculation runs in a worker thread."""
    return [SimpleNamespace(
        id=item.id,
        question=item.question,
        answer=item.answer,
        embedding=list(item.embedding) if item.embedding else None,
        created_at=item.created_at,
        updated_at=item.updated_at,
    ) for item in annotations]


def _snapshot_message(message: Message) -> SimpleNamespace:
    """Copy message values that are used after its async session has closed."""
    return SimpleNamespace(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        meta_data=dict(message.meta_data or {}),
        parent_message_id=message.parent_message_id,
        version=message.version,
        is_current=message.is_current,
        is_deleted=message.is_deleted,
        created_at=message.created_at,
    )


class KnowledgeRetrievalInput(BaseModel):
    """知识库检索工具输入参数"""
    query: str = Field(description="需要检索的问题或关键词")


class WebSearchInput(BaseModel):
    """网络搜索工具输入参数"""
    query: str = Field(description="需要搜索的问题或关键词")


def create_web_search_tool(web_search_config: Dict[str, Any]):
    """创建网络搜索工具

    Args:
        web_search_config: 网络搜索配置

    Returns:
        网络搜索工具
    """
    _ = web_search_config
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

    web_search_tool._tool_meta = {"tool_type": "web_search", "sources": []}
    return web_search_tool


async def _retrieve_chunks_via_standard(query: str, kb_config: Dict[str, Any]) -> list:
    """标准化知识库检索：走 KnowledgeRetrievalService + KnowledgeRetrievalRequest。

    读取 agent 的 ``knowledge_retrieval`` 配置（top_k / similarity_threshold /
    retrieve_type / reranker_id）。由于
    KnowledgeRetrievalRequest 只携带一组检索参数，这里沿用工作流知识库节点的约定，
    用第一个 KB 的参数作为全局默认；缺失值回落到 schema 默认值。
    """
    knowledge_bases = (kb_config or {}).get("knowledge_bases", []) or []
    kb_ids = [kb.get("kb_id") for kb in knowledge_bases if kb.get("kb_id")]
    if not kb_ids:
        return []

    first_kb = knowledge_bases[0] or {}

    def _as_float(value: Any, default: float) -> float:
        try:
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    retrieve_type_str = str(first_kb.get("retrieve_type") or "hybrid").strip().lower()
    try:
        retrieve_type = RetrieveType(retrieve_type_str)
    except ValueError:
        retrieve_type = RetrieveType.HYBRID

    rerank_id = None
    if kb_config.get("reranker_id"):
        try:
            rerank_id = uuid.UUID(str(kb_config.get("reranker_id")))
        except (ValueError, AttributeError):
            rerank_id = None

    # 分词检索不使用 vector_similarity_weight，其他检索类型从配置读取
    if retrieve_type == RetrieveType.PARTICIPLE:
        vector_similarity_weight = None
    else:
        vector_similarity_weight = _as_float(first_kb.get("vector_similarity_weight"), 0.5)
    
    request = KnowledgeRetrievalRequest(
        query=query,
        caller=KnowledgeRetrievalCaller.AGENT,
        kb_ids=[uuid.UUID(kid) for kid in kb_ids],
        top_k=_as_int(first_kb.get("top_k"), 3),
        similarity_threshold=_as_float(first_kb.get("similarity_threshold"), 0.7),
        vector_similarity_weight=vector_similarity_weight,
        retrieve_type=retrieve_type,
        rerank_id=rerank_id,
    )

    result = await KnowledgeRetrievalService.retrieve_async(request=request, principal=None)

    return result.chunks


def create_knowledge_retrieval_tool(kb_config, kb_ids, user_id, citations_collector: Optional[List[Citation]] = None, kb_names: Optional[List[Dict]] = None):
    """从知识库中检索相关信息。当用户的问题需要参考知识库、文档或历史记录时，使用此工具进行检索。

    Args:
        kb_config: 知识库配置
        kb_ids: 知识库ID列表
        user_id: 用户ID
        citations_collector: 用于收集引用信息的列表（由外部传入，tool 执行时填充）
        kb_names: 知识库名称列表 [{"id": "...", "name": "..."}]

    Returns:
        检索到的相关知识内容
    """
    logger.info(f"创建知识库检索工具，用户：{user_id}")

    @tool(args_schema=KnowledgeRetrievalInput)
    async def knowledge_retrieval_tool(query: str) -> str:
        """从知识库中检索相关信息。当用户的问题需要参考知识库、文档或历史记录时，使用此工具进行检索。

        Args:
            query: 需要检索的问题或关键词

        Returns:
            检索到的相关知识内容
        """

        try:

            retrieve_chunks_result = await _retrieve_chunks_via_standard(query, kb_config)
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

                # 收集引用信息
                if citations_collector is not None:
                    seen_doc_ids = {c.get("document_id") for c in citations_collector}
                    for chunk in retrieve_chunks_result:
                        meta = chunk.metadata or {}
                        document_id = meta.get("document_id")
                        if document_id and document_id not in seen_doc_ids:
                            seen_doc_ids.add(document_id)
                            citations_collector.append(Citation(
                                document_id=str(document_id),
                                doc_id=meta.get("doc_id", ""),
                                file_name=meta.get("file_name", ""),
                                knowledge_id=str(meta.get("knowledge_id", "")),
                                score=meta.get("score", 0)
                            ))

                # 收集每条结果的来源知识库信息（用于 agent_executions 记录）
                # 构建知识库 ID → 名称映射
                kb_name_map = {item["id"]: item["name"] for item in (kb_names or [])}
                sources = []
                for chunk in retrieve_chunks_result:
                    meta = chunk.metadata or {}
                    kid = str(meta.get("knowledge_id", ""))
                    sources.append({
                        "knowledge_id": kid,
                        "knowledge_name": kb_name_map.get(kid, kid),
                        "file_name": meta.get("file_name", ""),
                        "content": chunk.page_content[:500] if chunk.page_content else "",
                    })
                # 把来源信息挂到函数属性上，供外部读取
                knowledge_retrieval_tool._last_sources = sources
                from app.services.context_assembler import ContextEvidence
                knowledge_retrieval_tool._context_evidence = [
                    ContextEvidence(
                        source_type="knowledge",
                        source_id=str((chunk.metadata or {}).get("chunk_id") or
                                      (chunk.metadata or {}).get("id") or "") or None,
                        content=chunk.page_content or "",
                        score=(chunk.metadata or {}).get("score"),
                        metadata={
                            "knowledge_id": str((chunk.metadata or {}).get("knowledge_id", "")),
                            "document_id": str((chunk.metadata or {}).get("document_id", "")),
                            "file_name": (chunk.metadata or {}).get("file_name", ""),
                        },
                    ) for chunk in retrieve_chunks_result if chunk.page_content
                ]

                return f"检索到以下相关信息：\n\n{context}"
            else:
                knowledge_retrieval_tool._last_sources = []
                knowledge_retrieval_tool._context_evidence = []
                logger.warning("知识库检索未找到结果")
                return "未找到相关信息"
        except Exception as e:
            knowledge_retrieval_tool._context_evidence = []
            logger.error("知识库检索失败", extra={"error": str(e), "error_type": type(e).__name__})
            return f"检索失败: {str(e)}"

    # 挂载工具元数据，供 Agent 执行记录使用
    knowledge_retrieval_tool._tool_meta = {
        "tool_type": "knowledge_retrieval",
        "sources": [{"id": item["id"], "name": item["name"], "knowledge_name": item["name"]} for item in (kb_names or [])],
    }
    return knowledge_retrieval_tool


class AgentRunService:
    """Agent运行服务类"""

    def __init__(self, db: Session | AsyncSession):
        """Agent运行服务

        Args:
            db: 数据库会话
        """
        self.db = db

    async def _resolve_app_tenant_id_async(self, app_id: uuid.UUID) -> Optional[uuid.UUID]:
        async with get_async_db_context() as db:
            result = await db.execute(
                select(App.workspace_id).where(App.id == app_id).limit(1)
            )
            workspace_id = result.scalar_one_or_none()
            if not workspace_id:
                return None
            return await ToolRepository.get_tenant_id_by_workspace_id_async(db, str(workspace_id))

    async def _get_tenant_id_by_workspace_id_async(self, workspace_id: uuid.UUID) -> Optional[uuid.UUID]:
        async with get_async_db_context() as db:
            return await ToolRepository.get_tenant_id_by_workspace_id_async(db, str(workspace_id))

    async def _get_last_current_assistant_id_async(self, conv_uuid: uuid.UUID | None) -> Optional[uuid.UUID]:
        if not conv_uuid:
            return None
        async with get_async_db_context() as db:
            result = await db.execute(
                select(Message.id).where(
                    Message.conversation_id == conv_uuid,
                    Message.role == "assistant",
                    Message.is_current.is_(True),
                    Message.is_deleted.is_not(True),
                ).order_by(Message.created_at.desc()).limit(1)
            )
            return result.scalar_one_or_none()

    async def _get_message_async(self, message_id: uuid.UUID) -> Optional[SimpleNamespace]:
        async with get_async_db_context() as db:
            message = await db.get(Message, message_id)
            return _snapshot_message(message) if message else None

    async def _mark_message_not_current_async(self, message_id: uuid.UUID) -> None:
        async with get_async_db_context() as db:
            record = await db.get(Message, message_id)
            if not record:
                return
            record.is_current = False
            await db.commit()

    async def _save_regenerated_message_async(
            self,
            *,
            conversation_id: uuid.UUID,
            content: str,
            version: int,
            parent_message_id: uuid.UUID,
            meta_data: dict,
    ) -> SimpleNamespace:
        async with get_async_db_context() as db:
            new_msg = Message(
                conversation_id=conversation_id,
                role="assistant",
                content=content,
                version=version,
                is_current=True,
                parent_message_id=parent_message_id,
                meta_data=meta_data,
            )
            db.add(new_msg)

            conversation = await db.get(Conversation, conversation_id)
            if conversation:
                conversation.message_count = int(conversation.message_count or 0) + 1

            await db.commit()
            await db.refresh(new_msg)
            return _snapshot_message(new_msg)

    async def _create_tts_file_metadata_async(
            self,
            *,
            file_id: uuid.UUID,
            tenant_id: Optional[uuid.UUID],
            workspace_id: Optional[uuid.UUID],
            file_key: str,
            file_name: str,
            file_ext: str,
            content_type: str,
    ) -> None:
        async with get_async_db_context() as db:
            db.add(
                FileMetadata(
                    id=file_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    file_key=file_key,
                    file_name=file_name,
                    file_ext=file_ext,
                    file_size=0,
                    content_type=content_type,
                    status="pending",
                )
            )
            await db.commit()

    async def _update_tts_file_metadata_async(
            self,
            *,
            file_id: uuid.UUID,
            status: str,
            file_size: Optional[int] = None,
    ) -> None:
        async with get_async_db_context() as db:
            record = await db.get(FileMetadata, file_id)
            if not record:
                return
            record.status = status
            if file_size is not None:
                record.file_size = file_size
            await db.commit()

    async def _add_message_async(
            self,
            *,
            conversation_id: uuid.UUID,
            role: str,
            content: str,
            meta_data: Optional[dict] = None,
            message_id: Optional[uuid.UUID] = None,
            status: str = "completed",
            parent_message_id: Optional[uuid.UUID] = None,
    ) -> Message:
        async with get_async_db_context() as db:
            conversation = await db.get(Conversation, conversation_id)
            if not conversation:
                raise BusinessException(f"会话不存在: {conversation_id}", BizCode.NOT_FOUND)

            message = Message(
                id=message_id if message_id else uuid.uuid4(),
                conversation_id=conversation_id,
                role=role,
                content=content,
                meta_data=meta_data,
                status=status,
                parent_message_id=parent_message_id,
            )
            db.add(message)

            conversation.message_count = int(conversation.message_count or 0) + 1
            if conversation.message_count <= 2 and role == "user":
                conversation.title = content[:50] + ("..." if len(content) > 50 else "")

            await db.commit()
            await db.refresh(message)
            return message

    async def _create_agent_execution_async(
            self,
            *,
            app_id: uuid.UUID,
            conversation_id: uuid.UUID,
            agent_config_id: uuid.UUID,
            started_at: datetime.datetime,
            model_name: str,
            provider: Optional[str],
    ) -> uuid.UUID:
        async with get_async_db_context() as db:
            execution = AgentExecution(
                app_id=app_id,
                conversation_id=conversation_id,
                message_id=None,
                agent_config_id=agent_config_id,
                release_id=None,
                triggered_by=None,
                steps=[],
                status="running",
                started_at=started_at,
                meta_data={
                    "model": model_name,
                    "provider": provider,
                },
            )
            db.add(execution)
            await db.commit()
            await db.refresh(execution)
            return execution.id

    async def _update_agent_execution_completed_async(
            self,
            execution_id: uuid.UUID,
            *,
            steps: list,
            status: str = "completed",
            elapsed_time: Optional[float] = None,
            token_usage: Optional[dict] = None,
            error_message: Optional[str] = None,
            message_id: Optional[uuid.UUID] = None,
    ) -> None:
        async with get_async_db_context() as db:
            result = await db.execute(
                select(AgentExecution).where(AgentExecution.id == execution_id)
            )
            record = result.scalar_one_or_none()
            if not record:
                return

            record.steps = steps
            record.status = status
            record.completed_at = utcnow_naive()
            if elapsed_time is not None:
                record.elapsed_time = elapsed_time
            if token_usage is not None:
                record.token_usage = token_usage
            if error_message is not None:
                record.error_message = error_message
            if message_id is not None:
                record.message_id = message_id

            await db.commit()

    async def _record_api_key_usage_async(self, api_key_id: uuid.UUID | None) -> bool:
        if not api_key_id:
            return False
        async with get_async_db_context() as db:
            api_key = await db.get(ModelApiKey, api_key_id)
            if not api_key:
                return False
            current_count = int(api_key.usage_count or "0")
            api_key.usage_count = str(current_count + 1)
            api_key.last_used_at = utcnow_naive()
            await db.commit()
            return True

    def _build_debug_id(self) -> str:
        """生成可用于日志和 SSE 对齐的错误追踪 ID。"""
        return f"err_{uuid.uuid4().hex[:12]}"

    def _extract_exception_message(self, error: Exception) -> str:
        """优先从异常对象结构中提取更干净的错误消息。"""
        body = getattr(error, "body", None)
        if isinstance(body, dict):
            error_obj = body.get("error")
            if isinstance(error_obj, dict):
                message = error_obj.get("message")
                if isinstance(message, str) and message.strip():
                    return message
            message = body.get("message")
            if isinstance(message, str) and message.strip():
                return message

        message = getattr(error, "message", None)
        if isinstance(message, str) and message.strip():
            return message

        return str(error)

    def _build_compact_error(self, error: Exception, *, debug_id: Optional[str] = None) -> Dict[str, Any]:
        """构建精简但足够定位问题的结构化错误信息。"""
        compact_error: Dict[str, Any] = {
            "message": self._extract_exception_message(error),
            "type": type(error).__name__,
            "debug_id": debug_id or self._build_debug_id(),
        }

        def apply_exception_fields(target: Dict[str, Any], source: Exception) -> None:
            for attr_name, target_key in (
                ("status_code", "status"),
                ("request_id", "request_id"),
                ("code", "code"),
                ("param", "param"),
                ("type", "type"),
            ):
                attr_value = getattr(source, attr_name, None)
                if attr_value is not None:
                    target[target_key] = attr_value

        if isinstance(error, BusinessException):
            compact_error["message"] = error.message
            compact_error["code"] = int(error.code) if error.code is not None else int(BizCode.BAD_REQUEST)
            if error.context:
                compact_error["context"] = error.context

            if error.cause:
                cause_message = self._extract_exception_message(error.cause)
                if cause_message:
                    compact_error["message"] = cause_message
                apply_exception_fields(compact_error, error.cause)
            return compact_error

        apply_exception_fields(compact_error, error)

        return compact_error

    def _build_model_error_event_data(
            self,
            *,
            model_index: int,
            model_config_id: str,
            label: str,
            conversation_id: Optional[str],
            error: Any,
            timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """统一构建 compare 场景下的 model_error 事件。"""
        return {
            "model_index": model_index,
            "model_config_id": model_config_id,
            "label": label,
            "conversation_id": conversation_id,
            "error": error,
            "timestamp": timestamp if timestamp is not None else time.time()
        }

    def _build_model_end_event_data(
            self,
            *,
            model_index: int,
            model_config_id: str,
            label: str,
            conversation_id: Optional[str],
            elapsed_time: float,
            message_length: int = 0,
            audio_url: Optional[str] = None,
            audio_status: Optional[str] = None,
            citations: Optional[List[Any]] = None,
            suggested_questions: Optional[List[Any]] = None,
            status: str = "completed",
            error: Any = None,
            message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """统一构建 compare 场景下的 model_end 事件。"""
        data = {
            "model_index": model_index,
            "model_config_id": model_config_id,
            "label": label,
            "conversation_id": conversation_id,
            "elapsed_time": elapsed_time,
            "message_length": message_length,
            "audio_url": audio_url,
            "audio_status": audio_status,
            "citations": citations or [],
            "suggested_questions": suggested_questions or [],
            "status": status,
            "message_id": message_id,
            "timestamp": time.time()
        }
        if error is not None:
            data["error"] = error
        return data

    async def _check_annotation_match(self, app_id: uuid.UUID, message: str,
                                    source: str = "") -> Optional[dict]:
        """检查是否命中标注

        Args:
            app_id: 应用ID
            message: 用户消息
            source: 来源（用于记录命中来源）

        Returns:
            命中返回标注结果字典，未命中返回None
        """
        try:
            async with get_async_db_context() as db:
                result = await db.execute(
                    select(AppAnnotationSetting).where(AppAnnotationSetting.app_id == app_id).limit(1)
                )
                setting = result.scalar_one_or_none()
                if not setting or not setting.enabled:
                    return None
                if not setting.model_config_id:
                    return None

                result = await db.execute(
                    select(AppAnnotation).where(
                        AppAnnotation.app_id == app_id,
                        AppAnnotation.is_active == 1,
                    )
                )
                annotations = _snapshot_annotations(list(result.scalars().all()))
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
                api_key_data = {
                    "model_name": api_key_obj.model_name,
                    "provider": api_key_obj.provider,
                    "api_key": api_key_obj.api_key,
                    "api_base": api_key_obj.api_base,
                }

            from app.core.models.base import RedBearModelConfig
            config = RedBearModelConfig(
                model_name=api_key_data["model_name"],
                provider=api_key_data["provider"],
                api_key=api_key_data["api_key"],
                base_url=api_key_data["api_base"] or None,
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
            logger.warning(f"标注匹配检查失败: {e}")
            return None

    async def _load_annotation_context_evidence(
            self, app_id: uuid.UUID, message: str
    ) -> List[ContextEvidence]:
        """只在知识库或记忆工具返回有效证据后调用。"""
        try:
            async with get_async_db_context() as db:
                setting = (await db.execute(select(AppAnnotationSetting).where(
                    AppAnnotationSetting.app_id == app_id).limit(1))).scalar_one_or_none()
                if not setting or not setting.enabled or not setting.model_config_id:
                    return []
                annotations = _snapshot_annotations(list((await db.execute(select(AppAnnotation).where(
                    AppAnnotation.app_id == app_id,
                    AppAnnotation.is_active == 1,
                ))).scalars().all()))
                if not annotations:
                    return []
                api_key_obj = await ModelApiKeyService.get_available_api_key_async(
                    db, setting.model_config_id,
                    tenant_id=await self._resolve_app_tenant_id_async(app_id),
                )
                if not api_key_obj:
                    return []
                api_key_data = {
                    "model_name": api_key_obj.model_name,
                    "provider": api_key_obj.provider,
                    "api_key": api_key_obj.api_key,
                    "api_base": api_key_obj.api_base,
                }
            from app.core.models.base import RedBearModelConfig
            model_config = RedBearModelConfig(
                model_name=api_key_data["model_name"], provider=api_key_data["provider"],
                api_key=api_key_data["api_key"], base_url=api_key_data["api_base"] or None,
                timeout=60, max_retries=3,
            )
            candidates = await asyncio.to_thread(
                AnnotationService.find_context_candidates,
                message, annotations, model_config, 0.6, 3,
            )
            logger.info(
                "[上下文组装] 标注候选 | "
                f"应用={str(app_id)[:8]} | 标注总数={len(annotations)} | "
                f"候选={len(candidates)} | 阈值=0.6 | top_k=3"
            )
            return [ContextEvidence(
                source_type="annotation", source_id=item["annotation_id"],
                content=f"参考问题：{item['question']}\n参考答案：{item['answer']}",
                score=item["similarity"], created_at=item.get("created_at"),
                updated_at=item.get("updated_at"), metadata={"question": item["question"]},
            ) for item in candidates]
        except Exception:
            logger.warning("[上下文组装] 标注候选加载失败，继续使用知识库/记忆证据", exc_info=True)
            return []

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

    async def load_tools_config(self, tools_config, web_search, tenant_id, user_id=None, workspace_id=None) -> list:
        """加载工具配置"""
        tools = []
        if web_search:
            search_tool = create_web_search_tool({})
            tools.append(search_tool)
        if not tools_config:
            return tools
        async with get_async_db_context() as db:
            tool_service = ToolService(db)

            if tools_config and isinstance(tools_config, list):
                for tool_config in tools_config:
                    if tool_config.get("enabled", False):
                        # 根据工具名称查找工具实例
                        tool_instance = await tool_service.get_tool_instance_async(
                            tool_config.get("tool_id", ""),
                            tenant_id,
                        )
                        if tool_instance:
                            tool_instance.set_runtime_context(user_id=user_id, workspace_id=workspace_id)
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

    async def load_skill_config(
            self,
            skills_config: dict | None,
            message: str,
            tenant_id,
            user_id=None,
            workspace_id=None,
    ) -> tuple[list, str]:
        if not skills_config:
            return [], ""

        tools = []
        skill_prompts = ""
        skill_enable = skills_config.get("enabled", False)
        if skill_enable:
            middleware = AgentMiddleware(skills=skills_config)
            async with get_async_db_context() as db:
                skill_tools, skill_configs, tool_to_skill_map = await middleware.load_skill_tools_async(
                    db,
                    tenant_id,
                    runtime_context={"user_id": user_id, "workspace_id": workspace_id},
                )

            # 给技能工具挂载元数据（技能名称）
            for t in skill_tools:
                t_name = getattr(t, "name", None)
                if t_name and t_name in tool_to_skill_map:
                    skill_id = tool_to_skill_map[t_name]
                    skill_cfg = skill_configs.get(skill_id, {})
                    skill_name = skill_cfg.get("name", t_name)
                    t._tool_meta = {
                        "tool_type": "skill",
                        "sources": [{"id": skill_id, "name": skill_name}],
                    }

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

    async def load_knowledge_retrieval_config(
            self,
            knowledge_retrieval_config: dict | None,
            user_id
    ) -> tuple[list, list]:
        """返回 (tools, citations_collector)"""
        if not knowledge_retrieval_config:
            return [], []

        citations_collector = []
        tools = []
        knowledge_bases = knowledge_retrieval_config.get("knowledge_bases", [])
        kb_ids = [kb["kb_id"] for kb in knowledge_bases if kb.get("kb_id")]
        if kb_ids:
            # 查询知识库名称
            kb_names = []
            try:
                async with get_async_db_context() as db:
                    result = await db.execute(
                        select(Knowledge.id, Knowledge.name).where(Knowledge.id.in_(kb_ids))
                    )
                    rows = result.all()
                kb_names = [{"id": str(r.id), "name": r.name} for r in rows]

                # 对于共享知识库，chunk元数据中的knowledge_id是source_kb_id，
                # 需要将source_kb_id也映射到名称，否则会显示为ID
                target_kb_ids = [uuid.UUID(kid) for kid in kb_ids]
                async with get_async_db_context() as db:
                    result = await db.execute(
                        select(KnowledgeShare.source_kb_id, KnowledgeShare.target_kb_id).where(
                            KnowledgeShare.target_kb_id.in_(target_kb_ids)
                        )
                    )
                    share_rows = result.all()
                if share_rows:
                    id_to_name = {str(r.id): r.name for r in rows}
                    for sr in share_rows:
                        source_name = id_to_name.get(str(sr.target_kb_id))
                        if source_name:
                            kb_names.append({"id": str(sr.source_kb_id), "name": source_name})
            except Exception:
                kb_names = [{"id": kid, "name": kid} for kid in kb_ids]

            kb_tool = create_knowledge_retrieval_tool(
                knowledge_retrieval_config, kb_ids, user_id,
                citations_collector=citations_collector,
                kb_names=kb_names
            )
            tools.append(kb_tool)
            logger.debug(
                "已添加知识库检索工具",
                extra={"kb_ids": kb_ids, "tool_count": len(tools)}
            )
        return tools, citations_collector

    async def load_memory_config(
            self,
            memory_config: dict | None,
            user_id,
            workspace_id: uuid.UUID,
            storage_type,
            user_rag_memory_id
    ) -> tuple[list, bool]:
        """加载长期记忆配置"""
        from app.core.memory.memory_service import create_long_term_memory_tool

        enabled = bool(memory_config and memory_config.get("enabled"))
        config_id = None
        if enabled and workspace_id:
            async with get_async_db_context() as db:
                config_id = await MemoryConfigService(db).get_workspace_active_config_id_async(workspace_id)

        tool = create_long_term_memory_tool(
            memory_config, user_id, workspace_id, storage_type, user_rag_memory_id,
            config_id=config_id,
        )
        tools = [tool] if tool else []
        if tools:
            logger.debug("已添加长期记忆工具", extra={"user_id": user_id, "tool_count": len(tools)})
        return tools, enabled

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
    def _get_opening_statement(
            features_config: Dict[str, Any],
            is_new_conversation: bool,
            variables: Optional[Dict[str, Any]] = None
    ) -> tuple[Any, Any]:
        """首轮对话时返回开场白文本（支持变量替换），否则返回 None"""
        if not is_new_conversation:
            return None, None
        opening = features_config.get("opening_statement", {})
        if not (isinstance(opening, dict) and opening.get("enabled") and opening.get("statement")):
            return None, None

        statement = opening["statement"]
        suggested_questions = opening["suggested_questions"]

        # 如果有变量，进行替换（仅支持 {{var_name}} 格式）
        if variables:
            for var_name, var_value in variables.items():
                placeholder = f"{{{{{var_name}}}}}"
                statement = statement.replace(placeholder, str(var_value))

        return statement, suggested_questions

    @staticmethod
    def _filter_citations(
            features_config: Dict[str, Any],
            citations: List[Citation]
    ) -> List[Any]:
        """根据 citation 开关决定是否返回引用来源，并根据 allow_download 附加下载链接"""
        citation_cfg = features_config.get("citation", {})
        if not (isinstance(citation_cfg, dict) and citation_cfg.get("enabled")):
            return []
        allow_download = citation_cfg.get("allow_download", False)
        result = []
        for cit in citations:
            item = cit.model_dump() if hasattr(cit, "model_dump") else dict(cit)
            if allow_download and item.get("document_id"):
                from app.core.config import settings
                item["download_url"] = f"{settings.FILE_LOCAL_SERVER_URL}/apps/citations/{item['document_id']}/download"
            result.append(item)
        return result

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
            files: Optional[List[FileInput]] = None,
            source: str = "",
            history: Optional[List[Dict[str, str]]] = None,
            skip_save: bool = False,
            execution_mode: Literal["in_process", "sandbox"] = "in_process",
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
            history: 外部传入的历史消息（可选，用于重新生成场景）
            skip_save: 是否跳过保存消息（用于重新生成场景）
            execution_mode: 执行模式 (in_process / sandbox)

        Returns:
            Dict: 包含 AI 回复和元数据的字典
        """
        start_time = time.time()
        user_message_id = uuid.uuid4()
        assistant_message_id = uuid.uuid4()
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
        tenant_id = await self._get_tenant_id_by_workspace_id_async(workspace_id)

        try:
            # 1. 获取 API Key 配置
            api_key_config = await self._get_api_key(model_config.id, tenant_id=tenant_id)
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

            # 4. 准备工具列表
            tools = []

            # 从配置中获取启用的工具
            tools.extend(await self.load_tools_config(tools_config, web_search, tenant_id, user_id, workspace_id))
            skill_tools, skill_prompts = await self.load_skill_config(skills_config, message, tenant_id, user_id, workspace_id)
            tools.extend(skill_tools)
            if skill_prompts:
                system_prompt = f"{system_prompt}\n\n{skill_prompts}"
            kb_tools, citations_collector = await self.load_knowledge_retrieval_config(knowledge_retrieval_config, user_id)
            tools.extend(kb_tools)
            # 添加长期记忆工具
            if memory:
                memory_tools, _ = await self.load_memory_config(
                    memory_config, user_id, workspace_id, storage_type, user_rag_memory_id
                )
                tools.extend(memory_tools)

            # 5. 处理会话ID（创建或验证），新会话时写入开场白
            is_new_conversation = not conversation_id
            opening, suggested_questions = None, None
            if not sub_agent:
                opening, suggested_questions = self._get_opening_statement(features_config, is_new_conversation, variables)
            conversation_id = await self._ensure_conversation(
                conversation_id=conversation_id,
                app_id=agent_config.app_id,
                workspace_id=workspace_id,
                user_id=user_id,
                opening_statement=opening,
                suggested_questions=suggested_questions
            )

            # 检查标注命中
            if not sub_agent:
                annotation_match = await self._check_annotation_match(agent_config.app_id, message,
                                                                      source=source)
                if annotation_match:
                    elapsed_time = time.time() - start_time
                    # skip_save=True 时由调用方自行保存版本化消息，跳过 run 内部重复保存
                    if not skip_save:
                        conv_uuid = uuid.UUID(conversation_id)
                        conv_uuid = uuid.UUID(conversation_id)
                        parent_message_id = await self._get_last_current_assistant_id_async(conv_uuid)
                        await self._add_message_async(
                            message_id=user_message_id,
                            conversation_id=conv_uuid,
                            role="user",
                            content=message,
                            meta_data={"files": []},
                            parent_message_id=parent_message_id,
                        )
                        await self._add_message_async(
                            message_id=assistant_message_id,
                            conversation_id=conv_uuid,
                            role="assistant",
                            content=annotation_match["answer"],
                            meta_data={"usage": {}},
                            parent_message_id=user_message_id,
                        )
                    return {
                        "message": annotation_match["answer"],
                        "reasoning_content": None,
                        "conversation_id": conversation_id,
                        "user_message_id": str(user_message_id),
                        "usage": None,
                        "elapsed_time": elapsed_time,
                        "suggested_questions": [],
                        "citations": [],
                        "audio_url": None,
                        "audio_status": None,
                        "annotation_hit": {
                            "annotation_id": str(annotation_match["annotation_id"]),
                            "similarity": annotation_match["similarity"],
                            "question": annotation_match["question"],
                        }
                    }

            model_info = ModelInfo(
                model_name=api_key_config["model_name"],
                provider=api_key_config["provider"],
                api_key=api_key_config["api_key"],
                api_base=api_key_config["api_base"],
                capability=api_key_config["capability"],
                is_omni=api_key_config["is_omni"],
                model_type=model_config.type
            )

            # 6. 加载历史消息（包含开场白）
            used_context_engine = False
            if history is None:
                context_engine_manager = ContextEngineManager(self.db)
                prepared_input = await context_engine_manager.prepare_app_agent_input(
                    features=features_config,
                    conversation_id=uuid.UUID(conversation_id),
                    system_prompt=system_prompt,
                    current_input=message,
                    current_provider=api_key_config.get("provider"),
                    current_is_omni=api_key_config.get("is_omni", False),
                    legacy_max_history=settings.AGENT_MAX_HISTORY,
                    model_config_id=model_config.id,
                )
                if prepared_input:
                    system_prompt, history = prepared_input
                    used_context_engine = True
                else:
                    history = await self._load_conversation_history(
                        conversation_id=conversation_id,
                        max_history=settings.AGENT_MAX_HISTORY,
                        current_provider=api_key_config.get("provider"),
                        current_is_omni=api_key_config.get("is_omni", False)
                    )
            # 否则使用外部传入的历史（用于重新生成场景）

            # 6. 处理多模态文件
            processed_files = None
            has_doc_with_images = False
            if files:
                provider = api_key_config.get("provider", "openai")
                multimodal_service = MultimodalService(self.db, model_info)
                fu_config = features_config.get("file_upload", {})
                if hasattr(fu_config, "model_dump"):
                    fu_config = fu_config.model_dump()
                doc_img_recognition = isinstance(fu_config, dict) and fu_config.get("document_image_recognition", False)
                processed_files = await multimodal_service.process_files(
                    files, document_image_recognition=doc_img_recognition,
                    workspace_id=workspace_id
                )
                logger.info(f"处理了 {len(processed_files)} 个文件，provider={provider}")
                capability = api_key_config.get("capability", [])
                has_doc_with_images = (
                    doc_img_recognition
                    and ModelCapability.VISION in capability
                    and any(f.type == FileType.DOCUMENT for f in files)
                )
            if has_doc_with_images:
                system_prompt += (
                    "\n\n文档文字中包含图片位置标记如 [图片 第2页 第1张]: <img src=\"url\"...>，"
                    "请在回答中用 Markdown 格式 ![图片描述](url) 展示对应图片。"
                    "重要：图片 URL 中包含 UUID（如 /storage/permanent/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx），"
                    "必须将 src 属性的值原封不动复制到 Markdown 的括号中，不得增删任何字符。"
                )

            # 7. 根据模型能力选择执行路径
            capability = api_key_config.get("capability", [])
            async def load_annotation_context():
                return await self._load_annotation_context_evidence(agent_config.app_id, message)
            system_prompt = append_external_context_rule(system_prompt)
            use_agent_mode = ModelCapability.FUNCTION_CALL in capability
            orchestrator_node_executions = []
            if not use_agent_mode and tools:
                # 弱模型：用 ReAct prompt 驱动多轮工具调用，将轨迹注入 system_prompt
                system_prompt, orchestrator_node_executions = await ToolOrchestrator.create_and_run(
                    tools=tools,
                    system_prompt=system_prompt,
                    message=message,
                    history=history,
                    api_key_config=api_key_config,
                    model_config=model_config,
                    effective_params=effective_params,
                    processed_files=processed_files,
                    context_evidence_loader=load_annotation_context,
                )
                tools = []

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
                deep_thinking=effective_params.get("deep_thinking", False),
                thinking_budget_tokens=effective_params.get("thinking_budget_tokens"),
                json_output=effective_params.get("json_output", False),
                capability=capability,
                context_query=message,
                context_base_text=system_prompt + "\n" + str(history) + "\n" + message,
                context_evidence_loader=load_annotation_context,
            )

            for t in tools:
                if hasattr(t, 'tool_instance') and hasattr(t.tool_instance, 'set_runtime_context'):
                    t.tool_instance.set_runtime_context(
                        user_id=user_id or "anonymous",
                        conversation_id=str(conversation_id) if conversation_id else None,
                        uploaded_files=processed_files or []
                    )
            context = None

            logger.debug(
                "准备调用 LangChain Agent",
                extra={
                    "model": api_key_config["model_name"],
                    "use_agent_mode": use_agent_mode,
                    "has_history": bool(history),
                    "has_files": bool(processed_files)
                }
            )

            # 创建 Agent 执行记录（running 状态）
            agent_execution_id = None
            if not sub_agent and not skip_save:
                agent_execution_id = await self._create_agent_execution_async(
                    app_id=agent_config.app_id,
                    conversation_id=uuid.UUID(conversation_id) if isinstance(conversation_id, str) else conversation_id,
                    agent_config_id=agent_config.id,
                    started_at=datetime.datetime.fromtimestamp(start_time),
                    model_name=api_key_config["model_name"],
                    provider=api_key_config.get("provider"),
                )

            # 8. 调用 Agent（支持多模态）
            result = await agent.chat(
                message=message,
                history=history,
                context=context,
                files=processed_files
            )

            elapsed_time = time.time() - start_time

            await self._record_api_key_usage_async(api_key_config.get("api_key_id"))

            # 9. 生成 TTS audio_url（在保存消息前生成，以便一并存入 meta_data）
            audio_url = await self._generate_tts(
                features_config, result["content"], api_key_config,
                tenant_id=tenant_id, workspace_id=workspace_id
            ) if not sub_agent else None

            # 过滤 citations（只调用一次）
            filtered_citations = self._filter_citations(features_config, citations_collector)

            # 生成建议问题（在保存消息前生成，以便存入 meta_data）
            suggested_questions = (await self._generate_suggested_questions(
                features_config, result["content"], api_key_config, effective_params
            )) if not sub_agent else []

            # 10. 保存会话消息（skip_save=True 时由调用方自行保存版本化消息，跳过 run 内部重复保存）
            message_id = None
            if not sub_agent and not skip_save:
                message_id = await self._save_conversation_message(
                    conversation_id=conversation_id,
                    user_message=message,
                    assistant_message=result["content"],
                    message_id=assistant_message_id,
                    user_message_id=user_message_id,
                    app_id=agent_config.app_id,
                    user_id=user_id,
                    meta_data={
                        "usage": result.get("usage", {
                            "prompt_tokens": 0,
                            "completion_tokens": 0,
                            "total_tokens": 0
                        }),
                        "reasoning_content": result.get("reasoning_content"),
                        "suggested_questions": suggested_questions
                    },
                    files=files,
                    processed_files=processed_files,
                    audio_url=audio_url,
                    citations=filtered_citations,
                    provider=api_key_config.get("provider"),
                    is_omni=api_key_config.get("is_omni", False)
                )
                if used_context_engine and not skip_save:
                    _ctx_kwargs = dict(
                        features=features_config,
                        conversation_id=uuid.UUID(conversation_id),
                        current_provider=api_key_config.get("provider"),
                        current_is_omni=api_key_config.get("is_omni", False),
                        legacy_max_history=settings.AGENT_MAX_HISTORY,
                        model_config_id=model_config.id,
                    )
                    async def _run_after_turn(kwargs=_ctx_kwargs):
                        async with get_async_db_context() as db2:
                            await ContextEngineManager(db2).after_app_turn(**kwargs)
                    asyncio.create_task(_run_after_turn())

            # 11. 更新 Agent 执行记录为 completed
            node_executions = result.get("node_executions", [])
            if not sub_agent and not skip_save:
                await self._update_agent_execution_completed_async(
                    execution_id=agent_execution_id,
                    steps=orchestrator_node_executions + node_executions,
                    status="completed",
                    elapsed_time=elapsed_time,
                    token_usage=result.get("usage"),
                )

            response = {
                "message": result["content"],
                "message_id": message_id,
                "user_message_id": str(user_message_id),
                "reasoning_content": result.get("reasoning_content"),
                "conversation_id": conversation_id,
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
            # 更新 Agent 执行记录为 failed
            if not sub_agent and not skip_save:
                try:
                    elapsed_time = time.time() - start_time
                    await self._update_agent_execution_completed_async(
                        execution_id=agent_execution_id,
                        steps=[],
                        status="failed",
                        elapsed_time=elapsed_time,
                        error_message=str(e)[:2000],
                    )
                except Exception:
                    pass
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
            web_search: bool = True,
            memory: bool = True,
            sub_agent: bool = False,
            files: Optional[List[FileInput]] = None,
            source: str = "",
            history: Optional[List[Dict[str, str]]] = None,
            skip_save: bool = False,
            user_message_id: Optional[uuid.UUID] = None,
            execution_mode: Literal["in_process", "sandbox"] = "in_process",

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
            history: 外部传入的历史消息（可选，用于重新生成场景）
            skip_save: 是否跳过保存消息

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
        tenant_id = await self._get_tenant_id_by_workspace_id_async(workspace_id)

        start_time = time.time()
        # 支持外部传入 user_message_id（多模型对比时预生成并随 model_start 回传前端）
        user_message_id = user_message_id or uuid.uuid4()
        assistant_message_id = uuid.uuid4()

        try:
            # 1. 获取 API Key 配置
            api_key_config = await self._get_api_key(model_config.id, tenant_id=tenant_id)
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

            # 4. 准备工具列表
            tools = []

            # 从配置中获取启用的工具
            tools.extend(await self.load_tools_config(tools_config, web_search, tenant_id, user_id, workspace_id))
            skill_tools, skill_prompts = await self.load_skill_config(skills_config, message, tenant_id, user_id, workspace_id)
            tools.extend(skill_tools)
            if skill_prompts:
                system_prompt = f"{system_prompt}\n\n{skill_prompts}"
            kb_tools, citations_collector = await self.load_knowledge_retrieval_config(knowledge_retrieval_config, user_id)
            tools.extend(kb_tools)

            # 添加长期记忆工具
            if memory:
                memory_tools, _ = await self.load_memory_config(
                    memory_config, user_id, workspace_id, storage_type, user_rag_memory_id
                )
                tools.extend(memory_tools)

            # 5. 处理会话ID（创建或验证），新会话时写入开场白
            is_new_conversation = not conversation_id
            opening, suggested_questions = None, None
            if not sub_agent:
                opening, suggested_questions = self._get_opening_statement(features_config, is_new_conversation, variables)
            conversation_id = await self._ensure_conversation(
                conversation_id=conversation_id,
                app_id=agent_config.app_id,
                workspace_id=workspace_id,
                user_id=user_id,
                sub_agent=sub_agent,
                opening_statement=opening,
                suggested_questions=suggested_questions
            )

            # 检查标注命中
            if not sub_agent:
                annotation_match = await self._check_annotation_match(agent_config.app_id, message,
                                                                      source=source)
                if annotation_match:
                    elapsed_time = time.time() - start_time
                    # skip_save=True 时由调用方自行保存版本化消息，跳过 run_stream 内部重复保存
                    if not skip_save:
                        conv_uuid = uuid.UUID(conversation_id)
                        parent_message_id = await self._get_last_current_assistant_id_async(conv_uuid)
                        await self._add_message_async(
                            message_id=user_message_id,
                            conversation_id=conv_uuid,
                            role="user",
                            content=message,
                            meta_data={"files": []},
                            parent_message_id=parent_message_id,
                        )
                        await self._add_message_async(
                            message_id=assistant_message_id,
                            conversation_id=conv_uuid,
                            role="assistant",
                            content=annotation_match["answer"],
                            meta_data={"usage": {}},
                            parent_message_id=user_message_id,
                        )
                    yield self._format_sse_event("start", {
                        "conversation_id": conversation_id,
                        "message_id": str(assistant_message_id),
                        "user_message_id": str(user_message_id),
                        "timestamp": time.time()
                    })
                    yield self._format_sse_event("message", {
                        "content": annotation_match["answer"],
                        "conversation_id": conversation_id,
                    })
                    end_data = {
                        "conversation_id": conversation_id,
                        "message": annotation_match["answer"],
                        "answer": annotation_match["answer"],
                        "usage": {},
                        "elapsed_time": elapsed_time,
                        "message_length": len(annotation_match["answer"]),
                        "annotation_hit": {
                            "annotation_id": str(annotation_match["annotation_id"]),
                            "similarity": annotation_match["similarity"],
                            "question": annotation_match["question"],
                        }
                    }
                    yield self._format_sse_event("end", end_data)
                    return

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
            used_context_engine = False
            if history is None:
                context_engine_manager = ContextEngineManager(self.db)
                prepared_input = await context_engine_manager.prepare_app_agent_input(
                    features=features_config,
                    conversation_id=uuid.UUID(conversation_id),
                    system_prompt=system_prompt,
                    current_input=message,
                    current_provider=api_key_config.get("provider"),
                    current_is_omni=api_key_config.get("is_omni", False),
                    legacy_max_history=settings.AGENT_MAX_HISTORY,
                    model_config_id=model_config.id,
                )
                if prepared_input:
                    system_prompt, history = prepared_input
                    used_context_engine = True
                else:
                    history = await self._load_conversation_history(
                        conversation_id=conversation_id,
                        max_history=settings.AGENT_MAX_HISTORY,
                        current_provider=api_key_config.get("provider"),
                        current_is_omni=api_key_config.get("is_omni", False)
                    )

            # 6. 处理多模态文件
            processed_files = None
            has_doc_with_images = False
            if files:
                provider = api_key_config.get("provider", "openai")
                multimodal_service = MultimodalService(self.db, model_info)
                fu_config = features_config.get("file_upload", {})
                if hasattr(fu_config, "model_dump"):
                    fu_config = fu_config.model_dump()
                doc_img_recognition = isinstance(fu_config, dict) and fu_config.get("document_image_recognition", False)
                processed_files = await multimodal_service.process_files(
                    files, document_image_recognition=doc_img_recognition,
                    workspace_id=workspace_id
                )
                logger.info(f"处理了 {len(processed_files)} 个文件，provider={provider}")
                capability = api_key_config.get("capability", [])
                has_doc_with_images = (
                    doc_img_recognition
                    and ModelCapability.VISION in capability
                    and any(f.type == FileType.DOCUMENT for f in files)
                )
            if has_doc_with_images:
                system_prompt += (
                    "\n\n文档文字中包含图片位置标记如 [图片 第2页 第1张]: <img src=\"url\"...>，"
                    "请在回答中用 Markdown 格式 ![图片描述](url) 展示对应图片。"
                    "重要：图片 URL 中包含 UUID（如 /storage/permanent/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx），"
                    "必须将 src 属性的值原封不动复制到 Markdown 的括号中，不得增删任何字符。"
                )

            # 7. 根据模型能力选择执行路径
            capability = api_key_config.get("capability", [])
            async def load_annotation_context():
                return await self._load_annotation_context_evidence(agent_config.app_id, message)
            system_prompt = append_external_context_rule(system_prompt)
            use_agent_mode = ModelCapability.FUNCTION_CALL in capability
            orchestrator_node_executions = []
            if not use_agent_mode and tools:
                # 弱模型：用 ReAct prompt 驱动多轮工具调用，将轨迹注入 system_prompt
                system_prompt, orchestrator_node_executions = await ToolOrchestrator.create_and_run(
                    tools=tools,
                    system_prompt=system_prompt,
                    message=message,
                    history=history,
                    api_key_config=api_key_config,
                    model_config=model_config,
                    effective_params=effective_params,
                    processed_files=processed_files,
                    context_evidence_loader=load_annotation_context,
                )
                tools = []

            # 创建 LangChain Agent (in-process) or route to sandbox
            if execution_mode == "sandbox":
                from app.services.e2b_agent_adapter import E2BAgentAdapter
                sandbox_payload = await self._build_sandbox_payload(
                    agent_config=agent_config,
                    model_config=model_config,
                    api_key_config=api_key_config,
                    effective_params=effective_params,
                    message=message,
                    system_prompt=system_prompt,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    tools=tools,
                    history=history,
                    context=None,
                    variables=variables,
                    files_config=features_config.get("file_upload", {}),
                )
                _sandbox_adapter = E2BAgentAdapter(self.db)
                _sandbox_stream = self._sandbox_event_stream(
                    payload=sandbox_payload,
                    workspace_id=str(workspace_id),
                    user_id=user_id or "",
                    conversation_id=str(conversation_id),
                    adapter=_sandbox_adapter,
                )
            else:
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
                    streaming=True,
                    deep_thinking=effective_params.get("deep_thinking", False),
                    thinking_budget_tokens=effective_params.get("thinking_budget_tokens"),
                    json_output=effective_params.get("json_output", False),
                    capability=capability,
                    context_query=message,
                    context_base_text=system_prompt + "\n" + str(history) + "\n" + message,
                    context_evidence_loader=load_annotation_context,
                )

                for t in tools:
                    if hasattr(t, 'tool_instance') and hasattr(t.tool_instance, 'set_runtime_context'):
                        t.tool_instance.set_runtime_context(
                            user_id=user_id or "anonymous",
                            conversation_id=str(conversation_id) if conversation_id else None,
                            uploaded_files=processed_files or []
                        )
                _sandbox_adapter = None
                _sandbox_stream = None

            context = None

            # 8. 发送开始事件
            yield self._format_sse_event("start", {
                "conversation_id": conversation_id,
                "message_id": str(assistant_message_id),
                "user_message_id": str(user_message_id),
                "timestamp": time.time()
            })

            # 把弱模型的工具调用步骤补发给前端
            for step in orchestrator_node_executions:
                event_type = "tool_error" if step.get("status") == "failed" else "tool_end"
                yield self._format_sse_event("tool_start", {
                    "step_id": step.get("step_id"),
                    "name": step.get("node_name"),
                    "input": step.get("input"),
                    "meta": step.get("meta"),
                })
                yield self._format_sse_event(event_type, {
                    "step_id": step.get("step_id"),
                    "name": step.get("node_name"),
                    "output": step.get("output"),
                    "error": step.get("error"),
                    "meta": step.get("meta"),
                })

            # 创建 Agent 执行记录（running 状态）
            _agent_execution_id = None
            if not sub_agent and not skip_save:
                _agent_execution_id = await self._create_agent_execution_async(
                    app_id=agent_config.app_id,
                    conversation_id=uuid.UUID(conversation_id) if isinstance(conversation_id, str) else conversation_id,
                    agent_config_id=agent_config.id,
                    started_at=datetime.datetime.fromtimestamp(start_time),
                    model_name=api_key_config["model_name"],
                    provider=api_key_config.get("provider"),
                )

            # close() 前把后续还会用到的 ORM 属性读成普通值，防止 close 后触发 DetachedInstanceError
            _app_id = agent_config.app_id
            _model_config_id = model_config.id
            _model_config_name = model_config.name

            # LLM 推理期间不需要 db，提前归还连接给连接池
            # 所有工具（knowledge/memory/web_search）均使用独立连接，不依赖 self.db
            # SQLAlchemy Session.close() 只归还底层连接，session 对象仍可复用（懒重连）
            # self.db.close()

            # 9. 流式调用 Agent（支持多模态），同时并行启动 TTS
            full_content = ""
            full_reasoning = ""
            total_tokens = 0
            node_executions = []
            sandbox_citations = []

            # 启动流式 TTS（文本边输出边合成）
            text_queue: asyncio.Queue = asyncio.Queue()
            stream_audio_url, tts_task = await self._generate_tts_streaming(
                features_config, api_key_config,
                text_queue=text_queue,
                tenant_id=tenant_id, workspace_id=workspace_id
            ) if not sub_agent else (None, None)

            if execution_mode == "sandbox":
                _chunk_stream = _sandbox_stream
            else:
                _chunk_stream = agent.chat_stream(
                    message=message,
                    history=history,
                    context=context,
                    files=processed_files
                )

            async for chunk in _chunk_stream:
                if isinstance(chunk, int):
                    total_tokens = chunk
                elif isinstance(chunk, dict) and chunk.get("type") == "reasoning":
                    full_reasoning += chunk["content"]
                    yield self._format_sse_event("reasoning", {"content": chunk["content"]})
                elif isinstance(chunk, dict) and chunk.get("type") == "node_executions":
                    node_executions = chunk.get("data", [])
                elif isinstance(chunk, dict) and chunk.get("type") == "tool_start":
                    yield self._format_sse_event("tool_start", {"step_id": chunk.get("step_id"), "name": chunk["name"], "input": chunk.get("input"), "meta": chunk.get("meta")})
                elif isinstance(chunk, dict) and chunk.get("type") == "tool_end":
                    yield self._format_sse_event("tool_end", {"step_id": chunk.get("step_id"), "name": chunk["name"], "output": chunk.get("output"), "meta": chunk.get("meta")})
                elif isinstance(chunk, dict) and chunk.get("type") == "tool_error":
                    yield self._format_sse_event("tool_error", {"step_id": chunk.get("step_id"), "name": chunk["name"], "error": chunk.get("error")})
                elif isinstance(chunk, dict) and chunk.get("type") == "agent_log":
                    yield self._format_sse_event("agent_log", chunk)
                elif isinstance(chunk, dict):
                    event_type = str(chunk.get("type") or "unknown")
                    yield self._format_sse_event(event_type, chunk)
                else:
                    full_content += chunk
                    yield self._format_sse_event("message", {"content": chunk})
                    if tts_task is not None:
                        await text_queue.put(chunk)

            # 文本结束，通知 TTS
            if tts_task is not None:
                await text_queue.put(None)

            # Merge sandbox citations into collector
            if _sandbox_adapter is not None:
                sandbox_citations = getattr(_sandbox_adapter, "_sandbox_citations", [])
                if sandbox_citations:
                    seen_ids = {c.get("document_id") for c in citations_collector}
                    for cit in sandbox_citations:
                        doc_id = cit.get("document_id")
                        if doc_id and doc_id not in seen_ids:
                            seen_ids.add(doc_id)
                            citations_collector.append(Citation(
                                document_id=str(doc_id),
                                doc_id=cit.get("doc_id", ""),
                                file_name=cit.get("file_name", ""),
                                knowledge_id=str(cit.get("knowledge_id", "")),
                                score=cit.get("score", 0),
                            ))

            elapsed_time = time.time() - start_time
            await self._record_api_key_usage_async(api_key_config.get("api_key_id"))

            if sub_agent:
                yield self._format_sse_event("sub_usage", {"total_tokens": total_tokens})

            # 过滤 citations（只调用一次）
            filtered_citations = self._filter_citations(features_config, citations_collector)

            suggested_questions = (await self._generate_suggested_questions(
                features_config, full_content, api_key_config, effective_params
            )) if not sub_agent else []

            # 11. 保存会话消息（skip_save=True 时由调用方自行保存版本化消息，跳过 run_stream 内部重复保存）
            message_id = None
            if not sub_agent and not skip_save:
                message_id = await self._save_conversation_message(
                    conversation_id=conversation_id,
                    user_message=message,
                    assistant_message=full_content,
                    message_id=assistant_message_id,
                    user_message_id=user_message_id,
                    app_id=_app_id,
                    user_id=user_id,
                    meta_data={
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                        "reasoning_content": full_reasoning or None,
                        "suggested_questions": suggested_questions
                    },
                    files=files,
                    processed_files=processed_files,
                    audio_url=stream_audio_url,
                    citations=filtered_citations,
                    provider=api_key_config.get("provider"),
                    is_omni=api_key_config.get("is_omni", False)
                )
                if used_context_engine and not skip_save:
                    _ctx_kwargs = dict(
                        features=features_config,
                        conversation_id=uuid.UUID(conversation_id),
                        current_provider=api_key_config.get("provider"),
                        current_is_omni=api_key_config.get("is_omni", False),
                        legacy_max_history=settings.AGENT_MAX_HISTORY,
                        model_config_id=_model_config_id,
                    )
                    async def _run_after_turn(kwargs=_ctx_kwargs):
                        async with get_async_db_context() as db2:
                            await ContextEngineManager(db2).after_app_turn(**kwargs)
                    asyncio.create_task(_run_after_turn())

            # 11.5 更新 Agent 执行记录为 completed
            if not sub_agent and not skip_save:
                await self._update_agent_execution_completed_async(
                    execution_id=_agent_execution_id,
                    steps=orchestrator_node_executions + node_executions,
                    status="completed",
                    elapsed_time=elapsed_time,
                    token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                )

            # 12. 发送结束事件（包含 suggested_questions、audio_url 和 audio_status）
            end_data: Dict[str, Any] = {
                "conversation_id": conversation_id,
                "message_id": message_id,
                "elapsed_time": elapsed_time,
                "message_length": len(full_content)
            }
            if not sub_agent:
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
                end_data["citations"] = filtered_citations
            yield self._format_sse_event("end", end_data)

            logger.info(
                "流式试运行完成",
                extra={
                    "model": _model_config_name,
                    "elapsed_time": elapsed_time,
                    "message_length": len(full_content)
                }
            )

        except Exception as e:
            debug_id = self._build_debug_id()
            compact_error = self._build_compact_error(e, debug_id=debug_id)
            logger.error(
                "流式 Agent 调用失败",
                extra={"error": str(e), "debug_id": debug_id, "compact_error": compact_error},
                exc_info=True
            )
            try:
                await self.db.rollback()
            except Exception:
                pass
            # 保存失败的消息，使前端可以展示失败状态
            # skip_save=True 时由调用方处理失败态，跳过 run_stream 内部重复保存
            if not sub_agent and not skip_save:
                try:
                    conv_uuid = uuid.UUID(conversation_id)
                    parent_message_id = await self._get_last_current_assistant_id_async(conv_uuid)
                    failed_user_message_id = uuid.uuid4()
                    await self._add_message_async(
                        conversation_id=conv_uuid,
                        role="user",
                        content=message,
                        meta_data={"files": [], "history_files": {}},
                        message_id=failed_user_message_id,
                        parent_message_id=parent_message_id,
                    )
                    await self._add_message_async(
                        conversation_id=conv_uuid,
                        role="assistant",
                        content="",
                        meta_data={"error": json.dumps(compact_error, ensure_ascii=False)[:2000]},
                        status="failed",
                        parent_message_id=failed_user_message_id,
                    )
                except Exception:
                    pass
            # 更新 Agent 执行记录为 failed
            if not sub_agent and not skip_save:
                try:
                    elapsed_time = time.time() - start_time
                    await self._update_agent_execution_completed_async(
                        execution_id=_agent_execution_id,
                        steps=node_executions if 'node_executions' in dir() else [],
                        status="failed",
                        elapsed_time=elapsed_time,
                        error_message=json.dumps(compact_error, ensure_ascii=False)[:2000],
                    )
                except Exception:
                    pass
            # 发送错误事件
            yield self._format_sse_event("error", {
                "error": compact_error,
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

    # ──────────────────────────────────────────────────────────────
    # Sandbox Execution Helpers
    # ──────────────────────────────────────────────────────────────

    async def _build_sandbox_payload(
        self,
        *,
        agent_config: AgentConfig,
        model_config: ModelConfig,
        api_key_config: dict,
        effective_params: dict,
        message: str,
        system_prompt: str,
        workspace_id: uuid.UUID,
        user_id: Optional[str],
        conversation_id: Optional[str],
        tools: list,
        history: list | None,
        context: str | None,
        variables: dict | None,
        files_config: dict | None,
    ) -> dict:
        """Build a rich snapshot payload for E2B sandbox execution.

        Serializes the already-loaded tool instances directly from ``tools``
        so names / descriptions / parameters match the in-process path exactly.
        """
        serialized_tools = self._serialize_tools_for_sandbox(tools=tools)

        sandbox_agent_config = {
            "system_prompt": system_prompt,
            "tools": serialized_tools,
            "max_iterations": getattr(agent_config, "max_iterations", None),
            "strategy": getattr(agent_config, "strategy", "react"),
            "tool_call_limit": getattr(agent_config, "tool_call_limit", 1),
        }

        sandbox_model_config = {
            "model_name": api_key_config.get("model_name", ""),
            "api_key": api_key_config.get("api_key", ""),
            "api_base": api_key_config.get("api_base", ""),
            "provider": api_key_config.get("provider", "openai"),
            "temperature": effective_params.get("temperature", 0.7),
            "max_tokens": effective_params.get("max_tokens", 2000),
            "top_p": effective_params.get("top_p"),
            "top_k": effective_params.get("top_k"),
            "seed": effective_params.get("seed"),
            "stop": effective_params.get("stop"),
            "repetition_penalty": effective_params.get("repetition_penalty"),
            "frequency_penalty": effective_params.get("frequency_penalty"),
            "presence_penalty": effective_params.get("presence_penalty"),
            "deep_thinking": effective_params.get("deep_thinking", False),
            "thinking_budget_tokens": effective_params.get("thinking_budget_tokens"),
            "json_output": effective_params.get("json_output", False),
            "enable_search": effective_params.get("enable_search", False),
            "is_omni": api_key_config.get("is_omni", False),
            "capability": api_key_config.get("capability") or [],
            "extra_headers": getattr(model_config, "extra_headers", None),
            "concurrency": getattr(model_config, "concurrency", 5),
        }

        sandbox_context = {
            "history": history or [],
            "knowledge": context or "",
            "variables": variables or {},
        }

        return {
            "type": "agent_stream",
            "agent_config": sandbox_agent_config,
            "model_config": sandbox_model_config,
            "message": message,
            "context": sandbox_context,
            "runtime_env": {
                "callback_url": settings.E2B_CALLBACK_URL,
                "callback_secret": settings.E2B_CALLBACK_SECRET,
                "workspace_id": str(workspace_id),
                "user_id": user_id or "",
                "execution_id": str(uuid.uuid4()),
                "conversation_id": str(conversation_id or ""),
            },
        }

    @staticmethod
    def _serialize_tools_for_sandbox(*, tools: list) -> list[dict]:
        """Serialize already-loaded tool instances for sandbox consumption.

        Iterates the in-process tool list and produces a flat list of tool
        descriptors that the sandbox's tool loader (runtime.tools.loader)
        understands.  Uses the same names / descriptions / parameters as the
        in-process path, so tool_start events and LLM behaviour are identical.
        """
        from app.core.tools.langchain_adapter import LangchainToolWrapper

        serialized: list[dict] = []

        for tool in tools:
            meta = getattr(tool, "_tool_meta", None) or {}
            meta_type = meta.get("tool_type", "")

            # ── Knowledge retrieval ──────────────────────────
            if meta_type == "knowledge_retrieval":
                sources = meta.get("sources", [])
                kb_ids = [s["id"] for s in sources if s.get("id")]
                serialized.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "type": "knowledge_retrieval",
                    "tool_id": None,
                    "kb_ids": kb_ids,
                    "config": {
                        "kb_ids": kb_ids,
                        "top_k": getattr(tool, "top_k", 3),
                        "score_threshold": getattr(tool, "score_threshold", 0.7),
                    },
                })
                continue

            # ── Memory (long-term) ────────────────────────────
            if meta_type == "long_term_memory":
                sources = meta.get("sources", [])
                config_id = sources[0]["id"] if sources else None
                serialized.append({
                    "name": "memory_read",
                    "description": "Read user's long-term memories",
                    "type": "memory_read",
                    "tool_id": None,
                    "config": {"config_id": config_id},
                })
                serialized.append({
                    "name": "memory_write",
                    "description": "Save information to user's long-term memory",
                    "type": "memory_write",
                    "tool_id": None,
                    "config": {"config_id": config_id},
                })
                continue

            # ── Web search (via callback, same as in-process Search()) ─
            if meta_type == "web_search":
                serialized.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "type": "builtin",
                    "tool_id": None,
                    "config": {},
                })
                continue

            # ── Skill ─────────────────────────────────────────
            if meta_type == "skill":
                sources = meta.get("sources", [])
                skill_id = sources[0]["id"] if sources else None
                serialized.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "type": "skill",
                    "tool_id": skill_id,
                    "config": {
                        "skill_id": skill_id,
                    },
                })
                continue

            # ── Builtin / Custom / MCP (LangchainToolWrapper) ─
            if isinstance(tool, LangchainToolWrapper):
                ti = tool.tool_instance
                tool_type = ti.tool_type.value if hasattr(ti.tool_type, "value") else str(ti.tool_type)
                config_data = dict(ti.config) if hasattr(ti, "config") and ti.config else {}

                # Include parameters so sandbox can rebuild the args_schema
                if hasattr(ti, "parameters") and ti.parameters:
                    props = {}
                    required: list[str] = []
                    for p in ti.parameters:
                        if hasattr(p, "model_dump"):
                            pd = p.model_dump()
                        elif isinstance(p, dict):
                            pd = p
                        else:
                            continue
                        pname = pd.get("name", "")
                        if not pname:
                            continue
                        props[pname] = {
                            "type": pd.get("type", "string"),
                            "description": pd.get("description", ""),
                        }
                        if pd.get("required"):
                            required.append(pname)
                        if pd.get("default") is not None:
                            props[pname]["default"] = pd.get("default")
                        if pd.get("enum"):
                            props[pname]["enum"] = pd.get("enum")
                    config_data["parameters"] = {"properties": props, "required": required}

                # Operation may be on the wrapper (custom tools) or baked into
                # the inner instance (builtin tools via OperationTool).
                operation = tool.operation
                if operation is None and hasattr(ti, "operation"):
                    operation = ti.operation

                serialized.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "type": tool_type,
                    "tool_id": ti.tool_id if hasattr(ti, "tool_id") else None,
                    "config": config_data,
                    "operation": operation,
                })
                continue

            # ── Fallback (plain LangChain tool) ───────────────
            serialized.append({
                "name": tool.name,
                "description": tool.description or "",
                "type": meta_type or "builtin",
                "tool_id": None,
                "config": {},
            })

        return serialized

    async def _sandbox_event_stream(
        self,
        *,
        payload: dict,
        workspace_id: str,
        user_id: str,
        conversation_id: str,
        adapter: Any = None,
    ) -> AsyncGenerator[Any, None]:
        """Stream execution events from E2B sandbox.

        Translates sandbox protocol events into chunks compatible with
        agent.chat_stream() output format (str|int|dict).
        """
        from app.services.e2b_sandbox_service import get_sandbox_service

        sandbox_service = get_sandbox_service()

        async for event in sandbox_service.run_agent(
            agent_config=payload["agent_config"],
            model_config=payload["model_config"],
            message=payload["message"],
            context=payload["context"],
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
            execution_id=payload["runtime_env"]["execution_id"],
        ):
            if adapter is not None:
                chunk = adapter._translate_event_to_chunk(event)
            else:
                chunk = event
            if chunk is not None:
                yield chunk

    async def _get_api_key(self, model_config_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> Dict:
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
        async with get_async_db_context() as db:
            api_key = await ModelApiKeyService.get_available_api_key_async(
                db,
                model_config_id,
                tenant_id=tenant_id,
            )

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
            sub_agent: bool = False,
            opening_statement: Optional[str] = None,
            suggested_questions: Optional[List[str]] = None
    ) -> str:
        """确保会话存在（创建或验证）

        Args:
            conversation_id: 会话ID（可选）
            app_id: 应用ID
            workspace_id: 工作空间ID（必须）
            user_id: 用户ID
            sub_agent: 是否为子代理
            opening_statement: 开场白（新会话时作为第一条消息写入）
            suggested_questions: 预设问题列表

        Returns:
            str: 会话ID

        Raises:
            BusinessException: 当指定的会话不存在时
        """
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
            async with get_async_db_context() as db:
                new_conversation = Conversation(
                    id=uuid.UUID(new_conv_id),
                    app_id=app_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    is_draft=True,
                    title="草稿会话",
                    config_snapshot=config_snapshot
                )
                db.add(new_conversation)
                await db.commit()

            # 如果有开场白，作为第一条 assistant 消息写入数据库
            if opening_statement:
                await self._add_message_async(
                    conversation_id=uuid.UUID(new_conv_id),
                    role="assistant",
                    content=opening_statement,
                    meta_data={"suggested_questions": suggested_questions}
                )
                logger.debug(f"已保存开场白到会话 {new_conv_id}")

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
            async with get_async_db_context() as db:
                conversation = await db.get(Conversation, conv_uuid)
                if not conversation:
                    raise BusinessException(
                        f"会话不存在: {conversation_id}",
                        BizCode.NOT_FOUND,
                    )

                # 验证会话属于当前工作空间（或属于共享应用的源工作空间）
                # sub_agent 内部调用时跳过校验，已在上层验证过
                if not sub_agent and conversation.workspace_id != workspace_id:
                    share = (
                        await db.execute(
                            select(AppShare.id).where(
                                AppShare.source_app_id == app_id,
                                AppShare.target_workspace_id == workspace_id,
                                AppShare.is_active.is_(True),
                            ).limit(1)
                        )
                    ).scalar_one_or_none()

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
            max_history: int = 10,
            current_provider: Optional[str] = None,
            current_is_omni: Optional[bool] = None
    ) -> List[Dict[str, str]]:
        """加载会话历史消息，并根据当前模型配置处理多模态文件

        Args:
            conversation_id: 会话ID
            max_history: 最大历史消息数量
            current_provider: 当前模型的provider
            current_is_omni: 当前模型的is_omni

        Returns:
            List[Dict]: 历史消息列表
        """
        try:

            async with get_async_db_context() as db:
                stmt = select(Message).where(
                    Message.conversation_id == uuid.UUID(conversation_id),
                    Message.is_deleted.is_not(True),
                    Message.is_current.is_not(False),
                ).order_by(Message.created_at)
                if max_history:
                    stmt = stmt.limit(max_history)
                result = await db.execute(stmt)
                # Do not carry ORM Message instances outside this async
                # session: attributes may expire after the context exits.
                messages = [
                    {
                        "role": item.role,
                        "content": item.content,
                        "meta_data": dict(item.meta_data or {}),
                    }
                    for item in result.scalars().all()
                ]

            history = []
            for msg in messages:
                history_files = msg["meta_data"].get("history_files", {})

                has_files = bool(history_files and current_provider and current_is_omni is not None)
                if has_files:
                    stored_provider = history_files.get("provider")
                    stored_is_omni = history_files.get("is_omni")

                    if stored_provider != current_provider or stored_is_omni != current_is_omni:
                        continue

                    content = [{"type": "text", "text": msg["content"]}]
                    content.extend(history_files.get("content", []))
                else:
                    content = msg["content"]

                history.append({
                    "role": msg["role"],
                    "content": content
                })

            logger.info(
                "[会话历史] 加载完成 | "
                f"会话={conversation_id} | 请求上限={max_history} | 实际加载={len(history)}"
            )

            return history

        except Exception as e:
            logger.warning(
                "[会话历史] 加载失败 | "
                f"会话={conversation_id} | 异常={type(e).__name__}: {e}"
            )
            return []

    async def _load_history_before_message(
            self,
            conversation_id: uuid.UUID,
            before_time: datetime.datetime,
            max_history: int = 10
    ) -> List[Dict[str, Any]]:
        """加载指定时间之前的历史消息（用于重新生成场景）

        Args:
            conversation_id: 会话ID
            before_time: 截止时间（不包含该时间的消息）
            max_history: 最大历史消息数量

        Returns:
            List[Dict]: 历史消息列表，格式为 [{"role": "user/assistant", "content": [...]}]
        """
        # 查询指定时间之前的消息
        async with get_async_db_context() as db:
            result = await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.created_at < before_time,  # 只取截止时间之前的消息
                    Message.is_deleted.is_not(True),
                )
                .order_by(Message.created_at.asc())  # 正序排列
                .limit(max_history)
            )
            # Snapshot values before leaving the session: the context manager rolls
            # back read-only transactions, which expires ORM instances on exit.
            history_msgs = [
                _snapshot_message(message) for message in result.scalars().all()
            ]

        # 转换为 history 格式
        filtered_history = []
        for msg in history_msgs:
            msg_dict = {
                "role": msg.role,
                "content": [{"type": "text", "text": msg.content}]
            }
            # 处理用户消息中的多模态文件
            if msg.role == "user" and msg.meta_data:
                history_files = msg.meta_data.get("history_files", {})
                if history_files and history_files.get("content"):
                    msg_dict["content"].extend(history_files.get("content"))
            filtered_history.append(msg_dict)

        logger.debug(
            "加载指定时间前的历史消息",
            extra={
                "conversation_id": str(conversation_id),
                "before_time": to_iso_z(before_time),
                "max_history": max_history,
                "loaded_count": len(filtered_history)
            }
        )

        return filtered_history

    async def _save_conversation_message(
            self,
            conversation_id: str,
            user_message: str,
            assistant_message: str,
            meta_data: dict,
            app_id: Optional[uuid.UUID] = None,
            user_id: Optional[str] = None,
            files: Optional[List[FileInput]] = None,
            processed_files: Optional[List[Dict[str, Any]]] = None,
            audio_url: Optional[str] = None,
            citations: Optional[List[Any]] = None,
            provider: Optional[str] = None,
            is_omni: Optional[bool] = None,
            message_id: Optional[uuid.UUID] = None,
            user_message_id: Optional[uuid.UUID] = None
    ) -> Optional[str]:
        """保存会话消息（会话已通过 _ensure_conversation 确保存在）

        Args:
            conversation_id: 会话ID
            user_message: 用户消息
            assistant_message: AI 回复消息
            app_id: 应用ID（未使用，保留用于兼容性）
            user_id: 用户ID（未使用，保留用于兼容性）
            meta_data: token消耗
            files: 原始文件输入
            processed_files: 处理后的文件
            audio_url: 音频URL
            citations: 引用来源列表
            provider: 模型供应商
            is_omni: 是否为全模态模型

        Returns:
            Optional[str]: 助手消息ID
        """
        _ = (app_id, user_id)
        try:
            conv_uuid = uuid.UUID(conversation_id)

            # 保存消息（会话已经存在）
            human_meta = {
                "files": [],
                "history_files": {}
            }
            if files:
                local_ids = [f.upload_file_id for f in files
                             if f.transfer_method.value == "local_file" and f.upload_file_id
                             and (not f.name or not f.size)]
                meta_map = {}
                async with get_async_db_context() as db:
                    if local_ids:
                        result = await db.execute(
                            select(FileMetadata).where(
                                FileMetadata.id.in_(local_ids),
                                FileMetadata.status == "completed"
                            )
                        )
                        rows = result.scalars().all()
                        meta_map = {str(r.id): r for r in rows}
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
                        "file_type": f.file_type,
                        "name": name,
                        "size": size
                    })

            # 保存 history_files，包含 provider 和 is_omni 信息
            if processed_files:
                human_meta["history_files"] = {
                    "content": processed_files,
                    "provider": provider,
                    "is_omni": is_omni
                }

            parent_message_id = await self._get_last_current_assistant_id_async(conv_uuid)
            # 保存助手消息（含 audio_url 和 citations）
            if audio_url:
                meta_data["audio_url"] = audio_url
            if citations:
                meta_data["citations"] = citations
            async with get_async_db_context() as db:
                conversation = await db.get(Conversation, conv_uuid)
                if not conversation:
                    return None

                user_msg = Message(
                    id=user_message_id if user_message_id else uuid.uuid4(),
                    conversation_id=conv_uuid,
                    role="user",
                    content=user_message,
                    meta_data=human_meta,
                    parent_message_id=parent_message_id,
                    status="completed",
                )
                assistant_msg = Message(
                    id=message_id if message_id else uuid.uuid4(),
                    conversation_id=conv_uuid,
                    role="assistant",
                    content=assistant_message,
                    meta_data=meta_data,
                    parent_message_id=user_msg.id,
                    status="completed",
                )
                db.add(user_msg)
                db.add(assistant_msg)

                message_count = int(conversation.message_count or 0)
                if message_count + 1 <= 2:
                    conversation.title = user_message[:50] + ("..." if len(user_message) > 50 else "")
                conversation.message_count = message_count + 2

                await db.commit()

            logger.debug(
                "保存会话消息",
                extra={
                    "conversation_id": conversation_id,
                    "user_message_length": len(user_message),
                    "assistant_message_length": len(assistant_message),
                    "user_msg_id": str(user_msg.id),
                    "assistant_msg_id": str(assistant_msg.id),
                }
            )

            return str(assistant_msg.id)

        except Exception as e:
            logger.warning("保存会话消息失败", extra={"error": str(e)})
            return None

    async def _get_config_snapshot(self, app_id: uuid.UUID) -> Dict[str, Any]:
        """获取当前配置快照

        Args:
            app_id: 应用ID

        Returns:
            Dict: 配置快照
        """
        try:
            async with get_async_db_context() as db:
                stmt = select(AgentConfig).where(AgentConfig.app_id == app_id).limit(1)
                result = await db.execute(stmt)
                agent_cfg = result.scalar_one_or_none()

                if not agent_cfg:
                    return {}

                # 获取模型配置
                model_config = None
                if agent_cfg.default_model_config_id:
                    model_config = await db.get(ModelConfig, agent_cfg.default_model_config_id)

                # 构建快照（确保所有值都可序列化，在 session 关闭前读取所有属性）
                def safe_serialize(value):
                    if value is None:
                        return None
                    if isinstance(value, (str, int, float, bool)):
                        return value
                    if isinstance(value, (dict, list)):
                        return value
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
                    "snapshot_time": to_iso_z(utcnow_naive())
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
        _ = effective_params
        sq_config = features_config.get("suggested_questions_after_answer", {})
        if not isinstance(sq_config, dict) or not sq_config.get("enabled"):
            return []
        try:
            from langchain_core.messages import HumanMessage
            from app.core.models import RedBearLLM, RedBearModelConfig
            llm = RedBearLLM(
                RedBearModelConfig(
                    model_name=api_key_config["model_name"],
                    provider=api_key_config.get("provider", "openai"),
                    api_key=api_key_config["api_key"],
                    base_url=api_key_config.get("api_base"),
                    capability=api_key_config.get("capability", []),
                    is_omni=api_key_config.get("is_omni", False),
                    extra_params={"temperature": 0.5, "max_tokens": 200}
                ),
                type=ModelType.CHAT
            )
            prompt = (
                f"根据以下AI回复，生成3个用户可能继续追问的简短问题，每行一个，不加序号：\n\n{assistant_message}"
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            content = resp.content
            # 兼容 content 为 list 的情况（部分模型返回结构化内容）
            if isinstance(content, list):
                content = "".join(str(c) if isinstance(c, str) else c.get("text", "") for c in content)
            lines = [l.strip() for l in content.strip().split("\n") if l.strip()]
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

        from app.services.file_storage_service import FileStorageService, generate_file_key

        provider = api_key_config.get("provider", "openai")
        api_key = api_key_config.get("api_key")
        api_base = api_key_config.get("api_base")
        voice = tts_config.get("voice")
        file_ext, content_type = ".mp3", "audio/mpeg"

        is_dashscope = provider == "dashscope" or (
            isinstance(api_base, str) and "dashscope.aliyuncs.com" in api_base
        )

        file_id = uuid.uuid4()
        file_key = generate_file_key(tenant_id, workspace_id, file_id, file_ext)

        # 先写入 pending 状态的元数据，立即返回 URL
        await self._create_tts_file_metadata_async(
            file_id=file_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            file_key=file_key,
            file_name=f"tts_{file_id}{file_ext}",
            file_ext=file_ext,
            content_type=content_type,
        )

        server_url = settings.FILE_LOCAL_SERVER_URL
        audio_url = f"{server_url}/storage/permanent/{file_id}"

        # 后台任务：流式生成并写入存储，完成后更新状态
        async def _stream_to_storage():
            try:
                storage_service = FileStorageService()
                if is_dashscope:
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

                await self._update_tts_file_metadata_async(
                    file_id=file_id,
                    status="completed",
                    file_size=total_size,
                )
                logger.debug(f"TTS 流式写入完成，provider={provider}, file_key={file_key}")
            except Exception as e:
                logger.warning(f"TTS 流式写入失败: {e}")
                await self._update_tts_file_metadata_async(
                    file_id=file_id,
                    status="failed",
                )

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
        返回 (audio_url, task)，audio_url 立即可用（pending状态），task 完成后文件内容就绪。
        调用方向 text_queue put 文本 chunk，结束时 put None。
        前端可通过 GET /storage/files/{file_id}/status 轮询检查音频是否就绪。
        """
        tts_config = features_config.get("text_to_speech", {})
        if not isinstance(tts_config, dict) or not tts_config.get("enabled"):
            return None, None

        from app.services.file_storage_service import FileStorageService, generate_file_key

        provider = api_key_config.get("provider", "openai")
        api_key = api_key_config.get("api_key")
        api_base = api_key_config.get("api_base")
        voice = tts_config.get("voice")
        file_ext, content_type = ".mp3", "audio/mpeg"

        is_dashscope = provider == "dashscope" or (
            isinstance(api_base, str) and "dashscope.aliyuncs.com" in api_base
        )

        file_id = uuid.uuid4()
        file_key = generate_file_key(tenant_id, workspace_id, file_id, file_ext)

        await self._create_tts_file_metadata_async(
            file_id=file_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            file_key=file_key,
            file_name=f"tts_{file_id}{file_ext}",
            file_ext=file_ext,
            content_type=content_type,
        )

        server_url = settings.FILE_LOCAL_SERVER_URL
        audio_url = f"{server_url}/storage/permanent/{file_id}"

        async def _run():
            try:
                storage_service = FileStorageService()
                if is_dashscope:
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
                await self._update_tts_file_metadata_async(
                    file_id=file_id,
                    status="completed",
                    file_size=total_size,
                )
                logger.debug(f"TTS 流式合成完成，provider={provider}, file_key={file_key}")
            except Exception as e:
                logger.warning(f"TTS 流式合成失败: {e}")
                await self._update_tts_file_metadata_async(
                    file_id=file_id,
                    status="failed",
                )

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
            sentence_end = re.compile(r'[\u3002\uff01\uff1f.!?\n]')
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
            files: list[FileInput] | None = None,
            source: str = "",
            execution_mode: Literal["in_process", "sandbox"] = "in_process",
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
            execution_mode: 执行模式 (in_process / sandbox)

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
                            files=files,
                            source=source,
                            execution_mode=execution_mode,
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
                    "reasoning_content": result.get("reasoning_content"),
                    "usage": usage,
                    "elapsed_time": elapsed,
                    "tokens_per_second": (
                        usage.get("completion_tokens", 0) / elapsed
                        if elapsed > 0 and usage.get("completion_tokens") else None
                    ),
                    "cost_estimate": self._estimate_cost(usage, model_info["model_config"]),
                    "audio_url": result.get("audio_url"),
                    "audio_status": result.get("audio_status"),
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
                "audio_status": r.get("audio_status"),
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

        _ = model_config

        # 简化成本估算：暂时返回 None
        # TODO: 实现基于模型名称或配置的成本估算
        # 需要从 ModelApiKey 获取实际的模型名称，或者在 ModelConfig 中添加 model 字段
        return None

    def _with_parameters(self, agent_config: AgentConfig, parameters: Dict[str, Any]) -> tuple[AgentConfig, Any]:
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
            files: list[FileInput] | None = None,
            source: str = "",
            execution_mode: Literal["in_process", "sandbox"] = "in_process",
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
            execution_mode: 执行模式 (in_process / sandbox)

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

        # compare_start 的 user_message_id 仅作为本次对比的整体用户消息标识，
        # 供前端为本地 user 消息气泡定位/删除；每个模型各自落库的 user_message_id
        # 在 model_start 事件中返回，便于前端区分各对比模型
        user_message_id = uuid.uuid4()
        # 发送开始事件
        yield self._format_sse_event("compare_start", {
            "conversation_id": conversation_id,
            "model_count": len(models),
            "parallel": parallel,
            "user_message_id": str(user_message_id),
            "timestamp": time.time()
        })

        results = []

        async def run_single_model_stream(idx: int, model_info: Dict[str, Any], event_queue: asyncio.Queue):
            """运行单个模型（流式）并将事件放入队列"""
            model_label = model_info["label"]
            model_config_id = str(model_info["model_config_id"])
            # 使用模型自己的 conversation_id，如果没有则使用全局的
            model_conversation_id = model_info.get("conversation_id") or conversation_id
            # 每个模型预生成各自的 user_message_id，随 model_start 返回前端，
            # 便于前端在多模型对比中区分各模型对应的用户消息；
            # 同时下传 run_stream 作为该模型用户消息的落库 id，保证前后端一致
            model_user_message_id = uuid.uuid4()

            try:
                # 发送模型开始事件
                await event_queue.put(self._format_sse_event("model_start", {
                    "model_index": idx,
                    "model_config_id": model_config_id,
                    "model_name": model_info["model_config"].name,
                    "label": model_label,
                    "conversation_id": model_conversation_id,
                    "user_message_id": str(model_user_message_id),
                    "timestamp": time.time()
                }))

                start_time = time.time()
                full_content = ""
                full_reasoning = ""
                returned_conversation_id = model_conversation_id
                audio_url = None
                audio_status = None
                citations = []
                suggested_questions = []
                stream_error = None
                message_id = None

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
                            files=files,
                            source=source,
                            user_message_id=model_user_message_id,
                            execution_mode=execution_mode,
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

                            # 转发深度思考事件（带模型标识）
                            if event_type == "reasoning" and event_data:
                                reasoning_chunk = event_data.get("content", "")
                                full_reasoning += reasoning_chunk
                                await event_queue.put(self._format_sse_event("model_reasoning", {
                                    "model_index": idx,
                                    "model_config_id": model_config_id,
                                    "label": model_label,
                                    "conversation_id": returned_conversation_id,
                                    "content": event_data.get("content", "")
                                }))

                            # 转发工具调用事件（带模型标识）
                            if event_type == "tool_start" and event_data:
                                await event_queue.put(self._format_sse_event("model_tool_start", {
                                    "model_index": idx,
                                    "model_config_id": model_config_id,
                                    "step_id": event_data.get("step_id"),
                                    "name": event_data.get("name", ""),
                                    "input": event_data.get("input"),
                                }))

                            if event_type == "tool_end" and event_data:
                                await event_queue.put(self._format_sse_event("model_tool_end", {
                                    "model_index": idx,
                                    "model_config_id": model_config_id,
                                    "step_id": event_data.get("step_id"),
                                    "name": event_data.get("name", ""),
                                    "output": event_data.get("output"),
                                    "meta": event_data.get("meta"),
                                }))

                            if event_type == "tool_error" and event_data:
                                await event_queue.put(self._format_sse_event("model_tool_error", {
                                    "model_index": idx,
                                    "model_config_id": model_config_id,
                                    "step_id": event_data.get("step_id"),
                                    "name": event_data.get("name", ""),
                                    "error": event_data.get("error"),
                                }))

                            # 从 end 事件中提取 features 输出字段
                            if event_type == "end" and event_data:
                                audio_url = event_data.get("audio_url")
                                audio_status = event_data.get("audio_status")
                                citations = event_data.get("citations", [])
                                suggested_questions = event_data.get("suggested_questions", [])
                                message_id = event_data.get("message_id")

                            if event_type == "error" and event_data:
                                stream_error = event_data.get("error") or {"message": "未知错误"}
                                await event_queue.put(self._format_sse_event(
                                    "model_error",
                                    self._build_model_error_event_data(
                                        model_index=idx,
                                        model_config_id=model_config_id,
                                        label=model_label,
                                        conversation_id=returned_conversation_id,
                                        error=stream_error,
                                        timestamp=event_data.get("timestamp")
                                    )
                                ))
                        except Exception as e:
                            logger.warning(f"解析流式事件失败: {e}")
                finally:
                    # 恢复原始参数
                    agent_config.model_parameters = original_params

                elapsed = time.time() - start_time

                if stream_error:
                    await event_queue.put(self._format_sse_event(
                        "model_end",
                        self._build_model_end_event_data(
                            model_index=idx,
                            model_config_id=model_config_id,
                            label=model_label,
                            conversation_id=returned_conversation_id,
                            elapsed_time=elapsed,
                            message_length=len(full_content),
                            audio_url=audio_url,
                            audio_status=audio_status,
                            citations=citations,
                            suggested_questions=suggested_questions,
                            status="failed",
                            error=stream_error,
                            message_id=message_id
                        )
                    ))
                    return {
                        "model_config_id": model_info["model_config_id"],
                        "model_name": model_info["model_config"].name,
                        "label": model_label,
                        "conversation_id": returned_conversation_id,
                        "parameters_used": model_info["parameters"],
                        "message": full_content,
                        "reasoning_content": full_reasoning or None,
                        "elapsed_time": elapsed,
                        "audio_url": audio_url,
                        "audio_status": audio_status,
                        "citations": citations,
                        "suggested_questions": suggested_questions,
                        "error": stream_error
                    }

                # 构建结果（参考 run_compare）
                result = {
                    "model_config_id": model_info["model_config_id"],
                    "model_name": model_info["model_config"].name,
                    "label": model_label,
                    "conversation_id": returned_conversation_id,
                    "parameters_used": model_info["parameters"],
                    "message": full_content,
                    "reasoning_content": full_reasoning or None,
                    "elapsed_time": elapsed,
                    "audio_url": audio_url,
                    "audio_status": audio_status,
                    "citations": citations,
                    "suggested_questions": suggested_questions,
                    "error": None
                }

                # 发送模型完成事件
                await event_queue.put(self._format_sse_event(
                    "model_end",
                    self._build_model_end_event_data(
                        model_index=idx,
                        model_config_id=model_config_id,
                        label=model_label,
                        conversation_id=returned_conversation_id,
                        elapsed_time=elapsed,
                        message_length=len(full_content),
                        audio_url=audio_url,
                        audio_status=audio_status,
                        citations=citations,
                        suggested_questions=suggested_questions,
                        message_id=message_id
                    )
                ))

                return result

            except TimeoutError:
                logger.warning(f"模型运行超时: {model_label}")
                compact_error = {
                    "message": f"执行超时（{timeout}秒）",
                    "type": "TimeoutError",
                    "debug_id": self._build_debug_id(),
                }
                result = {
                    "model_config_id": model_info["model_config_id"],
                    "model_name": model_info["model_config"].name,
                    "label": model_label,
                    "conversation_id": model_conversation_id,
                    "parameters_used": model_info["parameters"],
                    "elapsed_time": timeout,
                    "error": compact_error
                }

                await event_queue.put(self._format_sse_event(
                    "model_error",
                    self._build_model_error_event_data(
                        model_index=idx,
                        model_config_id=model_config_id,
                        label=model_label,
                        conversation_id=model_conversation_id,
                        error=compact_error
                    )
                ))
                await event_queue.put(self._format_sse_event(
                    "model_end",
                    self._build_model_end_event_data(
                        model_index=idx,
                        model_config_id=model_config_id,
                        label=model_label,
                        conversation_id=model_conversation_id,
                        elapsed_time=timeout,
                        status="failed",
                        error=compact_error
                    )
                ))

                return result

            except Exception as e:
                debug_id = self._build_debug_id()
                compact_error = self._build_compact_error(e, debug_id=debug_id)
                logger.error(
                    f"模型运行失败: {model_label}, error: {e}",
                    extra={"debug_id": debug_id, "compact_error": compact_error}
                )
                result = {
                    "model_config_id": model_info["model_config_id"],
                    "model_name": model_info["model_config"].name,
                    "label": model_label,
                    "conversation_id": model_conversation_id,
                    "parameters_used": model_info["parameters"],
                    "elapsed_time": 0,
                    "error": compact_error
                }

                await event_queue.put(self._format_sse_event(
                    "model_error",
                    self._build_model_error_event_data(
                        model_index=idx,
                        model_config_id=model_config_id,
                        label=model_label,
                        conversation_id=model_conversation_id,
                        error=compact_error
                    )
                ))
                await event_queue.put(self._format_sse_event(
                    "model_end",
                    self._build_model_end_event_data(
                        model_index=idx,
                        model_config_id=model_config_id,
                        label=model_label,
                        conversation_id=model_conversation_id,
                        elapsed_time=0,
                        status="failed",
                        error=compact_error
                    )
                ))

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
                "reasoning_content": r.get("reasoning_content"),
                "elapsed_time": r.get("elapsed_time", 0),
                "audio_url": r.get("audio_url"),
                "audio_status": r.get("audio_status"),
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

    # ==================== 重新生成功能 ====================

    async def _locate_or_restore_parent_user_message(
            self, original_msg: SimpleNamespace
    ) -> SimpleNamespace:
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
        parent_msg = None
        if original_msg.parent_message_id:
            candidate = await self._get_message_async(original_msg.parent_message_id)
            if candidate and candidate.role == "user":
                parent_msg = candidate
        if not parent_msg:
            async with get_async_db_context() as db:
                result = await db.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == original_msg.conversation_id,
                        Message.role == "user",
                        Message.created_at <= original_msg.created_at,
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
                parent_record = result.scalar_one_or_none()
                parent_msg = _snapshot_message(parent_record) if parent_record else None
        if not parent_msg:
            # 兜底：同会话内最近一条 user 消息（不论时间顺序），覆盖 created_at 异常的脏数据
            async with get_async_db_context() as db:
                result = await db.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == original_msg.conversation_id,
                        Message.role == "user",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
                parent_record = result.scalar_one_or_none()
                parent_msg = _snapshot_message(parent_record) if parent_record else None
        if not parent_msg:
            raise BusinessException("无法找到原始用户消息", BizCode.NOT_FOUND)
        restored_deleted = False
        async with get_async_db_context() as db:
            original_record = await db.get(Message, original_msg.id)
            parent_record = await db.get(Message, parent_msg.id)
            if not original_record or not parent_record:
                raise BusinessException("无法找到原始用户消息", BizCode.NOT_FOUND)
            if original_record.parent_message_id != parent_record.id:
                original_record.parent_message_id = parent_record.id
            if parent_record.is_deleted:
                parent_record.is_deleted = False
                restored_deleted = True
            await db.commit()
            await db.refresh(parent_record)
            parent_msg = _snapshot_message(parent_record)
        original_msg.parent_message_id = parent_msg.id
        if restored_deleted:
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
            *,
            message_id: uuid.UUID,
            agent_config: AgentConfig,
            model_config: ModelConfig,
            workspace_id: uuid.UUID,
            user_id: str,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """重新生成回复（多版本支持）

        核心逻辑：
        1. 获取原 assistant 消息及其父 user 消息
        2. 将原 assistant 消息标记为非当前版本 (is_current=False)
        3. 复用相同的上下文重新调用 LLM
        4. 保存新版本 assistant 消息（version+1, is_current=True）

        Args:
            message_id: 原 AI 回复的消息ID
            agent_config: Agent 配置
            model_config: 模型配置
            workspace_id: 工作空间ID
            user_id: 用户ID
            storage_type: 存储类型
            user_rag_memory_id: RAG 记忆ID

        Returns:
            Dict: 包含新消息ID、内容、版本号等
        """
        # 1. 获取原消息
        original_msg = await self._get_message_async(message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)

        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)

        # Keep scalar values before any later await/yield.  The message snapshot is
        # already detached from its short-lived lookup session.
        conversation_uuid = original_msg.conversation_id
        conversation_id = str(conversation_uuid)
        new_version = original_msg.version + 1

        # 2. 将原版本标记为非当前
        await self._mark_message_not_current_async(message_id)
        original_msg.is_current = False

        # 3. 获取父用户消息（用于提取原始问题；若已被逻辑删除则自动恢复，
        # 见 _locate_or_restore_parent_user_message）
        parent_msg = await self._locate_or_restore_parent_user_message(original_msg)
        parent_msg_id = parent_msg.id

        user_message_content = parent_msg.content if parent_msg else ""

        # 3.5 提取父消息中的文件信息（如果有）
        files = None
        if parent_msg and parent_msg.meta_data:
            meta_files = parent_msg.meta_data.get("files", [])
            if meta_files:
                # 将存储的文件信息转换回 FileInput 格式
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
                        continue

        # 4. 加载上下文（到父消息为止，不包含当前要重新生成的消息）
        # 使用封装的方法加载父消息之前的历史
        filtered_history = await self._load_history_before_message(
            conversation_id=conversation_uuid,
            before_time=parent_msg.created_at,
            max_history=settings.AGENT_MAX_HISTORY
        )

        # 5. 调用 LLM（复用现有 run 方法，传入过滤后的历史和文件）
        result = await self.run(
            agent_config=agent_config,
            model_config=model_config,
            message=user_message_content,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            user_id=user_id,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            skip_save=True,  # 跳过保存，由 regenerate 自己保存
            history=filtered_history,  # 传入过滤后的历史
            files=files,  # 传入父消息的文件
        )

        # 6. 保存新版本消息
        new_msg = await self._save_regenerated_message_async(
            conversation_id=conversation_uuid,
            content=result["message"],
            version=new_version,
            parent_message_id=parent_msg_id,
            meta_data={
                "usage": result.get("usage"),
                "reasoning_content": result.get("reasoning_content"),
                "regenerated_from": str(message_id),
                "suggested_questions": result.get("suggested_questions", []),
                "citations": result.get("citations", []),
            },
        )

        logger.info(
            "重新生成回复成功",
            extra={
                "original_message_id": str(message_id),
                "new_message_id": str(new_msg.id),
                "version": new_version,
                "conversation_id": conversation_id,
            }
        )

        return {
            "message_id": str(new_msg.id),
            "message": result["message"],
            "reasoning_content": result.get("reasoning_content"),
            "version": new_version,
            "conversation_id": conversation_id,
            "suggested_questions": result.get("suggested_questions", []),
            "citations": result.get("citations", []),
        }

    async def regenerate_stream(
            self,
            *,
            message_id: uuid.UUID,
            agent_config: AgentConfig,
            model_config: ModelConfig,
            workspace_id: uuid.UUID,
            user_id: str,
            storage_type: Optional[str] = None,
            user_rag_memory_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """重新生成回复（流式输出，多版本支持）

        核心逻辑与 regenerate 相同，但支持流式输出

        Args:
            message_id: 原 AI 回复的消息ID
            agent_config: Agent 配置
            model_config: 模型配置
            workspace_id: 工作空间ID
            user_id: 用户ID
            storage_type: 存储类型
            user_rag_memory_id: RAG 记忆ID

        Yields:
            str: SSE 格式的事件数据
        """
        # 1. 获取原消息
        original_msg = await self._get_message_async(message_id)
        if not original_msg or original_msg.role != "assistant":
            raise BusinessException("只能重新生成 AI 回复", BizCode.BAD_REQUEST)

        if original_msg.is_deleted:
            raise BusinessException("消息已被删除", BizCode.BAD_REQUEST)

        # Cache values needed after the stream starts. Never rely on an ORM
        # instance across its short-lived lookup session or later yield points.
        conversation_uuid = original_msg.conversation_id
        conversation_id = str(conversation_uuid)
        new_version = original_msg.version + 1

        # 2. 将原版本标记为非当前
        await self._mark_message_not_current_async(message_id)
        original_msg.is_current = False

        # 3. 获取父用户消息（用于提取原始问题；若已被逻辑删除则自动恢复，
        # 见 _locate_or_restore_parent_user_message）
        parent_msg = await self._locate_or_restore_parent_user_message(original_msg)
        parent_msg_id = parent_msg.id

        user_message_content = parent_msg.content if parent_msg else ""

        # 3.5 提取父消息中的文件信息
        files = None
        if parent_msg and parent_msg.meta_data:
            meta_files = parent_msg.meta_data.get("files", [])
            if meta_files:
                from app.schemas.app_schema import FileInput, FileType, TransferMethod
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
                        continue

        # 4. 加载上下文
        filtered_history = await self._load_history_before_message(
            conversation_id=conversation_uuid,
            before_time=parent_msg.created_at,
            max_history=settings.AGENT_MAX_HISTORY
        )

        # 5. 流式调用 LLM
        full_content = ""
        full_reasoning = ""
        suggested_questions = []
        citations = []
        audio_url = None
        audio_status = None

        # 发送开始事件
        yield self._format_sse_event("start", {
            "conversation_id": conversation_id,
            "version": new_version,
            "timestamp": time.time()
        })

        # 流式调用 run_stream
        async for event_str in self.run_stream(
                agent_config=agent_config,
                model_config=model_config,
                message=user_message_content,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                user_id=user_id,
                storage_type=storage_type,
                user_rag_memory_id=user_rag_memory_id,
                skip_save=True,
                history=filtered_history,
                files=files,
        ):
            # 解析事件
            lines = event_str.strip().split('\n')
            event_type = None
            event_data = None

            for line in lines:
                if line.startswith('event: '):
                    event_type = line[7:].strip()
                elif line.startswith('data: '):
                    event_data = json.loads(line[6:])

            # 累积内容并转发事件
            if event_type == "message" and event_data:
                full_content += event_data.get("content", "")
                yield event_str
            elif event_type == "reasoning" and event_data:
                full_reasoning += event_data.get("content", "")
                yield event_str
            elif event_type == "tool_start" or event_type == "tool_end" or event_type == "tool_error":
                yield event_str
            elif event_type in ("agent_log", "agent_log_final"):
                # regenerate 接口不向前端返回 agent 执行轨迹事件，直接丢弃
                continue
            elif event_type == "end" and event_data:
                # 从 end 事件中提取 features 输出
                suggested_questions = event_data.get("suggested_questions", [])
                audio_url = event_data.get("audio_url")
                audio_status = event_data.get("audio_status")
                citations = event_data.get("citations", [])

        # 6. 保存新版本消息
        new_msg = await self._save_regenerated_message_async(
            conversation_id=conversation_uuid,
            content=full_content,
            version=new_version,
            parent_message_id=parent_msg_id,
            meta_data={
                "reasoning_content": full_reasoning or None,
                "regenerated_from": str(message_id),
                "suggested_questions": suggested_questions,
                "citations": citations,
                "audio_url": audio_url,
            },
        )

        logger.info(
            "重新生成回复成功（流式）",
            extra={
                "original_message_id": str(message_id),
                "new_message_id": str(new_msg.id),
                "version": new_version,
                "conversation_id": conversation_id,
            }
        )

        # 发送结束事件
        yield self._format_sse_event("end", {
            "message_id": str(new_msg.id),
            "conversation_id": conversation_id,
            "version": new_version,
            "message_length": len(full_content),
            "suggested_questions": suggested_questions,
            "audio_url": audio_url,
            "audio_status": audio_status,
            "citations": citations,
            "timestamp": time.time()
        })

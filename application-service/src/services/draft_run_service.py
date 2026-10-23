"""
Draft-run service.

Provides Agent draft-run functionality, allowing users to test configuration
without publishing the application.
"""
import logging

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

from ..utils.datetime_utils import to_iso_z, utcnow_naive
from ..workflow.node_cache import sanitize_json_text
from ..agent.agent_middleware import AgentMiddleware
from ..agent.langchain_agent import LangChainAgent
from ..config import current_settings
from ..errors import BizCode
from ..errors import BusinessException
from ..llm_error_handler import classify_multimodal_exception
from ..model_clients.embedding import RedBearEmbeddings
from ..usage_context import bind_usage
from ..integrations.knowledge.context_factory import build_app_knowledge_context
from ..integrations.knowledge.contracts import KnowledgeRetrievalSource
from ..integrations.knowledge.runtime import get_knowledge_retriever
from ..schemas.chunk_schema import RetrieveType
from ..schemas.knowledge_retrieval_schema import KnowledgeRetrievalRequest
from ..db import get_async_db_context
from ..models import App, AgentConfig, ModelConfig, Message, Conversation, Knowledge
from ..models.agent_execution_model import AgentExecution
from ..models.annotation_model import AppAnnotation, AppAnnotationHitLog, AppAnnotationSetting
from ..models.appshare_model import AppShare
from ..models.file_metadata_model import FileMetadata
from ..models.knowledgeshare_model import KnowledgeShare
from ..models.models_model import Modality, ModelFeature, ModelType
from ..repositories.tool_repository import ToolRepository
from ..schemas.app_schema import FileInput, Citation, FileType, TransferMethod
from ..schemas.model_schema import ModelInfo
from ..schemas.prompt_schema import PromptMessageRole, render_prompt_message
from .context_engine_manager import ContextEngineManager
from .annotation_service import AnnotationService
from .file_content_service import FileReference, resolve_image_retrieval_query
from .langchain_tool_server import Search
from .memory_config_service import MemoryConfigService
from .model_parameter_merger import ModelParameterMerger
from .model_service import ModelApiKeyService, ModelConfigService
from .multimodal_service import (
    MultimodalService,
    deserialize_file_reference,
    sanitize_processed_files_for_history,
    serialize_file_reference,
)
from .tool_orchestrator import ToolOrchestrator
from .context_assembler import (
    ContextEvidence,
    append_external_context_rule,
)
from .tool_service import ToolService
from ..memory.emotion.emotion_resolver import (
    apply_detection as apply_emotion_detection,
    cancel_detection as cancel_emotion_detection,
    start_detection as start_emotion_detection,
)

logger = logging.getLogger(__name__)


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    """Normalize a possibly dirty UUID input to a UUID or None.

    Background: release snapshots (app_releases.config) are serialized by
    `multi_agent_config_to_dict`, and historical versions wrote a NULL
    master_agent_id as the literal string "None"; reading it back and writing it
    into a UUID column triggered asyncpg `DataError: invalid UUID 'None'`, which
    surfaced on the observability path as "集群主执行记录创建失败（已降级）" and
    made the whole master record disappear (2026-09-14).

    Normalize once at the single write entry point: this fixes the root-cause data
    and ensures dirty values from any source can no longer break the entire
    observability chain. Invalid strings are downgraded to None as well - missing
    observability data is far better than a failed conversation.
    """
    if value is None or isinstance(value, uuid.UUID):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nil", "undefined"}:
        return None
    try:
        return uuid.UUID(text)
    except (ValueError, AttributeError, TypeError):
        logger.warning(f"agent_executions 写入时丢弃非法 UUID 入参: {value!r}")
        return None


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
    """Input parameters for the knowledge retrieval tool."""
    query: str = Field(
        default="",
        description=(
            "文本形式的检索问题或关键词。仅当用户提出了独立的文字问题/关键词时填写；"
            "当用户要求“按这张图/用图片检索相似内容”且没有额外文字问题时，本字段留空，"
            "只通过 image_refs 传图片，切勿把图片中能看到的文字 OCR 出来填到这里。"
        ),
    )
    image_refs: list[int] = Field(
        default_factory=list,
        description=(
            "需要按图片内容检索时，填写用户消息中列出的本轮上传图片编号（从 1 开始）。"
            "仅在用户明确要求按图片检索知识库时填写；纯文本问题留空。"
        ),
    )


def _extract_image_references(
    files: list[FileInput] | list[dict[str, Any]] | None,
) -> list[FileReference]:
    """Extract this turn's image file references in upload order.

    Local files (transfer_method=local_file) have only a file_id and an empty url,
    while remote images have only a url; both must be included in the allowlist, or
    image-based retrieval silently does nothing in private deployments.
    """
    references: list[FileReference] = []
    for file in files or []:
        reference = FileReference.from_payload(file)
        if reference is not None and reference.is_image and (reference.file_id or reference.url):
            references.append(reference)
    return references


def build_uploaded_images_manifest(
    files: list[FileInput] | list[dict[str, Any]] | None,
) -> tuple[str, list[FileReference]]:
    """Build the turn's image manifest injected into the user message and return the
    ordered image reference allowlist matching the indices.

    Returns (manifest_text, image_references); returns ("", []) when there are no images.
    The manifest gives the model only indices and names: image addresses (especially
    internal ones) mean nothing to the model and should not go into the context.
    """
    image_references = _extract_image_references(files)
    if not image_references:
        return "", []
    lines = ["本轮用户上传了以下图片，可在需要按图片内容检索知识库时，"
             "通过知识库检索工具的 image_refs 参数传入对应编号："]
    for index, reference in enumerate(image_references, start=1):
        lines.append(f"[图片{index}] {reference.name or reference.url or '本地图片'}")
    return "\n".join(lines), image_references


class WebSearchInput(BaseModel):
    """Input parameters for the web search tool."""
    query: str = Field(description="需要搜索的问题或关键词")


def create_web_search_tool(web_search_config: Dict[str, Any]):
    """Create the web search tool.

    Args:
        web_search_config: Web search configuration.

    Returns:
        The web search tool.
    """
    _ = web_search_config
    logger.info("创建网络搜索工具")

    @tool(args_schema=WebSearchInput)
    def web_search_tool(query: str) -> str:
        """Search the internet for the latest information. Use this tool when the
        user's question needs real-time information, recent news, or web resources.

        Args:
            query: The question or keywords to search for.

        Returns:
            The relevant web information found.
        """
        try:
            logger.info(f"执行网络搜索: {query}")

            # Call the search service
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


async def _retrieve_chunks_via_standard(
        query: str,
        kb_config: Dict[str, Any],
        *,
        app_id: uuid.UUID | str | None,
        workspace_id: uuid.UUID | str | None,
        source: KnowledgeRetrievalSource,
        image_references: list[FileReference] | None = None,
        image_stats: dict[str, Any] | None = None,
) -> list:
    """Retrieve text and, when explicitly supported, attached image queries.

    Image references are read as bytes uniformly by the shared file content service:
    local files (only a file_id) are read directly from storage, remote images are
    downloaded by URL - both work in private deployments.

    When ``image_stats`` is provided, the function backfills image retrieval counters
    in place (requested/encoded/succeeded/failed) so callers can feed failure reasons
    back to the model.
    """
    knowledge_bases = (kb_config or {}).get("knowledge_bases", []) or []
    valid_kbs = [kb for kb in knowledge_bases if kb.get("kb_id")]
    kb_ids = [kb["kb_id"] for kb in valid_kbs]
    if not kb_ids:
        return []

    first_kb = valid_kbs[0] or {}

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

    vector_similarity_weight = (
        None
        if retrieve_type == RetrieveType.PARTICIPLE
        else _as_float(first_kb.get("vector_similarity_weight"), 0.5)
    )
    enable_graph_retrieval = (
        1
        if (
            retrieve_type == RetrieveType.HYBRID
            and _as_int(first_kb.get("enable_graph_retrieval"), 0) == 1
        )
        else 0
    )
    request_top_k = (
        _as_int(kb_config.get("reranker_top_k"), 10)
        if rerank_id is not None
        else _as_int(first_kb.get("top_k"), 3)
    )

    # Shared by the text query and image requests; the query itself varies by modality
    request_kwargs: dict[str, Any] = dict(
        source=source,
        kb_ids=[uuid.UUID(kid) for kid in kb_ids],
        knowledge_bases=valid_kbs,
        top_k=request_top_k,
        similarity_threshold=_as_float(first_kb.get("similarity_threshold"), 0.7),
        vector_similarity_weight=vector_similarity_weight,
        retrieve_type=retrieve_type,
        rerank_id=rerank_id,
        rerank_mode=kb_config.get("rerank_mode"),
        rerank_weights=kb_config.get("rerank_weights"),
        enable_graph_retrieval=enable_graph_retrieval,
    )
    context = await build_app_knowledge_context(
        app_id,
        source=source,
        trace_id=uuid.uuid4().hex,
        expected_workspace_id=workspace_id,
    )
    retriever = get_knowledge_retriever()
    # Empty query with images means pure image search: skip text retrieval so visible
    # text in the image does not dominate recall
    results: list = []
    if (query or "").strip():
        text_request = KnowledgeRetrievalRequest(query=query, **request_kwargs)
        results = list((await retriever.retrieve(text_request, context)).chunks)

    image_diagnostics: dict[str, Any] = {"requested": 0, "encoded": 0, "succeeded": 0, "failed": 0}
    if image_references:
        from ..integrations.knowledge.retrieval_policy import image_retrieval_supported
        from .image_retrieval_guard import (
            check_image_retrieval,
            expand_knowledge_to_leaf_ids,
        )

        available_references = [ref for ref in image_references if ref.locator]
        # Deduplicate by locator, keeping first-seen order
        unique_references = list({ref.locator: ref for ref in available_references}.values())
        image_diagnostics["requested"] = len(unique_references)

        # Folder-type knowledge bases must be expanded to leaf bases first: image
        # capability depends on the model bound to each leaf, and using the folder
        # ID directly gets rejected early at the folder level.
        leaf_kb_ids = await expand_knowledge_to_leaf_ids(
            [uuid.UUID(kid) for kid in kb_ids],
            db=None,
        )
        logger.info(
            "知识库图片检索开始 kb_ids=%s leaf_kb_ids=%s retrieve_type=%s image_count=%s",
            kb_ids, leaf_kb_ids, retrieve_type.value, len(unique_references),
        )
        # Pre-check locally against the knowledge base's image limits, and give the
        # same specific reason as downstream when it rejects.
        # The agent flow is not interrupted: image search degrades, and the reason is
        # fed back to the model via diagnostics.
        rejection_reason = await check_image_retrieval(
            kb_ids=leaf_kb_ids,
            knowledge_bases=valid_kbs,
            retrieve_type=retrieve_type,
            rerank_id=rerank_id,
            rerank_mode=kb_config.get("rerank_mode"),
            enable_graph_retrieval=enable_graph_retrieval,
            metadata_filter_mode=kb_config.get("metadata_filter_mode"),
        )
        if rejection_reason is None and not await image_retrieval_supported(
            retriever,
            kb_ids=[str(kb_id) for kb_id in leaf_kb_ids],
            retrieve_type=retrieve_type,
            context=context,
            rerank_id=str(rerank_id) if rerank_id else None,
        ):
            # Fail closed: record explicitly when the policy is unsupported/unavailable
            # so image retrieval is never silently dropped
            rejection_reason = "当前知识库/检索模式不支持图片检索"
        if rejection_reason is not None:
            image_diagnostics["failed"] = len(unique_references)
            image_diagnostics["reason"] = rejection_reason
            logger.warning(
                "知识库图片检索未执行 kb_ids=%s retrieve_type=%s reason=%s",
                kb_ids, retrieve_type.value, rejection_reason,
            )
        else:
            for reference in unique_references:
                # Local files are read and encoded straight from storage, remote images
                # are downloaded by URL; neither depends on model-side reachability
                image_query = await resolve_image_retrieval_query(
                    reference,
                    workspace_id=context.principal.workspace_id if context.principal else None,
                    tenant_id=context.principal.tenant_id if context.principal else None,
                )
                if image_query is None:
                    image_diagnostics["failed"] += 1
                    logger.warning(
                        "知识库图片检索跳过：图片读取/编码失败 locator=%s", reference.locator,
                    )
                    continue
                image_diagnostics["encoded"] += 1
                image_request = KnowledgeRetrievalRequest(query=image_query, **request_kwargs)
                try:
                    image_chunks = (await retriever.retrieve(image_request, context)).chunks
                    image_diagnostics["succeeded"] += 1
                    logger.info(
                        "知识库图片检索命中 locator=%s chunks=%s",
                        reference.locator, len(image_chunks),
                    )
                    results.extend(image_chunks)
                except Exception as image_exc:
                    image_diagnostics["failed"] += 1
                    # One failed image retrieval must not discard already-successful
                    # text results
                    logger.warning(
                        "知识库图片检索失败: %s",
                        image_exc,
                        extra={"error": str(image_exc), "error_type": type(image_exc).__name__},
                    )
        logger.info("知识库图片检索结束 %s", image_diagnostics)
        if image_stats is not None:
            image_stats.update(image_diagnostics)

    seen_chunk_ids: set[str] = set()
    unique_results = []
    for chunk in results:
        metadata = getattr(chunk, "metadata", {}) or {}
        chunk_id = str(metadata.get("chunk_id") or metadata.get("id") or "")
        if chunk_id and chunk_id in seen_chunk_ids:
            continue
        if chunk_id:
            seen_chunk_ids.add(chunk_id)
        # Same policy as the workflow knowledge retrieval node: strip NULs from chunk
        # text as soon as content is retrieved (PDF/Office parsing often mixes in
        # U+0000), so later context/steps assembly does not fail on jsonb writes.
        content = getattr(chunk, "page_content", None)
        if isinstance(content, str):
            chunk.page_content = sanitize_json_text(content)
        unique_results.append(chunk)
    return unique_results



def create_knowledge_retrieval_tool(
        kb_config,
        kb_ids,
        user_id,
        *,
        app_id: uuid.UUID | str | None,
        workspace_id: uuid.UUID | str | None,
        source: KnowledgeRetrievalSource,
        citations_collector: Optional[List[Citation]] = None,
        kb_names: Optional[List[Dict]] = None,
        uploaded_files: Optional[List[FileInput]] = None,
):
    """Retrieve relevant information from the knowledge base. Use this tool when the
    user's question needs to reference knowledge bases, documents, or history.

    Args:
        kb_config: Knowledge base configuration.
        kb_ids: List of knowledge base IDs.
        user_id: User ID.
        app_id: Application that owns the knowledge configuration.
        workspace_id: Workspace that owns the application configuration.
        source: Business source of the knowledge call.
        citations_collector: List collecting citation info (passed in externally, filled during tool execution).
        kb_names: Knowledge base name list [{"id": "...", "name": "..."}]

    Returns:
        The relevant knowledge content retrieved.
    """
    logger.info(f"创建知识库检索工具，用户：{user_id}")
    # Ordered allowlist of this turn's uploaded images (1-based index -> image
    # reference); the model may reference only these images.
    # Local files have only a file_id and an empty url; bytes are read uniformly by
    # the shared file content service, independent of URL reachability.
    image_references: list[FileReference] = []

    def set_uploaded_files(files: list[FileInput] | list[dict[str, Any]] | None) -> None:
        """Record current-turn image references that the model may reference by index."""
        image_references[:] = _extract_image_references(files)

    set_uploaded_files(uploaded_files)

    @tool(args_schema=KnowledgeRetrievalInput)
    async def knowledge_retrieval_tool(query: str = "", image_refs: list[int] | None = None) -> str:
        """Retrieve relevant information from the knowledge base. Use when the user's
        question needs to reference knowledge bases, documents, or history.

        Parameter notes:
        - query: The retrieval question or keywords as text; may be empty. Fill it
          only when the user asks an independent textual question or provides
          keywords; never put image URLs, links, or base64 into query.
          When the user only asks to "search the knowledge base by this image /
          for similar content using the image" with no extra textual question,
          query MUST be empty and only image_refs is passed; in particular do not
          OCR or transcribe visible titles/text from the image into query, which
          would turn image search into text search.
        - image_refs: Fill only when the user asks to "search the knowledge base by
          image / by this image for similar content". Values are the indices N
          (integers, starting at 1) from the "[图片N]" list in the user message;
          multiple indices may be passed. For pure text questions, or when the user
          did not ask for image search, leave empty or pass an empty list; never
          invent indices. For example, if the user message contains "[图片1]
          xxx.png" and asks for image search, pass image_refs=[1] and leave query
          empty. What follows "[图片N]" in the list may be a file name or
          "本地图片"; either way the index value is unaffected - do not infer
          image usability from it.

        At least one of query and image_refs must be non-empty.

        Returns:
            The relevant knowledge content retrieved.
        """

        try:
            # Only retrieve images the model explicitly selected and that are on this
            # turn's upload allowlist; no arbitrary references
            selected_image_references: list[FileReference] = []
            if image_refs:
                if not image_references:
                    # The model gave indices but this turn has no image allowlist
                    # (ordering / non-image-upload issue)
                    logger.warning(
                        "知识库工具收到 image_refs=%s 但本轮图片白名单为空，忽略图片检索",
                        image_refs,
                    )
                for ref in image_refs:
                    if isinstance(ref, bool) or not isinstance(ref, int):
                        logger.warning("知识库工具 image_refs 含非整数编号，已忽略: %r", ref)
                        continue
                    if 1 <= ref <= len(image_references):
                        selected_image_references.append(image_references[ref - 1])
                    else:
                        logger.warning(
                            "知识库工具 image_refs 编号越界: ref=%s 本轮图片数=%s",
                            ref, len(image_references),
                        )
            query = (query or "").strip()
            logger.info(
                "知识库工具检索 query=%r image_refs=%s 选中图片数=%s",
                query[:120], image_refs, len(selected_image_references),
            )
            if not query and not selected_image_references:
                # Both retrieval entries are empty: cannot search, tell the model
                # explicitly to supply the parameters
                return "检索失败：query 与 image_refs 不能同时为空。请提供文字问题，或在用户要求按图检索时传入有效图片编号。"
            image_stats: dict[str, Any] = {}
            retrieve_chunks_result = await _retrieve_chunks_via_standard(
                query,
                kb_config,
                app_id=app_id,
                workspace_id=workspace_id,
                source=source,
                image_references=selected_image_references,
                image_stats=image_stats,
            )
            # The model explicitly requested image search but none of the images
            # succeeded: give clear feedback so the model does not pretend it searched
            # by image without any image evidence
            image_failure_notice = ""
            if image_stats.get("requested", 0) > 0 and image_stats.get("succeeded", 0) == 0:
                # Pre-checks/policy rejections carry a specific reason; relay it to the
                # model instead of only saying "try again later"
                failure_detail = str(image_stats.get("reason") or "").strip() or (
                    "图片暂不可下载/编码，或知识库当前不支持图片模态"
                )
                image_failure_notice = (
                    f"\n\n注意：本次按图片检索未成功执行（{failure_detail}），"
                    "以上仅为文本检索结果（如有）。请不要声称已根据图片内容检索，"
                    "请提示用户按上述原因调整配置，或改用文字描述。"
                )
                logger.warning("知识库图片检索全部失败 image_stats=%s", image_stats)
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

                # Collect citation info
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

                # Collect source knowledge base info for each result (for the
                # agent_executions record)
                # Build a knowledge base ID -> name map
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
                # Attach source info to the function attribute for external readers
                knowledge_retrieval_tool._last_sources = sources
                from .context_assembler import ContextEvidence
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

                return f"检索到以下相关信息：\n\n{context}{image_failure_notice}"
            else:
                knowledge_retrieval_tool._last_sources = []
                knowledge_retrieval_tool._context_evidence = []
                logger.warning("知识库检索未找到结果 image_stats=%s", image_stats)
                if image_failure_notice:
                    return "未找到相关信息。" + image_failure_notice.strip()
                return "未找到相关信息"
        except Exception as e:
            knowledge_retrieval_tool._context_evidence = []
            logger.error("知识库检索失败", extra={"error": str(e), "error_type": type(e).__name__})
            return f"检索失败: {str(e)}"

    # Keep the current turn's files mutable after the tool is constructed.
    object.__setattr__(knowledge_retrieval_tool, "set_uploaded_files", set_uploaded_files)
    # Attach tool metadata for Agent execution records and sandbox serialization.
    knowledge_retrieval_tool._tool_meta = {
        "tool_type": "knowledge_retrieval",
        "sources": [{"id": item["id"], "name": item["name"], "knowledge_name": item["name"]} for item in (kb_names or [])],
    }
    return knowledge_retrieval_tool


class AgentRunService:
    """Agent run service class."""

    def __init__(self, db: Session | AsyncSession | None):
        """Agent run service.

        Args:
            db: Database session.
        """
        self.db = db

    def _uses_async_session(self) -> bool:
        return isinstance(self.db, AsyncSession)

    async def _release_db_connection(self) -> None:
        """Release the underlying DB connection back to the pool before LLM streaming."""
        try:
            if self._uses_async_session():
                await self.db.close()
            else:
                self.db.close()
        except Exception as e:
            logger.warning(f"Failed to release DB connection: {e}")

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
            agent_role: str = "master",
            parent_execution_id: Optional[uuid.UUID] = None,
            orchestration_mode: Optional[str] = None,
            release_id: Optional[uuid.UUID] = None,
            agent_name: Optional[str] = None,
            agent_id: Optional[str] = None,
            message_id: Optional[uuid.UUID] = None,
            task: Optional[str] = None,
    ) -> uuid.UUID:
        """Create the Agent execution record (running).

        In the multi-agent cluster scenario:
        - The master Agent's execution is created by the orchestrator (agent_role="master");
        - Sub agents are created by AgentRunService itself (agent_role="sub" + parent_execution_id).

        Note: a cluster sub Agent's agent_config is an AgentConfigProxy whose id is
        release.id (see _load_agent_async), while the agent_config_id foreign key
        points to agent_configs.id; writing it directly fails the FK - so sub agents
        are persisted via release_id with agent_config_id=None.
        """
        async with get_async_db_context() as db:
            execution = AgentExecution(
                app_id=app_id,
                conversation_id=conversation_id,
                message_id=_as_uuid(message_id),
                agent_config_id=_as_uuid(agent_config_id),
                release_id=_as_uuid(release_id),
                triggered_by=None,
                steps=[],
                status="running",
                started_at=started_at,
                agent_role=agent_role,
                parent_execution_id=_as_uuid(parent_execution_id),
                orchestration_mode=orchestration_mode,
                meta_data={
                    "model": model_name,
                    "provider": provider,
                    "agent_name": agent_name,
                    "agent_id": agent_id,
                    # The task a sub Agent received (the "input" on the log detail
                    # page); same source and truncation length as the agent_dispatch
                    # event's task so the live panel matches the detail page.
                    "task": task,
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
            agent_log: Optional[dict] = None,
            meta_patch: Optional[dict] = None,
    ) -> None:
        """Finalize the Agent execution record.

        Args:
            meta_patch: Observability fields appended/overwritten into meta_data
                (missing-required / undefined cluster variables are recorded here).
                When omitted, existing behavior is fully preserved.
        """
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
            if agent_log is not None:
                record.agent_log = agent_log
            if meta_patch:
                # JSON column: assigning a new dict triggers change tracking (mutating
                # the dict in place is not detected by SQLAlchemy)
                merged_meta = dict(record.meta_data or {})
                merged_meta.update(meta_patch)
                record.meta_data = merged_meta

            await db.commit()

    async def _record_api_key_usage_async(self, api_key_id: uuid.UUID | None) -> bool:
        if not api_key_id:
            return False
        async with get_async_db_context() as db:
            return await ModelApiKeyService.record_api_key_usage_bridge_async(db, api_key_id)

    def _build_debug_id(self) -> str:
        """Generate an error trace ID for log/SSE alignment."""
        return f"err_{uuid.uuid4().hex[:12]}"

    def _extract_exception_message(self, error: Exception) -> str:
        """Prefer extracting a cleaner error message from the exception's structure."""
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
        """Build a compact but sufficient structured error payload."""
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
        """Build the model_error event for the compare scenario uniformly."""
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
        """Build the model_end event for the compare scenario uniformly."""
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
        """Check whether an annotation is hit.

        Args:
            app_id: Application ID.
            message: User message.
            source: Source (recorded as the hit origin).

        Returns:
            The annotation result dict on a hit, None otherwise.
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
                embedding_ref = await ModelApiKeyService.resolve_invoke_ref_async(
                    db,
                    setting.model_config_id,
                    tenant_id=tenant_id,
                )
                if not embedding_ref:
                    return None

                threshold = setting.similarity_threshold

            query_embedding = await RedBearEmbeddings.for_invoke(embedding_ref).aembed_query(message)
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
        """Called only after the knowledge base or memory tool returned valid evidence."""
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
                embedding_ref = await ModelApiKeyService.resolve_invoke_ref_async(
                    db, setting.model_config_id,
                    tenant_id=await self._resolve_app_tenant_id_async(app_id),
                )
                if not embedding_ref:
                    return []
            candidates = await asyncio.to_thread(
                AnnotationService.find_context_candidates,
                message, annotations, embedding_ref, 0.6, 3,
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

    @staticmethod
    def prepare_variables_for_cluster(
            input_vars: dict | None,
            variables_config: dict
    ) -> dict:
        """Variable merge strategy for cluster sub agents.

        Semantics differ from `prepare_variables` (entry validation for standalone
        applications) - in a cluster, a sub agent is not the entry point; the
        variables it receives are a package issued uniformly by the cluster:

        1. **Values passed in by the cluster win**: the cluster is the caller; a
           sub agent has no reason to override it;
        2. **The sub agent's definition fills defaults**: variables the cluster did
           not pass (or passed empty) fall back to defaults from this agent's
           definition - this was a cause of "defined a variable but it never took
           effect" (applications written for the cluster usually do not know which
           variables a sub agent needs internally);
        3. **No more aborting the whole cluster over a sub agent's required rules**:
           the old implementation raised ValueError for missing required variables,
           so "one sub agent in the cluster declared a required variable -> the
           whole conversation returned 500". Required-ness should be decided by the
           cluster entry (see multi_agent_variable_contract.build_cluster_variable_bag);
           the sub agent layer only fills defaults and records observations.

        Returns:
            The merged variable dict (undefined variables are kept, not dropped here).
        """
        from .multi_agent_variable_contract import merge_sub_agent_variables

        merged, missing_required, undefined = merge_sub_agent_variables(input_vars, variables_config)
        if missing_required or undefined:
            logger.warning(
                "子 Agent 变量合并：存在待确认项",
                extra={
                    "missing_required": missing_required,
                    "undefined_values": undefined,
                },
            )
        return merged

    async def load_tools_config(self, tools_config, web_search, tenant_id, user_id=None, workspace_id=None) -> list:
        """Load tool configuration."""
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
                        # Look up the tool instance by tool name
                        tool_instance = await tool_service.get_tool_instance_async(
                            tool_config.get("tool_id", ""),
                            tenant_id,
                        )
                        if tool_instance:
                            tool_instance.set_runtime_context(user_id=user_id, workspace_id=workspace_id)
                            # Convert to a LangChain tool
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

            # Attach metadata (skill name) to skill tools
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
            user_id,
            *,
            app_id: uuid.UUID,
            workspace_id: uuid.UUID,
            source: KnowledgeRetrievalSource,
    ) -> tuple[list, list]:
        """Return (tools, citations_collector)."""
        if not knowledge_retrieval_config:
            return [], []

        citations_collector = []
        tools = []
        knowledge_bases = knowledge_retrieval_config.get("knowledge_bases", [])
        kb_ids = [kb["kb_id"] for kb in knowledge_bases if kb.get("kb_id")]
        if kb_ids:
            # Query knowledge base names
            kb_names = []
            try:
                async with get_async_db_context() as db:
                    result = await db.execute(
                        select(Knowledge.id, Knowledge.name).where(Knowledge.id.in_(kb_ids))
                    )
                    rows = result.all()

                    # Folder-type knowledge bases are expanded to their child bases
                    # during retrieval, so a hit chunk's knowledge_id is the child
                    # base's ID. If the name map only holds folder IDs, child bases
                    # fall back to displaying UUIDs. Recursively expand the selected
                    # folders here to fill in names for all descendants.
                    pending = [r.id for r in rows]
                    seen = set(pending)
                    child_rows = []
                    while pending:
                        result = await db.execute(
                            select(Knowledge.id, Knowledge.name).where(
                                Knowledge.parent_id.in_(pending)
                            )
                        )
                        level = result.all()
                        pending = []
                        for r in level:
                            if r.id not in seen:
                                seen.add(r.id)
                                pending.append(r.id)
                                child_rows.append(r)
                    rows = rows + child_rows
                kb_names = [{"id": str(r.id), "name": r.name} for r in rows]

                # For shared knowledge bases, the knowledge_id in chunk metadata is
                # the source_kb_id; map source_kb_id to a name too, or it displays
                # as the raw ID
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
                app_id=app_id,
                workspace_id=workspace_id,
                source=source,
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
            user_rag_memory_id,
            app_id: uuid.UUID | None = None,
    ) -> tuple[list, bool]:
        """Load long-term memory configuration."""
        from ..memory.memory_service import create_long_term_memory_tool

        enabled = bool(memory_config and memory_config.get("enabled"))
        config_id = None
        if enabled and workspace_id:
            async with get_async_db_context() as db:
                config_id = await MemoryConfigService(db).get_workspace_active_config_id_async(workspace_id)

        tool = create_long_term_memory_tool(
            memory_config, user_id, workspace_id, storage_type, user_rag_memory_id,
            config_id=config_id,
            app_id=app_id,
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
        """Validate uploaded files against the file_upload configuration."""
        if not files or not features_config:
            return
        fu = features_config.get("file_upload", {})
        if not (isinstance(fu, dict) and fu.get("enabled")):
            raise BusinessException("该应用未开启文件上传功能", BizCode.BAD_REQUEST)
        max_count = fu.get("max_file_count", 5)
        if len(files) > max_count:
            raise BusinessException(f"文件数量超过限制（最多 {max_count} 个）", BizCode.BAD_REQUEST)

        # Validate transfer methods
        allowed_methods = fu.get("allowed_transfer_methods", ["local_file", "remote_url"])
        for f in files:
            if f.transfer_method.value not in allowed_methods:
                raise BusinessException(
                    f"不支持的文件传输方式：{f.transfer_method.value}，允许的方式：{', '.join(allowed_methods)}",
                    BizCode.BAD_REQUEST
                )

        # Per-type config keys for the enabled flag and size limit
        type_cfg = {
            "image":    ("image_enabled",    "image_max_size_mb",    20,  "图片"),
            "audio":    ("audio_enabled",    "audio_max_size_mb",    50,  "音频"),
            "document": ("document_enabled", "document_max_size_mb", 100, "文档"),
            "video":    ("video_enabled",    "video_max_size_mb",    500, "视频"),
        }

        for f in files:
            ftype = str(f.type)  # e.g. "image", "audio", "document", "video"
            cfg = type_cfg.get(ftype)
            if cfg is None:
                continue
            enabled_key, size_key, default_max_mb, label = cfg

            # Validate the per-type switch
            if not fu.get(enabled_key):
                raise BusinessException(f"该应用未开启{label}文件上传", BizCode.BAD_REQUEST)

            # Validate file size (only when content is already loaded)
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
        """Return the opening statement text on the first turn (with variable substitution), else None."""
        if not is_new_conversation:
            return None, None
        opening = features_config.get("opening_statement", {})
        if not (isinstance(opening, dict) and opening.get("enabled") and opening.get("statement")):
            return None, None

        statement = opening["statement"]
        suggested_questions = opening["suggested_questions"]

        # Substitute variables if any (only the {{var_name}} form is supported)
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
        """Return citations per the citation switch; append download links when allow_download is set."""
        citation_cfg = features_config.get("citation", {})
        if not (isinstance(citation_cfg, dict) and citation_cfg.get("enabled")):
            return []
        allow_download = citation_cfg.get("allow_download", False)
        result = []
        for cit in citations:
            item = cit.model_dump() if hasattr(cit, "model_dump") else dict(cit)
            if allow_download and item.get("document_id"):
                from ..config import current_settings
                item["download_url"] = f"{current_settings().file_local_server_url}/apps/citations/{item['document_id']}/download"
            result.append(item)
        return result

    @bind_usage("app", "agent_config.app_id")
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
            stateless: bool = False,
            execution_mode: Literal["in_process", "sandbox"] = "in_process",
            parent_execution_id: Optional[uuid.UUID] = None,
            orchestration_mode: Optional[str] = None,
            execution_owner: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run a draft run (using the LangChain Agent).

        Args:
            agent_config: Agent configuration.
            model_config: Model configuration.
            message: User message.
            workspace_id: Workspace ID (required, for conversation isolation).
            conversation_id: Conversation ID (for multi-turn conversations).
            user_id: User ID.
            variables: Custom variable values.
            storage_type: Storage type (optional).
            user_rag_memory_id: User RAG memory ID (optional).
            web_search: Whether to enable web search (default True).
            memory: Whether to enable long-term memory (default True).
            sub_agent: Whether this is a sub agent call (default False).
            files: Multimodal file list (optional).
            history: Externally supplied history messages (optional, for regeneration).
            skip_save: Whether to skip saving messages (for regeneration).
            stateless: Whether to run the sub Agent without conversation or message persistence.
            execution_mode: Execution mode (in_process / sandbox).
            parent_execution_id: Execution ID of the parent (master) Agent in cluster orchestration; passed through by the orchestrator when sub_agent=True.
            orchestration_mode: Orchestration mode supervisor / supervisor_loop / collaboration (cluster scenarios only).
            execution_owner: {"release_id": UUID, "agent_name": str}, avoids the AgentConfigProxy.id foreign-key pitfall.

        Returns:
            Dict: Dict containing the AI reply and metadata.
        """
        if stateless and (not sub_agent or not skip_save):
            raise ValueError("stateless 模式必须与 sub_agent=True、skip_save=True 一起使用")
        if stateless and history is None:
            history = []

        start_time = time.time()
        user_message_id = uuid.uuid4()
        assistant_message_id = uuid.uuid4()
        tools_config: dict | list | None = agent_config.tools
        skills_config: dict | None = agent_config.skills
        knowledge_retrieval_config: dict | None = agent_config.knowledge_retrieval
        memory_config: dict | None = agent_config.memory
        features_config: dict = agent_config.features or {}
        # Cluster sub Agent execution trace (AgentTraceRecorder snapshot), taken from
        # the agent_log returned by agent.chat()
        _trace: Optional[dict] = None
        agent_execution_id: Optional[uuid.UUID] = None

        # Read feature switches from features (takes precedence over parameter defaults)
        web_search_feature = features_config.get("web_search", {})
        if not isinstance(web_search_feature, dict) or not web_search_feature.get("enabled"):
            web_search = False

        # file_upload validation
        self._validate_file_upload(features_config, files)
        # Emotion-aware reply: runs concurrently with prompt/context-engine/multimodal
        # preparation; the result is injected before ToolOrchestrator.
        emotion_detection = start_emotion_detection(features_config, message)
        tenant_id = await self._get_tenant_id_by_workspace_id_async(workspace_id)

        try:
            # 1. Fetch the API Key configuration
            api_key_config = await self._get_api_key(model_config.id, tenant_id=tenant_id)
            # Remote mode: the undecrypted view serves the invoke seam and multimodal
            # formatting; credentials still serve existing consumers like sandbox/usage
            model_view = await ModelConfigService.get_runtime_model_view_bridge_async(
                self.db, model_config.id, tenant_id=tenant_id
            )
            logger.debug(
                "API Key 配置获取成功",
                extra={
                    "model_name": api_key_config["model_name"],
                    "has_api_key": bool(api_key_config["api_key"]),
                    "has_api_base": bool(api_key_config.get("api_base"))
                }
            )

            # 2. Merge model parameters
            effective_params = ModelParameterMerger.get_effective_parameters(
                model_config=model_config,
                agent_config=agent_config
            )

            if sub_agent:
                variables = self.prepare_variables_for_cluster(variables, agent_config.variables)
            else:
                # FIXME: subagent input valid
                variables = variables or {}

            system_prompt = render_prompt_message(
                agent_config.system_prompt,
                PromptMessageRole.USER,
                variables
            )

            # 3. Process the system prompt (with variable substitution)
            system_prompt = system_prompt.get_text_content() or "你是一个专业的AI助手"

            # 4. Load tool/skill/knowledge/memory configuration in parallel (each uses
            # its own DB session; no interdependencies)
            tools = []
            parallel_coros = [
                self.load_tools_config(tools_config, web_search, tenant_id, user_id, workspace_id),
                self.load_skill_config(skills_config, message, tenant_id, user_id, workspace_id),
                self.load_knowledge_retrieval_config(
                    knowledge_retrieval_config,
                    user_id,
                    app_id=agent_config.app_id,
                    workspace_id=workspace_id,
                    source=KnowledgeRetrievalSource.AGENT if sub_agent else KnowledgeRetrievalSource.DRAFT,
                ),
            ]
            if memory:
                parallel_coros.append(
                    self.load_memory_config(
                        memory_config, user_id, workspace_id, storage_type,
                        user_rag_memory_id, app_id=agent_config.app_id,
                    )
                )
            parallel_results = await asyncio.gather(*parallel_coros, return_exceptions=True)

            base_tools = parallel_results[0]
            skill_result = parallel_results[1]
            kb_result = parallel_results[2]
            memory_result = parallel_results[3] if memory else None

            if not isinstance(base_tools, Exception):
                tools.extend(base_tools)
            else:
                logger.warning("load_tools_config failed: %s", base_tools)

            if not isinstance(skill_result, Exception) and skill_result is not None:
                skill_tools, skill_prompts = skill_result
                tools.extend(skill_tools)
                if skill_prompts:
                    system_prompt = f"{system_prompt}\n\n{skill_prompts}"
            elif isinstance(skill_result, Exception):
                logger.warning("load_skill_config failed: %s", skill_result)

            if not isinstance(kb_result, Exception) and kb_result is not None:
                kb_tools, citations_collector = kb_result
                tools.extend(kb_tools)
            elif isinstance(kb_result, Exception):
                logger.warning("load_knowledge_retrieval_config failed: %s", kb_result)
                citations_collector = []

            if memory_result is not None and not isinstance(memory_result, Exception):
                memory_tools, _ = memory_result
                tools.extend(memory_tools)
            elif isinstance(memory_result, Exception):
                logger.warning("load_memory_config failed: %s", memory_result)

            # 5. Handle the conversation ID. stateless sub calls explicitly skip
            # conversation creation and validation.
            if not stateless:
                is_new_conversation = not conversation_id
                opening, suggested_questions = None, None
                if not sub_agent:
                    opening, suggested_questions = self._get_opening_statement(
                        features_config, is_new_conversation, variables
                    )
                conversation_id = await self._ensure_conversation(
                    conversation_id=conversation_id,
                    app_id=agent_config.app_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    sub_agent=sub_agent,
                    opening_statement=opening,
                    suggested_questions=suggested_questions,
                )

            # Check for an annotation hit
            if not sub_agent:
                annotation_match = await self._check_annotation_match(agent_config.app_id, message,
                                                                      source=source)
                if annotation_match:
                    elapsed_time = time.time() - start_time
                    # Annotation hit: return directly
                    cancel_emotion_detection(emotion_detection)
                    # With skip_save=True the caller saves versioned messages itself;
                    # skip the duplicate save inside run
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

            # 6. Load history messages (including the opening statement)
            used_context_engine = False
            if history is None:
                context_engine_manager = ContextEngineManager(self.db)
                prepared_input = await context_engine_manager.prepare_app_agent_input(
                    features=features_config,
                    conversation_id=uuid.UUID(conversation_id),
                    system_prompt=system_prompt,
                    current_input=message,
                    current_provider=api_key_config.get("provider"),
                    legacy_max_history=current_settings().agent_max_history,
                    model_config_id=model_config.id,
                    # Cluster sub Agent: cross_session injection is disabled; history
                    # only uses the cluster conversation
                    include_cross_session=not sub_agent,
                )
                if prepared_input:
                    system_prompt, history = prepared_input
                    used_context_engine = True
                else:
                    history = await self._load_conversation_history(
                        conversation_id=conversation_id,
                        max_history=current_settings().agent_max_history,
                        current_provider=api_key_config.get("provider"),
                    )
            # Otherwise use the externally supplied history (for regeneration)

            # 6. Process multimodal files
            processed_files = None
            has_doc_with_images = False
            # Used only for the user message sent to the LLM (appends this turn's image
            # manifest); does not pollute the raw message stored in DB
            llm_message = message
            if files:
                provider = api_key_config.get("provider", "openai")
                multimodal_service = MultimodalService(self.db, model_view)
                fu_config = features_config.get("file_upload", {})
                if hasattr(fu_config, "model_dump"):
                    fu_config = fu_config.model_dump()
                doc_img_recognition = isinstance(fu_config, dict) and fu_config.get("document_image_recognition", False)
                processed_files = await multimodal_service.process_files(
                    files, document_image_recognition=doc_img_recognition,
                    workspace_id=workspace_id,
                    file_upload_config=fu_config if isinstance(fu_config, dict) else None,
                )
                logger.info(f"处理了 {len(processed_files)} 个文件，provider={provider}")
                # Feed this turn's uploaded files (including image URLs) back into the
                # knowledge tool as the allowlist the model may reference
                for tool in tools:
                    set_uploaded_files = getattr(tool, "set_uploaded_files", None)
                    if callable(set_uploaded_files):
                        set_uploaded_files(files)
                # List this turn's images and their indices in the user message sent to
                # the LLM so the model can explicitly trigger image search when needed
                image_manifest, _ = build_uploaded_images_manifest(files)
                if image_manifest:
                    llm_message = f"{message}\n\n{image_manifest}"
                input_modalities = api_key_config.get("input_modalities") or []
                has_doc_with_images = (
                    doc_img_recognition
                    and Modality.IMAGE in input_modalities
                    and any(f.type == FileType.DOCUMENT for f in files)
                )
            if has_doc_with_images:
                system_prompt += (
                    "\n\n文档文字中可能包含图片位置标记如 [图片 第2页 第1张]。"
                    "对应图片已作为独立视觉输入提供，请结合位置标记和视觉内容理解文档；"
                    "不要在回答中输出任何文件地址、存储路径或内部标识。"
                )

            # 7. Choose the execution path based on model capabilities
            features = api_key_config.get("features") or []
            async def load_annotation_context():
                return await self._load_annotation_context_evidence(agent_config.app_id, message)
            system_prompt = append_external_context_rule(system_prompt)
            # Emotion-aware reply: must complete before ToolOrchestrator / LangChainAgent.
            system_prompt = await apply_emotion_detection(
                system_prompt, emotion_detection, user_message_id,
                app_id=agent_config.app_id, tenant_id=tenant_id, db=self.db,
                write_cache=False,
            )
            use_agent_mode = ModelFeature.FUNCTION_CALL in features
            orchestrator_node_executions = []
            if not use_agent_mode and tools:
                # Weak models: drive multi-turn tool calls with a ReAct prompt and
                # inject the trace into system_prompt
                system_prompt, orchestrator_node_executions = await ToolOrchestrator.create_and_run(
                    tools=tools,
                    system_prompt=system_prompt,
                    message=llm_message,
                    history=history,
                    model_view=model_view,
                    effective_params=effective_params,
                    processed_files=processed_files,
                    context_evidence_loader=load_annotation_context,
                )
                tools = []

            agent = LangChainAgent(
                model_name=api_key_config["model_name"],
                model_view=model_view,
                provider=api_key_config.get("provider", "openai"),
                temperature=effective_params.get("temperature", 0.7),
                max_tokens=effective_params.get("max_tokens", 2000),
                system_prompt=system_prompt,
                tools=tools,
                deep_thinking=effective_params.get("deep_thinking", False),
                thinking_budget_tokens=effective_params.get("thinking_budget_tokens"),
                json_output=effective_params.get("json_output", False),
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

            # Create the Agent execution record (running status)
            # skip_save=True only means "do not persist conversation messages" (the
            # regeneration caller saves them itself); it does NOT mean "do not persist
            # the execution trace" - cluster sub Agents must also leave a trace, or
            # their logs would be nowhere to be found.
            agent_execution_id = None
            if not skip_save:
                agent_execution_id = await self._create_agent_execution_async(
                    app_id=agent_config.app_id,
                    conversation_id=uuid.UUID(conversation_id) if isinstance(conversation_id, str) else conversation_id,
                    # The sub Agent's agent_config is an AgentConfigProxy whose id is
                    # release.id; it cannot be written into agent_config_id
                    # (FK->agent_configs.id), so use release_id instead
                    agent_config_id=None if sub_agent else agent_config.id,
                    started_at=utcnow_naive(),
                    model_name=api_key_config["model_name"],
                    provider=api_key_config.get("provider"),
                    agent_role="sub" if sub_agent else "master",
                    parent_execution_id=parent_execution_id,
                    orchestration_mode=orchestration_mode,
                    release_id=(execution_owner or {}).get("release_id"),
                    agent_name=(execution_owner or {}).get("agent_name"),
                    agent_id=(execution_owner or {}).get("agent_id"),
                    task=message[:500] if sub_agent else None,
                )

            # 8. Invoke the Agent (multimodal supported)
            result = await agent.chat(
                message=llm_message,
                history=history,
                context=context,
                files=processed_files
            )
            # agent.chat() already returns agent_log (a trace.finalize snapshot)
            if isinstance(result, dict) and result.get("agent_log"):
                _trace = result.get("agent_log")

            elapsed_time = time.time() - start_time

            await self._record_api_key_usage_async(api_key_config.get("api_key_id"))

            # 9. Generate the TTS audio_url (generated before saving messages so it is
            # stored in meta_data along with them)
            audio_url = await self._generate_tts(
                features_config, result["content"], api_key_config,
                tenant_id=tenant_id, workspace_id=workspace_id
            ) if not sub_agent else None

            # Filter citations (called only once)
            filtered_citations = self._filter_citations(features_config, citations_collector)

            # Generate suggested questions (before saving messages, so they are stored
            # in meta_data)
            suggested_questions = (await self._generate_suggested_questions(
                features_config, result["content"], model_view, effective_params
            )) if not sub_agent else []

            # 10. Save conversation messages (with skip_save=True the caller saves
            # versioned messages itself; skip the duplicate save inside run)
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
                )
                if used_context_engine and not skip_save:
                    _ctx_kwargs = dict(
                        features=features_config,
                        conversation_id=uuid.UUID(conversation_id),
                        current_provider=api_key_config.get("provider"),
                        legacy_max_history=current_settings().agent_max_history,
                        model_config_id=model_config.id,
                    )
                    async def _run_after_turn(kwargs=_ctx_kwargs):
                        async with get_async_db_context() as db2:
                            await ContextEngineManager(db2).after_app_turn(**kwargs)
                    asyncio.create_task(_run_after_turn())

            # 11. Mark the Agent execution record completed
            node_executions = result.get("node_executions", [])
            if agent_execution_id is not None:
                await self._update_agent_execution_completed_async(
                    execution_id=agent_execution_id,
                    steps=orchestrator_node_executions + node_executions,
                    status="completed",
                    elapsed_time=elapsed_time,
                    token_usage=result.get("usage"),
                    # Only cluster sub Agents persist the trace (single-Agent apps
                    # keep traces in steps; existing storage volume unchanged)
                    agent_log=_trace if sub_agent else None,
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

        except asyncio.CancelledError:
            # The orchestration layer applies an overall timeout via asyncio.wait_for,
            # which cancels this coroutine on timeout; CancelledError is a BaseException,
            # so without handling it here it slips past the except Exception below and
            # the execution record stays in running forever (a zombie record).
            # Cleanup uses its own DB context (get_async_db_context), unaffected by
            # self.db state; must re-raise after cleanup to preserve cancellation
            # semantics (swallowing it would break graceful shutdown and the wait_for
            # contract).
            logger.warning(
                "LangChain Agent 调用被取消（超时或外部取消），收尾执行记录",
                extra={"agent_execution_id": str(agent_execution_id) if agent_execution_id else None},
            )
            if agent_execution_id is not None:
                try:
                    await self._update_agent_execution_completed_async(
                        execution_id=agent_execution_id,
                        steps=[],
                        status="failed",
                        elapsed_time=time.time() - start_time,
                        error_message="执行被取消（超时或外部取消）",
                        agent_log=_trace if sub_agent else None,
                    )
                except Exception as update_err:
                    # Cleanup failure must not mask the CancelledError, but leave a
                    # trace to help diagnose zombie running records
                    logger.error(
                        "取消收尾：更新执行记录失败",
                        extra={
                            "agent_execution_id": str(agent_execution_id),
                            "error": str(update_err),
                        },
                    )
            raise

        except Exception as e:
            logger.error("LangChain Agent 调用失败", extra={"error": str(e), "error_type": type(e).__name__})
            # Mark the Agent execution record failed
            if agent_execution_id is not None:
                try:
                    elapsed_time = time.time() - start_time
                    await self._update_agent_execution_completed_async(
                        execution_id=agent_execution_id,
                        steps=[],
                        status="failed",
                        elapsed_time=elapsed_time,
                        error_message=str(e)[:2000],
                        agent_log=_trace if sub_agent else None,
                    )
                except Exception:
                    pass
            # Model-configuration exceptions keep their own error code (model missing/
            # deprecated/disabled/missing key); do not downgrade to INTERNAL_ERROR
            if isinstance(e, BusinessException) and e.code in (
                BizCode.MODEL_NOT_FOUND,
                BizCode.MODEL_DEPRECATED,
                BizCode.MODEL_CONFIG_INVALID,
                BizCode.API_KEY_MISSING,
                BizCode.AGENT_CONFIG_MISSING,
            ):
                raise
            raise BusinessException(f"Agent 调用失败: {str(e)}", BizCode.INTERNAL_ERROR, cause=e)

    @bind_usage("app", "agent_config.app_id")
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
            stateless: bool = False,
            user_message_id: Optional[uuid.UUID] = None,
            execution_mode: Literal["in_process", "sandbox"] = "in_process",
            parent_execution_id: Optional[uuid.UUID] = None,
            orchestration_mode: Optional[str] = None,
            execution_owner: Optional[Dict[str, Any]] = None,

    ) -> AsyncGenerator[str, None]:
        """Run a draft run with streaming output (using the LangChain Agent).

        Args:
            agent_config: Agent configuration.
            model_config: Model configuration.
            message: User message.
            workspace_id: Workspace ID (required, for conversation isolation).
            conversation_id: Conversation ID (for multi-turn conversations).
            user_id: User ID.
            variables: Custom variable values.
            history: Externally supplied history messages (optional, for regeneration).
            skip_save: Whether to skip saving messages.
            parent_execution_id: Execution ID of the parent (master) Agent in cluster orchestration; passed through by the orchestrator when sub_agent=True.
            orchestration_mode: Orchestration mode supervisor / supervisor_loop / collaboration (cluster scenarios only).
            execution_owner: {"release_id": UUID, "agent_name": str}, avoids the AgentConfigProxy.id foreign-key pitfall.

        Yields:
            str: SSE-formatted event data.
        """
        if stateless and (not sub_agent or not skip_save):
            raise ValueError("stateless 模式必须与 sub_agent=True、skip_save=True 一起使用")
        if stateless and history is None:
            history = []

        tools_config: dict | list | None = agent_config.tools
        skills_config: dict | None = agent_config.skills
        knowledge_retrieval_config: dict | None = agent_config.knowledge_retrieval
        memory_config: dict | None = agent_config.memory
        features_config: dict = agent_config.features or {}

        # Read feature switches from features (the features config is authoritative)
        web_search_feature = features_config.get("web_search", {})
        if isinstance(web_search_feature, dict) and web_search_feature.get("enabled"):
            web_search = True

        # file_upload validation
        self._validate_file_upload(features_config, files)
        # Emotion-aware reply: started early, concurrent with prompt/context-engine/
        # multimodal preparation; the result is injected before ToolOrchestrator.
        emotion_detection = start_emotion_detection(features_config, message)
        tenant_id = await self._get_tenant_id_by_workspace_id_async(workspace_id)

        start_time = time.time()
        # Allow an externally supplied user_message_id (pre-generated in multi-model
        # compare and returned to the frontend with model_start)
        user_message_id = user_message_id or uuid.uuid4()
        assistant_message_id = uuid.uuid4()
        # Cluster sub Agent execution trace (AgentTraceRecorder snapshot):
        # agent_log_final preferred, agent_log as fallback
        _trace: Optional[dict] = None
        _agent_execution_id: Optional[uuid.UUID] = None
        # Exception safety: the except branch writes a failed record, so these locals
        # must already be defined
        orchestrator_node_executions: list = []
        node_executions: list = []
        total_tokens = 0
        full_content = ""

        try:
            # 1. Fetch the API Key configuration
            api_key_config = await self._get_api_key(model_config.id, tenant_id=tenant_id)
            # Remote mode: the undecrypted view serves the invoke seam and multimodal
            # formatting; credentials still serve existing consumers like sandbox/usage
            model_view = await ModelConfigService.get_runtime_model_view_bridge_async(
                self.db, model_config.id, tenant_id=tenant_id
            )
            if sub_agent:
                # Cluster sub Agents use the merge strategy (see the
                # prepare_variables_for_cluster docstring)
                variables = self.prepare_variables_for_cluster(variables, agent_config.variables)
            else:
                variables = variables or {}

            # 2. Merge model parameters
            effective_params = ModelParameterMerger.get_effective_parameters(
                model_config=model_config,
                agent_config=agent_config
            )

            items_params = variables

            system_prompt = render_prompt_message(
                agent_config.system_prompt,  # correct a typo
                PromptMessageRole.USER,
                items_params
            )

            # 3. Process the system prompt (with variable substitution)
            system_prompt = system_prompt.get_text_content() or "你是一个专业的AI助手"

            # 4. Load tool/skill/knowledge/memory configuration in parallel (each uses
            # its own DB session; no interdependencies)
            tools = []
            parallel_coros = [
                self.load_tools_config(tools_config, web_search, tenant_id, user_id, workspace_id),
                self.load_skill_config(skills_config, message, tenant_id, user_id, workspace_id),
                self.load_knowledge_retrieval_config(
                    knowledge_retrieval_config,
                    user_id,
                    app_id=agent_config.app_id,
                    workspace_id=workspace_id,
                    source=KnowledgeRetrievalSource.AGENT if sub_agent else KnowledgeRetrievalSource.DRAFT,
                ),
            ]
            if memory:
                parallel_coros.append(
                    self.load_memory_config(
                        memory_config, user_id, workspace_id, storage_type,
                        user_rag_memory_id, app_id=agent_config.app_id,
                    )
                )
            parallel_results = await asyncio.gather(*parallel_coros, return_exceptions=True)

            base_tools = parallel_results[0]
            skill_result = parallel_results[1]
            kb_result = parallel_results[2]
            memory_result = parallel_results[3] if memory else None

            if not isinstance(base_tools, Exception):
                tools.extend(base_tools)
            else:
                logger.warning("load_tools_config failed: %s", base_tools)

            if not isinstance(skill_result, Exception) and skill_result is not None:
                skill_tools, skill_prompts = skill_result
                tools.extend(skill_tools)
                if skill_prompts:
                    system_prompt = f"{system_prompt}\n\n{skill_prompts}"
            elif isinstance(skill_result, Exception):
                logger.warning("load_skill_config failed: %s", skill_result)

            if not isinstance(kb_result, Exception) and kb_result is not None:
                kb_tools, citations_collector = kb_result
                tools.extend(kb_tools)
            elif isinstance(kb_result, Exception):
                logger.warning("load_knowledge_retrieval_config failed: %s", kb_result)
                citations_collector = []

            if memory_result is not None and not isinstance(memory_result, Exception):
                memory_tools, _ = memory_result
                tools.extend(memory_tools)
            elif isinstance(memory_result, Exception):
                logger.warning("load_memory_config failed: %s", memory_result)

            # 5. Handle the conversation ID. stateless sub calls explicitly skip
            # conversation creation and validation.
            if not stateless:
                is_new_conversation = not conversation_id
                opening, suggested_questions = None, None
                if not sub_agent:
                    opening, suggested_questions = self._get_opening_statement(
                        features_config, is_new_conversation, variables
                    )
                conversation_id = await self._ensure_conversation(
                    conversation_id=conversation_id,
                    app_id=agent_config.app_id,
                    workspace_id=workspace_id,
                    user_id=user_id,
                    sub_agent=sub_agent,
                    opening_statement=opening,
                    suggested_questions=suggested_questions,
                )

            # Check for an annotation hit
            if not sub_agent:
                annotation_match = await self._check_annotation_match(agent_config.app_id, message,
                                                                      source=source)
                if annotation_match:
                    elapsed_time = time.time() - start_time
                    # Annotation hit: return directly
                    cancel_emotion_detection(emotion_detection)
                    # With skip_save=True the caller saves versioned messages itself;
                    # skip the duplicate save inside run_stream
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

            # 6. Load history messages
            used_context_engine = False
            if history is None:
                context_engine_manager = ContextEngineManager(self.db)
                prepared_input = await context_engine_manager.prepare_app_agent_input(
                    features=features_config,
                    conversation_id=uuid.UUID(conversation_id),
                    system_prompt=system_prompt,
                    current_input=message,
                    current_provider=api_key_config.get("provider"),
                    legacy_max_history=current_settings().agent_max_history,
                    model_config_id=model_config.id,
                )
                if prepared_input:
                    system_prompt, history = prepared_input
                    used_context_engine = True
                else:
                    history = await self._load_conversation_history(
                        conversation_id=conversation_id,
                        max_history=current_settings().agent_max_history,
                        current_provider=api_key_config.get("provider"),
                    )

            # 6. Process multimodal files
            processed_files = None
            has_doc_with_images = False
            # Used only for the user message sent to the LLM (appends this turn's image
            # manifest); does not pollute the raw message stored in DB
            llm_message = message
            if files:
                provider = api_key_config.get("provider", "openai")
                multimodal_service = MultimodalService(self.db, model_view)
                fu_config = features_config.get("file_upload", {})
                if hasattr(fu_config, "model_dump"):
                    fu_config = fu_config.model_dump()
                doc_img_recognition = isinstance(fu_config, dict) and fu_config.get("document_image_recognition", False)
                processed_files = await multimodal_service.process_files(
                    files, document_image_recognition=doc_img_recognition,
                    workspace_id=workspace_id,
                    file_upload_config=fu_config if isinstance(fu_config, dict) else None,
                )
                logger.info(f"处理了 {len(processed_files)} 个文件，provider={provider}")
                input_modalities = api_key_config.get("input_modalities") or []
                # Feed this turn's uploaded files (including image URLs) back into the
                # knowledge tool as the allowlist the model may reference
                for tool in tools:
                    set_uploaded_files = getattr(tool, "set_uploaded_files", None)
                    if callable(set_uploaded_files):
                        set_uploaded_files(files)
                # List this turn's images and their indices in the user message sent to
                # the LLM so the model can explicitly trigger image search when needed
                image_manifest, _ = build_uploaded_images_manifest(files)
                if image_manifest:
                    llm_message = f"{message}\n\n{image_manifest}"
                has_doc_with_images = (
                    doc_img_recognition
                    and Modality.IMAGE in input_modalities
                    and any(f.type == FileType.DOCUMENT for f in files)
                )
            if has_doc_with_images:
                system_prompt += (
                    "\n\n文档文字中可能包含图片位置标记如 [图片 第2页 第1张]。"
                    "对应图片已作为独立视觉输入提供，请结合位置标记和视觉内容理解文档；"
                    "不要在回答中输出任何文件地址、存储路径或内部标识。"
                )

            # 7. Choose the execution path based on model capabilities
            features = api_key_config.get("features") or []
            async def load_annotation_context():
                return await self._load_annotation_context_evidence(agent_config.app_id, message)
            system_prompt = append_external_context_rule(system_prompt)
            # Emotion-aware reply: must complete before ToolOrchestrator / LangChainAgent.
            system_prompt = await apply_emotion_detection(
                system_prompt, emotion_detection, user_message_id,
                app_id=agent_config.app_id, tenant_id=tenant_id, db=self.db,
                write_cache=False,
            )
            use_agent_mode = ModelFeature.FUNCTION_CALL in features
            orchestrator_node_executions = []
            if not use_agent_mode and tools:
                # Weak models: drive multi-turn tool calls with a ReAct prompt and
                # inject the trace into system_prompt
                system_prompt, orchestrator_node_executions = await ToolOrchestrator.create_and_run(
                    tools=tools,
                    system_prompt=system_prompt,
                    message=llm_message,
                    history=history,
                    model_view=model_view,
                    effective_params=effective_params,
                    processed_files=processed_files,
                    context_evidence_loader=load_annotation_context,
                )
                tools = []

            # Create the LangChain Agent (in-process) or route to sandbox
            if execution_mode == "sandbox":
                from .e2b_agent_adapter import E2BAgentAdapter
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
                    model_view=model_view,
                    provider=api_key_config.get("provider", "openai"),
                    temperature=effective_params.get("temperature", 0.7),
                    max_tokens=effective_params.get("max_tokens", 2000),
                    system_prompt=system_prompt,
                    tools=tools,
                    streaming=True,
                    deep_thinking=effective_params.get("deep_thinking", False),
                    thinking_budget_tokens=effective_params.get("thinking_budget_tokens"),
                    json_output=effective_params.get("json_output", False),
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

            # 8. Send the start event
            yield self._format_sse_event("start", {
                "conversation_id": conversation_id,
                "message_id": str(assistant_message_id),
                "user_message_id": str(user_message_id),
                "timestamp": time.time()
            })

            # Re-send the weak model's tool call steps to the frontend
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

            # Create the Agent execution record (running status)
            # skip_save=True only means "do not persist conversation messages" (the
            # regeneration caller saves them); it does NOT mean "do not persist the
            # execution trace".
            if not skip_save:
                _agent_execution_id = await self._create_agent_execution_async(
                    app_id=agent_config.app_id,
                    conversation_id=uuid.UUID(conversation_id) if isinstance(conversation_id, str) else conversation_id,
                    # The sub Agent's agent_config is an AgentConfigProxy whose id is
                    # release.id; it cannot be written into agent_config_id
                    # (FK->agent_configs.id), so use release_id instead
                    agent_config_id=None if sub_agent else agent_config.id,
                    started_at=utcnow_naive(),
                    model_name=api_key_config["model_name"],
                    provider=api_key_config.get("provider"),
                    agent_role="sub" if sub_agent else "master",
                    parent_execution_id=parent_execution_id,
                    orchestration_mode=orchestration_mode,
                    release_id=(execution_owner or {}).get("release_id"),
                    agent_name=(execution_owner or {}).get("agent_name"),
                    agent_id=(execution_owner or {}).get("agent_id"),
                    task=message[:500] if sub_agent else None,
                )

            # Sub Agent dispatch event: the execution_id only exists at this point;
            # the frontend can build the block first and then receive data.
            # The event type is newly added (no existing event name changed); older
            # frontends can simply ignore unknown events.
            if sub_agent and _agent_execution_id is not None:
                yield self._format_sse_event("agent_dispatch", {
                    "execution_id": str(_agent_execution_id),
                    "agent_id": (execution_owner or {}).get("agent_id"),
                    "agent_name": (execution_owner or {}).get("agent_name"),
                    "parent_execution_id": str(parent_execution_id) if parent_execution_id else None,
                    "orchestration_mode": orchestration_mode,
                    "task": message[:500],
                })

            # Read ORM attributes still needed later into plain values before close(),
            # to avoid triggering DetachedInstanceError afterwards
            _app_id = agent_config.app_id
            _model_config_id = model_config.id
            _model_config_name = model_config.name

            # db is not needed during LLM inference; return the connection to the pool
            # early
            # All tools (knowledge/memory/web_search) use independent connections and
            # do not depend on self.db
            await self._release_db_connection()

            # 9. Stream Agent output (multimodal supported) and start TTS in parallel
            full_content = ""
            full_reasoning = ""
            total_tokens = 0
            node_executions = []
            sandbox_citations = []

            # Start streaming TTS (synthesizes while text is being output)
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
                    message=llm_message,
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
                    # Incremental snapshot: fallback until the final state arrives (a
                    # mid-flight exception still leaves an inspectable trace)
                    if _trace is None and isinstance(chunk.get("data"), dict):
                        _trace = chunk.get("data")
                    yield self._format_sse_event("agent_log", chunk)
                elif isinstance(chunk, dict) and chunk.get("type") == "agent_log_final":
                    # Final snapshot wins; the SSE behavior is identical to the
                    # previous "generic branch passthrough"
                    if isinstance(chunk.get("data"), dict):
                        _trace = chunk.get("data")
                    yield self._format_sse_event("agent_log_final", chunk)
                elif isinstance(chunk, dict):
                    event_type = str(chunk.get("type") or "unknown")
                    yield self._format_sse_event(event_type, chunk)
                else:
                    full_content += chunk
                    yield self._format_sse_event("message", {"content": chunk})
                    if tts_task is not None:
                        await text_queue.put(chunk)

            # Text finished; notify TTS
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
                # Carry the execution_id: the orchestration layer/frontend uses it to
                # pair the "pre-built block" with the real sub execution record
                yield self._format_sse_event("sub_usage", {
                    "total_tokens": total_tokens,
                    "execution_id": str(_agent_execution_id) if _agent_execution_id else None,
                })

            # Filter citations (called only once)
            filtered_citations = self._filter_citations(features_config, citations_collector)

            suggested_questions = (await self._generate_suggested_questions(
                features_config, full_content, model_view, effective_params
            )) if not sub_agent else []

            # 11. Save conversation messages (with skip_save=True the caller saves
            # versioned messages itself; skip the duplicate save inside run_stream)
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
                )
                if used_context_engine and not skip_save:
                    _ctx_kwargs = dict(
                        features=features_config,
                        conversation_id=uuid.UUID(conversation_id),
                        current_provider=api_key_config.get("provider"),
                        legacy_max_history=current_settings().agent_max_history,
                        model_config_id=_model_config_id,
                    )
                    async def _run_after_turn(kwargs=_ctx_kwargs):
                        async with get_async_db_context() as db2:
                            await ContextEngineManager(db2).after_app_turn(**kwargs)
                    asyncio.create_task(_run_after_turn())

            # 11.5 Mark the Agent execution record completed
            if _agent_execution_id is not None:
                await self._update_agent_execution_completed_async(
                    execution_id=_agent_execution_id,
                    steps=orchestrator_node_executions + node_executions,
                    status="completed",
                    elapsed_time=elapsed_time,
                    token_usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                    # Only cluster sub Agents persist the trace (single-Agent apps
                    # keep traces in steps; existing storage volume unchanged)
                    agent_log=_trace if sub_agent else None,
                )

            # Sub Agent completion event: the frontend marks the corresponding block
            # complete with it (carries elapsed time/tokens/final output).
            # `output` MUST be included: the running panel's "output" has no other
            # data source (a sub run's body text goes through sub_agent_message,
            # which the frontend does not consume); without it only an empty `{}`
            # remains.
            if sub_agent:
                yield self._format_sse_event("agent_complete", {
                    "execution_id": str(_agent_execution_id) if _agent_execution_id else None,
                    "agent_id": (execution_owner or {}).get("agent_id"),
                    "agent_name": (execution_owner or {}).get("agent_name"),
                    "status": "completed",
                    "output": full_content,
                    "elapsed_time": elapsed_time,
                    "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                })

            # 12. Send the end event (including suggested_questions, audio_url, and
            # audio_status)
            end_data: Dict[str, Any] = {
                "conversation_id": conversation_id,
                "message_id": message_id,
                "elapsed_time": elapsed_time,
                "message_length": len(full_content),
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                "citations": filtered_citations,
            }
            if sub_agent:
                # Sub Agent completion event: carries execution_id / tokens; the
                # frontend uses it to close out the corresponding block
                end_data["execution_id"] = str(_agent_execution_id) if _agent_execution_id else None
            if not sub_agent:
                end_data["suggested_questions"] = suggested_questions
                end_data["audio_url"] = stream_audio_url
                # Check whether TTS has finished (non-blocking; does not cancel the task)
                audio_status = "pending"
                if tts_task is not None and tts_task.done():
                    # Task already done; check for an exception
                    try:
                        tts_task.result()
                        audio_status = "completed"
                    except Exception as e:
                        logger.warning(f"TTS任务异常: {e}")
                        audio_status = "failed"
                end_data["audio_status"] = audio_status if stream_audio_url else None
            yield self._format_sse_event("end", end_data)

            logger.info(
                "流式试运行完成",
                extra={
                    "model": _model_config_name,
                    "elapsed_time": elapsed_time,
                    "message_length": len(full_content)
                }
            )

        except asyncio.CancelledError:
            # The orchestration layer applies a stream_idle_timeout to each event
            # (asyncio.wait_for); an idle timeout throws CancelledError into this
            # generator's current await point. It is a BaseException, so without
            # handling it here it slips past the except Exception below and the
            # execution record stays in running forever (the frontend block also
            # stays running forever).
            # Do DB cleanup only here, with no yield - the consumer already left on
            # timeout, so yielding to a closed generator is meaningless; the
            # orchestration guard is responsible for sending the frontend completion
            # event.
            # Cleanup uses its own DB context, unaffected by self.db state; must
            # re-raise after cleanup.
            logger.warning(
                "流式 Agent 调用被取消（空闲超时或外部取消），收尾执行记录",
                extra={
                    "agent_execution_id": str(_agent_execution_id) if _agent_execution_id else None,
                    "content_length": len(full_content) if full_content else 0,
                },
            )
            try:
                await self.db.rollback()
            except Exception:
                pass
            if _agent_execution_id is not None:
                try:
                    await self._update_agent_execution_completed_async(
                        execution_id=_agent_execution_id,
                        steps=orchestrator_node_executions + node_executions,
                        status="failed",
                        elapsed_time=time.time() - start_time,
                        error_message="执行被取消（空闲超时或外部取消）",
                        agent_log=_trace if sub_agent else None,
                    )
                except Exception as update_err:
                    logger.error(
                        "流式执行取消收尾记录失败",
                        extra={
                            "execution_id": str(_agent_execution_id),
                            "error": str(update_err),
                        },
                    )

            raise

        except Exception as e:
            debug_id = self._build_debug_id()
            public_error = classify_multimodal_exception(e, debug_id=debug_id)
            if public_error is not None:
                compact_error = public_error
                logger.error(
                    "流式 Agent 多模态输入失败（已安全处理）",
                    extra={
                        "error_type": type(e).__name__,
                        "error_kind": compact_error["kind"],
                        "debug_id": debug_id,
                    },
                )
            else:
                # Non-target exceptions keep the existing handling and logging behavior.
                compact_error = self._build_compact_error(e, debug_id=debug_id)
                logger.error(
                    "流式 Agent 调用失败",
                    extra={"error": str(e), "debug_id": debug_id, "compact_error": compact_error},
                    exc_info=True,
                )
            try:
                await self.db.rollback()
            except Exception:
                pass
            # Save the failed messages so the frontend can show the failure state
            # With skip_save=True the caller handles the failure state; skip the
            # duplicate save inside run_stream
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
            # Mark the Agent execution record failed
            if _agent_execution_id is not None:
                try:
                    elapsed_time = time.time() - start_time
                    await self._update_agent_execution_completed_async(
                        execution_id=_agent_execution_id,
                        steps=orchestrator_node_executions + node_executions,
                        status="failed",
                        elapsed_time=elapsed_time,
                        error_message=json.dumps(compact_error, ensure_ascii=False)[:2000],
                        agent_log=_trace if sub_agent else None,
                    )
                except Exception:
                    pass
            # Send a completion event on sub Agent failure as well, so the frontend
            # block does not stay in running forever
            # (output carries whatever partial content was produced, to help judge
            # "where it died")
            if sub_agent:
                yield self._format_sse_event("agent_complete", {
                    "execution_id": str(_agent_execution_id) if _agent_execution_id else None,
                    "agent_id": (execution_owner or {}).get("agent_id"),
                    "agent_name": (execution_owner or {}).get("agent_name"),
                    "status": "failed",
                    "output": full_content,
                    "elapsed_time": time.time() - start_time,
                    "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": total_tokens},
                })
            # Send the error event
            yield self._format_sse_event("error", {
                "error": compact_error,
                "timestamp": time.time()
            })

    def _format_sse_event(self, event: str, data: Dict[str, Any]) -> str:
        """Format an SSE event.

        Args:
            event: Event type.
            data: Event data.

        Returns:
            str: SSE-formatted string.
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
                "callback_url": current_settings().e2b_callback_url,
                "callback_secret": current_settings().e2b_callback_secret.get_secret_value(),
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
        from ..tools.langchain_adapter import LangchainToolWrapper

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
        from .e2b_sandbox_service import get_sandbox_service

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
        """Fetch the model's API Key.

        Args:
            model_config_id: Model configuration ID.

        Returns:
            Dict: Dict containing model_name, api_key, api_base.

        Raises:
            BusinessException: When no API Key is available (model missing/deprecated/disabled/missing credentials).
        """
        async with get_async_db_context() as db:
            api_key = await ModelApiKeyService.resolve_runtime_api_key_bridge_or_raise_async(
                db,
                model_config_id,
                tenant_id=tenant_id,
            )

            if not api_key:
                await ModelConfigService.raise_model_unavailable_async(
                    db, model_config_id, tenant_id=tenant_id
                )

            return {
                "model_name": api_key.model_name,
                "provider": api_key.provider,
                "api_key": api_key.api_key,
                "api_base": api_key.api_base,
                "api_key_id": api_key.id,
                "input_modalities": list(getattr(api_key, "input_modalities", None) or []),
                "output_modalities": list(getattr(api_key, "output_modalities", None) or []),
                "features": list(getattr(api_key, "features", None) or []),
                # Legacy fields exist only in the sandbox payload scope
                "is_omni": api_key.is_omni,
                "capability": api_key.capability,
                "tenant_id": api_key.tenant_id,
                "model_config_id": api_key.model_config_id,
                "channel_id": api_key.channel_id,
                "failover_plan": api_key.failover_plan,
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
        """Ensure the conversation exists (create or validate).

        Args:
            conversation_id: Conversation ID (optional).
            app_id: Application ID.
            workspace_id: Workspace ID (required).
            user_id: User ID.
            sub_agent: Whether this is a sub agent.
            opening_statement: Opening statement (written as the first message for a new conversation).
            suggested_questions: Preset question list.

        Returns:
            str: Conversation ID.

        Raises:
            BusinessException: When the specified conversation does not exist.
        """
        # Create a new conversation when no conversation ID was provided
        if not conversation_id:
            logger.info(
                "创建新的草稿会话",
                extra={"workspace_id": str(workspace_id)}
            )

            # Fetch the config snapshot
            config_snapshot = await self._get_config_snapshot(app_id)

            # Create the new conversation
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

            # If there is an opening statement, write it to the DB as the first
            # assistant message
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

        # A conversation ID was provided; validate its existence and workspace ownership
        try:
            conv_uuid = uuid.UUID(conversation_id)
            async with get_async_db_context() as db:
                conversation = await db.get(Conversation, conv_uuid)
                if not conversation:
                    raise BusinessException(
                        f"会话不存在: {conversation_id}",
                        BizCode.NOT_FOUND,
                    )

                # Validate that the conversation belongs to the current workspace (or
                # to the source workspace of a shared application)
                # sub_agent internal calls skip validation (already validated upstream)
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

                    # Case 2: for sub_agent internal calls, workspace_id is the source
                    # application's workspace while the conversation was created by the
                    # share target; allow it as long as the conversation belongs to the
                    # same app
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
    ) -> List[Dict[str, str]]:
        """Load conversation history and adapt multimodal files to the current model config.

        Args:
            conversation_id: Conversation ID.
            max_history: Maximum number of history messages.
            current_provider: The current model's provider.

        Returns:
            List[Dict]: History message list.
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

                has_files = bool(history_files and current_provider)
                if has_files:
                    stored_provider = history_files.get("provider")

                    if stored_provider != current_provider:
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
        """Load history messages before the given time (for regeneration).

        Args:
            conversation_id: Conversation ID.
            before_time: Cutoff time (messages at this time are excluded).
            max_history: Maximum number of history messages.

        Returns:
            List[Dict]: History message list in the form [{"role": "user/assistant", "content": [...]}]
        """
        # Query messages before the given time
        async with get_async_db_context() as db:
            result = await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.created_at < before_time,  # only messages before the cutoff
                    Message.is_deleted.is_not(True),
                )
                .order_by(Message.created_at.asc())  # ascending order
                .limit(max_history)
            )
            # Snapshot values before leaving the session: the context manager rolls
            # back read-only transactions, which expires ORM instances on exit.
            history_msgs = [
                _snapshot_message(message) for message in result.scalars().all()
            ]

        # Convert to history format
        filtered_history = []
        for msg in history_msgs:
            msg_dict = {
                "role": msg.role,
                "content": [{"type": "text", "text": msg.content}]
            }
            # Handle multimodal files in user messages
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
            message_id: Optional[uuid.UUID] = None,
            user_message_id: Optional[uuid.UUID] = None
    ) -> Optional[str]:
        """Save conversation messages (the conversation is already ensured by _ensure_conversation).

        Args:
            conversation_id: Conversation ID.
            user_message: User message.
            assistant_message: AI reply message.
            app_id: Application ID (unused, kept for compatibility).
            user_id: User ID (unused, kept for compatibility).
            meta_data: Token usage.
            files: Raw file inputs.
            processed_files: Processed files.
            audio_url: Audio URL.
            citations: Citation source list.
            provider: Model provider.

        Returns:
            Optional[str]: Assistant message ID.
        """
        _ = (app_id, user_id)
        try:
            conv_uuid = uuid.UUID(conversation_id)

            # Save messages (the conversation already exists)
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
                        meta_map = {str(r.id): (r.file_name, r.file_size) for r in rows}
                for f in files:
                    name, size = f.name, f.size
                    if f.transfer_method.value == "local_file" and f.upload_file_id and (not name or not size):
                        meta = meta_map.get(str(f.upload_file_id))
                        if meta:
                            name = name or meta[0]
                            size = size or meta[1]
                    human_meta["files"].append(serialize_file_reference(f, name=name, size=size))

            # Save history_files, including provider info
            if processed_files:
                human_meta["history_files"] = {
                    "content": sanitize_processed_files_for_history(processed_files),
                    "provider": provider,
                }

            parent_message_id = await self._get_last_current_assistant_id_async(conv_uuid)
            # Save the assistant message (with audio_url and citations)
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
        """Fetch the current configuration snapshot.

        Args:
            app_id: Application ID.

        Returns:
            Dict: Configuration snapshot.
        """
        try:
            async with get_async_db_context() as db:
                stmt = select(AgentConfig).where(AgentConfig.app_id == app_id).limit(1)
                result = await db.execute(stmt)
                agent_cfg = result.scalar_one_or_none()

                if not agent_cfg:
                    return {}

                # Fetch the model configuration
                model_config = None
                if agent_cfg.default_model_config_id:
                    model_config = await db.get(ModelConfig, agent_cfg.default_model_config_id)

                # Build the snapshot (ensure every value is serializable; read all
                # attributes before the session closes)
                def safe_serialize(value):
                    if value is None:
                        return None
                    if isinstance(value, (str, int, float, bool)):
                        return value
                    if isinstance(value, (dict, list)):
                        return value
                    if hasattr(value, "model_dump"):
                        return value.model_dump()
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
            # For multi-Agent applications it is normal to have no direct AgentConfig
            logger.debug("获取配置快照失败（可能是多 Agent 应用）", exc_info=True, extra={"error": str(e)})
            return {}

    async def _generate_suggested_questions(
            self,
            features_config: Dict[str, Any],
            assistant_message: str,
            model_view: ModelInfo,
            effective_params: Dict[str, Any]
    ) -> List[str]:
        """Generate follow-up suggested questions per the suggested_questions_after_answer config."""
        _ = effective_params
        sq_config = features_config.get("suggested_questions_after_answer", {})
        if not isinstance(sq_config, dict) or not sq_config.get("enabled"):
            return []
        try:
            from langchain_core.messages import HumanMessage
            from ..model_clients import RedBearChatModel
            llm = RedBearChatModel.for_invoke(
                model_view, params={"temperature": 0.5, "max_tokens": 200}
            )
            prompt = (
                f"根据以下AI回复，生成3个用户可能继续追问的简短问题，每行一个，不加序号：\n\n{assistant_message}"
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            content = resp.content
            # Compatibility for content being a list (some models return structured content)
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
        """Register file metadata and return audio_url first; stream the audio content to storage in the background."""
        tts_config = features_config.get("text_to_speech", {})
        if not isinstance(tts_config, dict) or not tts_config.get("enabled"):
            return None
        if not text or not text.strip():
            return None

        from .file_storage_service import FileStorageService, generate_file_key

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

        # Write pending-status metadata first and return the URL immediately
        await self._create_tts_file_metadata_async(
            file_id=file_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            file_key=file_key,
            file_name=f"tts_{file_id}{file_ext}",
            file_ext=file_ext,
            content_type=content_type,
        )

        server_url = current_settings().file_local_server_url
        audio_url = f"{server_url}/storage/permanent/{file_id}"

        # Background task: stream-generate and write to storage, then update the status
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
        """Synthesize audio in parallel from a streaming text input.
        Returns (audio_url, task); audio_url is available immediately (pending state), and
        the file content is ready once task completes.
        The caller puts text chunks into text_queue and puts None when finished.
        The frontend can poll GET /storage/files/{file_id}/status to check whether the audio is ready.
        """
        tts_config = features_config.get("text_to_speech", {})
        if not isinstance(tts_config, dict) or not tts_config.get("enabled"):
            return None, None

        from .file_storage_service import FileStorageService, generate_file_key

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

        server_url = current_settings().file_local_server_url
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
        """OpenAI TTS: collect all text first, then synthesize as a stream (OpenAI does not support incremental input)."""
        from openai import AsyncOpenAI
        # Collect all text (the text stream is already outputting in parallel, so the
        # wait is short)
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
        """DashScope TTS: streaming text input, enabling true parallel synthesis."""
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
            """Take text from text_queue, split it into sentences, and feed them to the synthesizer."""
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
                # Split into sentences and feed them in
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
        """OpenAI-compatible TTS streaming generation; yields bytes chunks."""
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
        """DashScope TTS streaming generation; yields bytes chunks."""
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
        """Substitute variables in the text.

        Args:
            text: Original text.
            values: Variable values.
            definitions: Variable definitions.

        Returns:
            str: The text after substitution.
        """
        result = text

        # Build the variable definition map
        var_defs = {var["name"]: var for var in definitions}

        for var_name, var_value in values.items():
            # Check whether the variable is defined
            if var_name not in var_defs:
                logger.warning(f"未定义的变量: {var_name}")
                continue

            # Substitute the variable (multiple placeholder formats supported)
            placeholders = [
                f"{{{{{var_name}}}}}",  # {{var_name}}
                f"{{{var_name}}}",  # {var_name}
                f"${{{var_name}}}",  # ${var_name}
            ]

            for placeholder in placeholders:
                if placeholder in result:
                    result = result.replace(placeholder, str(var_value))

        return result

    # ==================== Multi-model compare draft run ====================

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
        """Multi-model compare draft run.

        Args:
            agent_config: Agent configuration.
            models: Model configuration list; each item contains model_config, parameters, label, model_config_id.
            message: User message.
            workspace_id: Workspace ID.
            conversation_id: Conversation ID.
            user_id: User ID.
            variables: Variable parameters.
            parallel: Whether to execute in parallel.
            timeout: Timeout in seconds.
            execution_mode: Execution mode (in_process / sandbox).

        Returns:
            Dict: Compare results.
        """
        logger.info(
            "多模型对比试运行",
            extra={
                "model_count": len(models),
                "parallel": parallel
            }
        )

        # Validate file uploads up front (same as inside run())
        features_config: dict = agent_config.features or {}
        if hasattr(features_config, 'model_dump'):
            features_config = features_config.model_dump()
        # self._validate_file_upload(features_config, files)

        async def run_single_model(model_info):
            """Run a single model."""
            try:
                start_time = time.time()

                # Temporarily override parameters (no deepcopy, to avoid SQLAlchemy
                # session issues)
                original_params = agent_config.model_parameters
                agent_config.model_parameters = model_info["parameters"]

                # Use the model's own conversation_id; fall back to the global one
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
                    # Restore the original parameters
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

        # Execute all models (in parallel or serially)
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

        # Aggregate statistics
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
        """Estimate the cost.

        Args:
            usage: Token usage.
            model_config: Model configuration.

        Returns:
            Optional[float]: Estimated cost in USD.
        """
        if not usage:
            return None

        _ = model_config

        # Simplified cost estimation: return None for now
        # TODO: implement cost estimation based on the model name or configuration
        # Need to fetch the actual model name from ModelApiKey, or add a model field to ModelConfig
        return None

    def _with_parameters(self, agent_config: AgentConfig, parameters: Dict[str, Any]) -> tuple[AgentConfig, Any]:
        """Create an agent_config with overridden parameters (shallow copy; only model_parameters changes).

        Args:
            agent_config: Original Agent configuration.
            parameters: Parameters to override.

        Returns:
            AgentConfig: The modified configuration (note: this is the same object; only model_parameters was temporarily changed).
        """
        # Save the original parameters
        original_params = agent_config.model_parameters
        # Set the new parameters
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
            skip_save: bool = False,
    ) -> AsyncGenerator[str, None]:
        """Multi-model compare draft run with streaming output.

        Modeled on run_compare; supports parallel or serial execution.

        Args:
            agent_config: Agent configuration.
            models: Model configuration list; each item contains model_config, parameters, label, model_config_id.
            message: User message.
            workspace_id: Workspace ID.
            conversation_id: Conversation ID.
            user_id: User ID.
            variables: Variable parameters.
            storage_type: Storage type.
            user_rag_memory_id: RAG memory ID.
            web_search: Whether to enable web search.
            memory: Whether to enable memory.
            parallel: Whether to execute in parallel.
            timeout: Timeout in seconds.
            files: Multimodal files.
            execution_mode: Execution mode (in_process / sandbox).

        Yields:
            str: SSE-formatted event data.
        """
        logger.info(
            "多模型对比流式试运行",
            extra={"model_count": len(models), "parallel": parallel}
        )

        # Validate file uploads up front
        # features_config: dict = agent_config.features or {}
        # if hasattr(features_config, 'model_dump'):
        #     features_config = features_config.model_dump()
        # self._validate_file_upload(features_config, files)

        # The compare_start user_message_id is only an overall identifier for this
        # compare run's user message, letting the frontend locate/delete the local
        # user message bubble; each model's own persisted user_message_id is returned
        # in its model_start event so the frontend can tell the compared models apart
        user_message_id = uuid.uuid4()
        # Send the start event
        yield self._format_sse_event("compare_start", {
            "conversation_id": conversation_id,
            "model_count": len(models),
            "parallel": parallel,
            "user_message_id": str(user_message_id),
            "timestamp": time.time()
        })

        results = []

        async def run_single_model_stream(idx: int, model_info: Dict[str, Any], event_queue: asyncio.Queue):
            """Run a single model (streaming) and put its events into the queue."""
            model_label = model_info["label"]
            model_config_id = str(model_info["model_config_id"])
            # Use the model's own conversation_id; fall back to the global one
            model_conversation_id = model_info.get("conversation_id") or conversation_id
            # Pre-generate a user_message_id per model and return it to the frontend
            # with model_start so the frontend can distinguish each model's user
            # message in the compare; also pass it down to run_stream as the
            # persisted id for that model's user message, keeping frontend and
            # backend consistent
            model_user_message_id = uuid.uuid4()

            try:
                # Send the model start event
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
                total_tokens = 0
                returned_conversation_id = model_conversation_id
                audio_url = None
                audio_status = None
                citations = []
                suggested_questions = []
                stream_error = None
                message_id = None

                # Temporarily override parameters
                original_params = agent_config.model_parameters
                agent_config.model_parameters = model_info["parameters"]

                try:
                    # Stream a single model
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
                            skip_save=skip_save,
                    ):
                        # Parse the raw event
                        try:
                            lines = event_str.strip().split('\n')
                            event_type = None
                            event_data = None

                            for line in lines:
                                if line.startswith('event: '):
                                    event_type = line[7:].strip()
                                elif line.startswith('data: '):
                                    event_data = json.loads(line[6:])

                            # Get the actual conversation_id from the start event
                            if event_type == "start" and event_data:
                                conv_id = event_data.get("conversation_id")
                                if conv_id:
                                    returned_conversation_id = conv_id

                            # Accumulate message content
                            if event_type == "message" and event_data:
                                chunk = event_data.get("content", "")
                                full_content += chunk

                                # Forward the message chunk event (with the model identifier)
                                await event_queue.put(self._format_sse_event("model_message", {
                                    "model_index": idx,
                                    "model_config_id": model_config_id,
                                    "label": model_label,
                                    "conversation_id": returned_conversation_id,
                                    "content": chunk
                                }))

                            # Forward the deep-thinking event (with the model identifier)
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

                            # Forward tool call events (with the model identifier)
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

                            # Extract feature output fields from the end event
                            if event_type == "end" and event_data:
                                audio_url = event_data.get("audio_url")
                                audio_status = event_data.get("audio_status")
                                citations = event_data.get("citations", [])
                                suggested_questions = event_data.get("suggested_questions", [])
                                message_id = event_data.get("message_id")
                                total_tokens = event_data.get("total_tokens", 0) or 0

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
                    # Restore the original parameters
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
                        "error": stream_error,
                        "user_message_id": str(model_user_message_id),
                    }

                # Build the result (modeled on run_compare)
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
                    "error": None,
                    "total_tokens": total_tokens,
                    "user_message_id": str(model_user_message_id),
                }

                # Send the model completion event
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
                    "error": compact_error,
                    "user_message_id": str(model_user_message_id),
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
                    "error": compact_error,
                    "user_message_id": str(model_user_message_id),
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
            # Execute all models in parallel (modeled on run_compare)
            logger.debug(f"并行执行 {len(models)} 个模型（流式）")

            # Create the event queue
            event_queue = asyncio.Queue()

            # Start parallel tasks for all models
            tasks = [
                asyncio.create_task(run_single_model_stream(idx, model_info, event_queue))
                for idx, model_info in enumerate(models)
            ]

            # Continuously take events from the queue and forward them
            completed_tasks = set()
            while len(completed_tasks) < len(tasks):
                try:
                    # Try to get an event from the queue
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                    yield event
                except TimeoutError:
                    # Check whether any task has finished
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

            # Drain the remaining events in the queue
            while not event_queue.empty():
                try:
                    event = event_queue.get_nowait()
                    yield event
                except asyncio.QueueEmpty:
                    break

        else:
            # Execute each model serially (modeled on run_compare)
            logger.debug(f"串行执行 {len(models)} 个模型（流式）")

            for idx, model_info in enumerate(models):
                # Create a temporary queue for the single model
                event_queue = asyncio.Queue()

                # Run the single model
                result = await run_single_model_stream(idx, model_info, event_queue)
                if result:
                    results.append(result)

                # Forward all events of that model
                while not event_queue.empty():
                    try:
                        event = event_queue.get_nowait()
                        yield event
                    except asyncio.QueueEmpty:
                        break

        # Aggregate statistics (modeled on run_compare)
        successful = [r for r in results if not r.get("error")]
        failed = [r for r in results if r.get("error")]

        fastest = min(successful, key=lambda x: x["elapsed_time"]) if successful else None
        cheapest = min(
            successful,
            key=lambda x: x.get("cost_estimate") or float("inf")
        ) if successful else None

        # Build the result summary (including the full message)
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

        # Enqueue per-model message saves via the batch persist queue.
        # The inner run_stream already persists user+assistant when skip_save=False;
        # only persist here when the inner call does not (skip_save=True); otherwise
        # the same user_message_id would get a second assistant message inserted
        # (previously hidden because the whole batch rolled back on a primary-key
        # conflict).
        if skip_save:
            from .batch_persist_queue import BatchPersistQueue, PersistTask

            # Build files_meta from FileInput list
            _files_meta: list[dict[str, Any]] = []
            if files:
                for f in files:
                    _files_meta.append({
                        "type": getattr(f, 'type', None),
                        "url": getattr(f, 'url', None),
                        "name": getattr(f, 'name', None),
                        "size": getattr(f, 'size', None),
                        "file_type": getattr(f, 'file_type', None),
                    })

            for r in successful:
                _conv_id = r.get("conversation_id")
                _user_msg_id = r.get("user_message_id")
                if _conv_id and _user_msg_id:
                    try:
                        await BatchPersistQueue.enqueue(PersistTask(
                            task_type="save_messages",
                            args={
                                "sync_memory": False,
                                "ctx": None,  # unused when overrides provided
                                "result": None,  # unused when overrides provided
                                "conversation_id_override": uuid.UUID(_conv_id) if isinstance(_conv_id, str) else _conv_id,
                                "user_message_id_override": uuid.UUID(_user_msg_id) if isinstance(_user_msg_id, str) else _user_msg_id,
                                "message_id_override": uuid.uuid4(),
                                "user_message_content": message,
                                "files_meta": _files_meta,
                                "content_override": r.get("message", ""),
                                "meta_override": {
                                    "usage": {
                                        "prompt_tokens": 0,
                                        "completion_tokens": 0,
                                        "total_tokens": r.get("total_tokens", 0) or 0,
                                    },
                                    "reasoning": r.get("reasoning_content") or "",
                                    "audio_url": r.get("audio_url"),
                                    "citations": r.get("citations", []),
                                    "suggested_questions": r.get("suggested_questions", []),
                                    "audio_status": r.get("audio_status"),
                                },
                            },
                        ))
                    except Exception:
                        logger.exception("Failed to enqueue compare model save for %s", r.get("label"))

        # Send the compare completion event (format modeled on run_compare's return)
        yield self._format_sse_event("compare_end", {
            "conversation_id": conversation_id,
            "results": results_summary,  # contains full results
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

    # ==================== Regeneration ====================

    async def _locate_or_restore_parent_user_message(
            self, original_msg: SimpleNamespace
    ) -> SimpleNamespace:
        """Locate the parent user message of the original assistant message.

        Lookup order:
        1. Prefer the already-backfilled parent_message_id (ignored as dirty data
           when the message it points to is not a user message);
        2. Otherwise walk back to the most recent user message in the same
           conversation whose created_at is earlier than or equal to the original
           message (not filtered by is_deleted, because that message is still the
           actual question even if deleted; <= rather than < covers the boundary
           case of user/assistant rows persisted in the same millisecond);
        3. If still not found, relax to the most recent user message in this
           conversation (fallback covering dirty data with out-of-order created_at).

        If the resolved parent message was soft-deleted (the user accidentally
        deleted the original question before regenerating), restore it automatically
        (is_deleted set back to False) and continue rather than raising
        "无法找到原始用户消息" - an accidental deletion of the question must not make
        the reply impossible to regenerate.
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
            # Fallback: the most recent user message in the conversation (regardless
            # of time order), covering dirty data with abnormal created_at
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
        """Regenerate a reply (multi-version support).

        Core flow:
        1. Fetch the original assistant message and its parent user message
        2. Mark the original assistant message as not current (is_current=False)
        3. Re-invoke the LLM with the same context
        4. Save a new assistant message version (version+1, is_current=True)

        Args:
            message_id: Message ID of the original AI reply.
            agent_config: Agent configuration.
            model_config: Model configuration.
            workspace_id: Workspace ID.
            user_id: User ID.
            storage_type: Storage type.
            user_rag_memory_id: RAG memory ID.

        Returns:
            Dict: Contains the new message ID, content, version number, etc.
        """
        # 1. Fetch the original message
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

        # 2. Mark the original version as not current
        await self._mark_message_not_current_async(message_id)
        original_msg.is_current = False

        # 3. Fetch the parent user message (to recover the original question; if it
        # was soft-deleted it is restored automatically, see
        # _locate_or_restore_parent_user_message)
        parent_msg = await self._locate_or_restore_parent_user_message(original_msg)
        parent_msg_id = parent_msg.id

        user_message_content = parent_msg.content if parent_msg else ""

        # 3.5 Extract file info from the parent message (if any)
        files = None
        if parent_msg and parent_msg.meta_data:
            meta_files = parent_msg.meta_data.get("files", [])
            if meta_files:
                # Convert the stored file info back to FileInput format
                files = []
                for f in meta_files:
                    try:
                        file_input = deserialize_file_reference(f)
                        files.append(file_input)
                    except Exception as e:
                        logger.warning(f"转换文件信息失败: {e}")
                        continue

        # 4. Load the context (up to the parent message, excluding the message being
        # regenerated)
        # Use the wrapped helper to load history before the parent message
        filtered_history = await self._load_history_before_message(
            conversation_id=conversation_uuid,
            before_time=parent_msg.created_at,
            max_history=current_settings().agent_max_history
        )

        # 5. Invoke the LLM (reusing the existing run method, passing filtered
        # history and files)
        result = await self.run(
            agent_config=agent_config,
            model_config=model_config,
            message=user_message_content,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            user_id=user_id,
            storage_type=storage_type,
            user_rag_memory_id=user_rag_memory_id,
            skip_save=True,  # skip saving; regenerate saves itself
            history=filtered_history,  # pass the filtered history
            files=files,  # pass the parent message's files
        )

        # 6. Save the new version message
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
        """Regenerate a reply with streaming output (multi-version support).

        Same core flow as regenerate, with streaming output.

        Args:
            message_id: Message ID of the original AI reply.
            agent_config: Agent configuration.
            model_config: Model configuration.
            workspace_id: Workspace ID.
            user_id: User ID.
            storage_type: Storage type.
            user_rag_memory_id: RAG memory ID.

        Yields:
            str: SSE-formatted event data.
        """
        # 1. Fetch the original message
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

        # 2. Mark the original version as not current
        await self._mark_message_not_current_async(message_id)
        original_msg.is_current = False

        # 3. Fetch the parent user message (to recover the original question; if it
        # was soft-deleted it is restored automatically, see
        # _locate_or_restore_parent_user_message)
        parent_msg = await self._locate_or_restore_parent_user_message(original_msg)
        parent_msg_id = parent_msg.id

        user_message_content = parent_msg.content if parent_msg else ""

        # 3.5 Extract file info from the parent message
        files = None
        if parent_msg and parent_msg.meta_data:
            meta_files = parent_msg.meta_data.get("files", [])
            if meta_files:
                from ..schemas.app_schema import FileInput, TransferMethod
                files = []
                for f in meta_files:
                    try:
                        file_input = deserialize_file_reference(f)
                        files.append(file_input)
                    except Exception as e:
                        logger.warning(f"转换文件信息失败: {e}")
                        continue

        # 4. Load context
        filtered_history = await self._load_history_before_message(
            conversation_id=conversation_uuid,
            before_time=parent_msg.created_at,
            max_history=current_settings().agent_max_history
        )

        # 5. Stream the LLM call
        full_content = ""
        full_reasoning = ""
        suggested_questions = []
        citations = []
        audio_url = None
        audio_status = None

        # Send the start event
        yield self._format_sse_event("start", {
            "conversation_id": conversation_id,
            "version": new_version,
            "timestamp": time.time()
        })

        # Stream via run_stream
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
            # Parse the event
            lines = event_str.strip().split('\n')
            event_type = None
            event_data = None

            for line in lines:
                if line.startswith('event: '):
                    event_type = line[7:].strip()
                elif line.startswith('data: '):
                    event_data = json.loads(line[6:])

            # Accumulate content and forward the event
            if event_type == "message" and event_data:
                full_content += event_data.get("content", "")
                yield event_str
            elif event_type == "reasoning" and event_data:
                full_reasoning += event_data.get("content", "")
                yield event_str
            elif event_type == "tool_start" or event_type == "tool_end" or event_type == "tool_error":
                yield event_str
            elif event_type in ("agent_log", "agent_log_final"):
                # The regenerate endpoint does not return agent execution trace
                # events to the frontend; drop them
                continue
            elif event_type == "end" and event_data:
                # Extract feature outputs from the end event
                suggested_questions = event_data.get("suggested_questions", [])
                audio_url = event_data.get("audio_url")
                audio_status = event_data.get("audio_status")
                citations = event_data.get("citations", [])

        # 6. Save the new version message
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

        # Send the end event
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

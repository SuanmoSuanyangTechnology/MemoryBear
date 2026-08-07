import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import redis
from celery import states
from celery.exceptions import Ignore, Retry
from elasticsearch import AsyncElasticsearch
from fastapi.encoders import jsonable_encoder
from redis.exceptions import RedisError
from sqlalchemy import select, cast, String

from app.aioRedis import get_thread_safe_redis
from app.celery_app import celery_app
from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.models import RedBearEmbeddings, RedBearLLM
from app.core.memory.storage_services.reflection_engine import retry_registry as rr
from app.core.rag.chunk.hierarchy import GroupedChildChunks, validate_parent_child_result
from app.core.rag.chunk.metadata import merge_parser_metadata
from app.core.rag.chunk.parser.image_storage import cleanup_mineru_v3_images
from app.core.rag.crawler.web_crawler import WebCrawler
from app.core.rag.graphrag.general.index import init_graphrag, run_graphrag_for_kb
from app.core.rag.graphrag.utils import get_llm_cache, set_llm_cache
from app.core.rag.knowledge_graph.config import (
    GraphPipeline,
    GraphPipelineConfigError,
    is_graph_enabled,
    resolve_graph_pipeline,
)
from app.core.rag.knowledge_graph.dispatch import dispatch_document_graph_sync
from app.core.rag.knowledge_graph.elasticsearch_store import GraphElasticsearchStore
from app.core.rag.knowledge_graph.extractor import LLMEntityRelationExtractor
from app.core.rag.knowledge_graph.index_pipeline import KnowledgeGraphIndexPipeline
from app.core.rag.knowledge_graph.lock import create_knowledge_graph_lock
from app.core.rag.knowledge_graph.rebuild_task_guard import (
    acquire_rebuild_execution,
    has_rebuild_terminal,
    mark_rebuild_terminal,
    refresh_rebuild_job,
    release_rebuild_execution,
    release_rebuild_job,
)
from app.core.rag.knowledge_graph.runtime import (
    build_model_config,
    snapshot_graph_runtime,
)
from app.core.rag.parser_config import set_graph_pipeline_for_migration
from app.core.rag.retrieval.async_elasticsearch import (
    build_async_elasticsearch_client_config,
)
from app.core.rag.integrations.feishu.client import FeishuAPIClient
from app.core.rag.integrations.feishu.models import FileInfo
from app.core.rag.integrations.yuque.client import YuqueAPIClient
from app.core.rag.integrations.yuque.models import YuqueDocInfo
from app.core.rag.llm.chat_model import Base
from app.core.rag.llm.cv_model import QWenCV
from app.core.rag.llm.embedding_model import OpenAIEmbed
from app.core.rag.llm.sequence2txt_model import QWenSeq2txt
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.prompts.generator import qa_proposal
from app.core.rag.utils.chunk_write_order import (
    pop_vectorized_bootstrap_batch,
    prioritize_vectorized_chunks,
)
from app.core.rag.utils.redis_conn import REDIS_CONN
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import (
    ElasticSearchVectorFactory,
)
# Import a unified Celery instance
from app.core.utils.datetime_utils import (
    as_utc_aware,
    parse_iso_to_utc_naive,
    to_iso_z,
    to_timestamp_ms,
    utcnow,
    utcnow_naive,
)
from app.db import get_db_context, get_db_read
from app.models import App, AppRelease, Document, File, Knowledge, User, Workspace
from app.models.file_model import FILE_ROLE_SOURCE
from app.models.end_user_model import EndUser
from app.models.models_model import ModelType
from app.repositories.end_user_repository import get_end_users_by_workspace
from app.schemas import document_schema, file_schema
from app.services.memory_config_service import MemoryConfigService
from app.services.memory_forget_service import MemoryForgetService
from app.services.model_service import ModelApiKeyService
from app.utils.redis_lock import RedisFairLock

logger = get_logger(__name__)

# ── 预编译文件类型正则 & 常量 ──────────────────────────────────
AUDIO_PATTERN = re.compile(
    r"\.(da|wave|wav|mp3|aac|flac|ogg|aiff|au|midi|wma|realaudio|vqf|oggvorbis|ape?)$",
    re.IGNORECASE,
)
VIDEO_PATTERN = re.compile(
    r"\.(mp4|mov|avi|flv|mpeg|mpg|webm|wmv|3gp|3gpp|mkv?)$",
    re.IGNORECASE,
)
DEFAULT_PARSE_LANGUAGE = "Chinese"
DEFAULT_PARSE_TO_PAGE = 100_000
EMBEDDING_BATCH_SIZE = settings.EMBEDDING_BATCH_SIZE
# Embedding 并发写入的最大线程数，需根据模型 API rate limit 调整
EMBEDDING_MAX_WORKERS = int(os.getenv("EMBEDDING_MAX_WORKERS", "3"))
# auto_questions LLM 并发调用的最大线程数
AUTO_QUESTIONS_MAX_WORKERS = int(os.getenv("AUTO_QUESTIONS_MAX_WORKERS", "5"))
# 文档解析页数上限
MAX_DOCUMENT_PAGES = int(os.getenv("MAX_DOCUMENT_PAGES", "200"))


@dataclass(frozen=True)
class _GraphTaskState:
    knowledge_id: str
    workspace_id: str
    pipeline: GraphPipeline
    graph_enabled: bool
    document_active: bool | None
    active_document_ids: tuple[str, ...]
    fingerprint: str


class _GraphDocumentDeletionPending(RuntimeError):
    """The graph cleanup must wait until the document deletion is committed."""


def _document_active_state(document: Document | None) -> bool | None:
    if document is None:
        return None
    return document.status == 1


def _canonical_graph_uuid(value: object, field_name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise GraphPipelineConfigError(
            f"invalid {field_name}: {value}"
        ) from exc


def _graph_task_fingerprint(knowledge: Knowledge) -> str:
    import hashlib

    graph_config = (knowledge.parser_config or {}).get("graphrag")
    payload = {
        "llm_id": str(knowledge.llm_id) if knowledge.llm_id else None,
        "embedding_id": (
            str(knowledge.embedding_id) if knowledge.embedding_id else None
        ),
        "graphrag": graph_config,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_graph_task_state(
        knowledge_id: str,
        document_id: str | None = None,
        *,
        include_active_documents: bool = False,
) -> _GraphTaskState:
    knowledge_uuid = uuid.UUID(
        _canonical_graph_uuid(knowledge_id, "knowledge id")
    )
    document_uuid = (
        uuid.UUID(_canonical_graph_uuid(document_id, "document id"))
        if document_id
        else None
    )

    with get_db_context() as db:
        knowledge = db.query(Knowledge).filter(
            Knowledge.id == knowledge_uuid
        ).first()
        if knowledge is None:
            raise GraphPipelineConfigError(
                f"knowledge does not exist: {knowledge_id}"
            )
        workspace = db.query(Workspace).filter(
            Workspace.id == knowledge.workspace_id
        ).first()
        if workspace is None:
            raise GraphPipelineConfigError(
                f"workspace does not exist: {knowledge.workspace_id}"
            )

        document_active: bool | None = None
        if document_uuid is not None:
            document = db.query(Document).filter(
                Document.id == document_uuid,
                Document.kb_id == knowledge_uuid,
            ).first()
            document_active = _document_active_state(document)

        active_document_ids: tuple[str, ...] = ()
        if include_active_documents:
            documents = db.query(Document).filter(
                Document.kb_id == knowledge_uuid,
                Document.status == 1,
                Document.chunk_num > 0,
            ).order_by(Document.id).all()
            active_document_ids = tuple(str(document.id) for document in documents)

        return _GraphTaskState(
            knowledge_id=str(knowledge.id),
            workspace_id=str(workspace.id),
            pipeline=resolve_graph_pipeline(knowledge.parser_config),
            graph_enabled=is_graph_enabled(knowledge.parser_config),
            document_active=document_active,
            active_document_ids=active_document_ids,
            fingerprint=_graph_task_fingerprint(knowledge),
        )


def _build_evidence_index_pipeline(runtime, client, lock_guard):
    llm_type = (
        ModelType.CHAT
        if runtime.llm.model_type == ModelType.CHAT.value
        else ModelType.LLM
    )
    llm = RedBearLLM(build_model_config(runtime.llm), type=llm_type)
    embedding = RedBearEmbeddings(build_model_config(runtime.embedding))
    extractor = LLMEntityRelationExtractor(
        llm,
        runtime.entity_types,
        runtime.scene_name,
    )
    return KnowledgeGraphIndexPipeline(
        store=GraphElasticsearchStore(client),
        extractor=extractor,
        embedding=embedding,
        lock_guard=lock_guard,
    )


async def _run_evidence_document_async(
        runtime,
        document_id: str,
        document_active: bool,
        lock_guard,
) -> None:
    client = AsyncElasticsearch(**build_async_elasticsearch_client_config())
    try:
        pipeline = _build_evidence_index_pipeline(runtime, client, lock_guard)
        await pipeline.sync_document(runtime, document_id, document_active)
    finally:
        await client.close()


async def _run_evidence_rebuild_async(runtime, active_document_ids, lock_guard) -> None:
    client = AsyncElasticsearch(**build_async_elasticsearch_client_config())
    try:
        pipeline = _build_evidence_index_pipeline(runtime, client, lock_guard)
        await pipeline.rebuild_knowledge(runtime, active_document_ids)
    finally:
        await client.close()


async def _run_evidence_clear_async(
        graph_index_name: str,
        knowledge_id: str,
        lock_guard,
        *,
        clear_all: bool,
) -> None:
    client = AsyncElasticsearch(**build_async_elasticsearch_client_config())
    try:
        store = GraphElasticsearchStore(client)
        if clear_all:
            await store.clear_all_graph_documents(
                graph_index_name,
                knowledge_id,
                ensure_valid=lock_guard.ensure_valid,
            )
        else:
            await store.clear_evidence_graph(
                graph_index_name,
                knowledge_id,
                ensure_valid=lock_guard.ensure_valid,
            )
    finally:
        await client.close()


def _execute_evidence_document(
        state: _GraphTaskState,
        document_id: str,
        lock_guard,
        *,
        document_active: bool,
) -> None:
    runtime = snapshot_graph_runtime(state.knowledge_id)
    asyncio.run(
        _run_evidence_document_async(
            runtime,
            str(document_id),
            document_active,
            lock_guard,
        )
    )


def _execute_evidence_rebuild(state: _GraphTaskState, lock_guard) -> None:
    runtime = snapshot_graph_runtime(state.knowledge_id)
    asyncio.run(
        _run_evidence_rebuild_async(
            runtime,
            state.active_document_ids,
            lock_guard,
        )
    )


def _execute_evidence_clear(
        state: _GraphTaskState,
        lock_guard,
        *,
        clear_all: bool,
) -> None:
    asyncio.run(
        _run_evidence_clear_async(
            f"graphrag_{state.workspace_id}",
            state.knowledge_id,
            lock_guard,
            clear_all=clear_all,
        )
    )


def _commit_evidence_pipeline(
        knowledge_id: str,
        expected_fingerprint: str,
) -> None:
    knowledge_uuid = uuid.UUID(
        _canonical_graph_uuid(knowledge_id, "knowledge id")
    )
    with get_db_context() as db:
        knowledge = db.query(Knowledge).filter(
            Knowledge.id == knowledge_uuid
        ).with_for_update().first()
        if knowledge is None:
            raise GraphPipelineConfigError(
                f"knowledge does not exist: {knowledge_id}"
            )
        if resolve_graph_pipeline(knowledge.parser_config) is GraphPipeline.EVIDENCE:
            return
        if _graph_task_fingerprint(knowledge) != expected_fingerprint:
            raise RuntimeError(
                "graph migration inputs changed while rebuilding"
            )
        knowledge.parser_config = set_graph_pipeline_for_migration(
            knowledge.parser_config,
            GraphPipeline.EVIDENCE,
        )
        db.commit()


def _run_evidence_graph_document(
        knowledge_id: str,
        document_id: str,
        *,
        document_deleted: bool = False,
) -> dict[str, Any]:
    knowledge_id = _canonical_graph_uuid(knowledge_id, "knowledge id")
    document_id = _canonical_graph_uuid(document_id, "document id")
    with create_knowledge_graph_lock(knowledge_id) as lock_guard:
        lock_guard.ensure_valid()
        state = _load_graph_task_state(knowledge_id, document_id)
        if state.pipeline is not GraphPipeline.EVIDENCE:
            return {"status": "skipped", "reason": "pipeline_changed"}
        if not state.graph_enabled:
            return {"status": "skipped", "reason": "graph_disabled"}
        if document_deleted and state.document_active is not None:
            raise _GraphDocumentDeletionPending(
                "document deletion has not been committed"
            )
        _execute_evidence_document(
            state,
            document_id,
            lock_guard,
            document_active=(
                False if document_deleted else bool(state.document_active)
            ),
        )
        lock_guard.ensure_valid()
        return {
            "status": "completed",
            "knowledge_id": knowledge_id,
            "document_id": document_id,
        }


def _run_evidence_graph_rebuild(knowledge_id: str) -> dict[str, Any]:
    knowledge_id = _canonical_graph_uuid(knowledge_id, "knowledge id")
    with create_knowledge_graph_lock(knowledge_id) as lock_guard:
        lock_guard.ensure_valid()
        state = _load_graph_task_state(
            knowledge_id,
            include_active_documents=True,
        )
        if state.pipeline is not GraphPipeline.EVIDENCE:
            return {"status": "skipped", "reason": "pipeline_changed"}
        if not state.graph_enabled:
            return {"status": "skipped", "reason": "graph_disabled"}
        _execute_evidence_rebuild(state, lock_guard)
        lock_guard.ensure_valid()
        return {"status": "completed", "knowledge_id": knowledge_id}


def _run_evidence_graph_migration(knowledge_id: str) -> dict[str, Any]:
    knowledge_id = _canonical_graph_uuid(knowledge_id, "knowledge id")
    with create_knowledge_graph_lock(knowledge_id) as lock_guard:
        lock_guard.ensure_valid()
        state = _load_graph_task_state(
            knowledge_id,
            include_active_documents=True,
        )
        if state.pipeline is GraphPipeline.EVIDENCE:
            return {"status": "already_evidence", "knowledge_id": knowledge_id}

        if state.graph_enabled and state.active_document_ids:
            _execute_evidence_rebuild(state, lock_guard)
        else:
            _execute_evidence_clear(state, lock_guard, clear_all=False)
        lock_guard.ensure_valid()
        _commit_evidence_pipeline(knowledge_id, state.fingerprint)
        lock_guard.ensure_valid()
        return {"status": "migrated", "knowledge_id": knowledge_id}


def _run_clear_all_knowledge_graph_data(
        knowledge_id: str,
        *,
        force: bool = False,
) -> dict[str, Any]:
    knowledge_id = _canonical_graph_uuid(knowledge_id, "knowledge id")
    with create_knowledge_graph_lock(knowledge_id) as lock_guard:
        lock_guard.ensure_valid()
        state = _load_graph_task_state(knowledge_id)
        if state.graph_enabled and not force:
            return {"status": "skipped", "reason": "graph_reenabled"}
        _execute_evidence_clear(state, lock_guard, clear_all=True)
        lock_guard.ensure_valid()
        return {"status": "cleared", "knowledge_id": knowledge_id}


def _graph_task_retry_countdown(task) -> int:
    return min(300, 2 ** int(task.request.retries or 0))


def _redacted_graph_exc_info(exc: Exception):
    redacted = RuntimeError(f"{type(exc).__name__}: message redacted")
    return type(redacted), redacted, exc.__traceback__


def _run_observed_graph_task(
        task,
        *,
        task_name: str,
        knowledge_id: str,
        operation: Callable[[], dict[str, Any]],
        document_id: str | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    task_id = str(getattr(task.request, "id", None) or "unknown")
    retry = int(getattr(task.request, "retries", 0) or 0)
    document_field = (
        f" document_id={document_id}" if document_id is not None else ""
    )
    logger.info(
        "[EvidenceGraph] task_start"
        " task=%s task_id=%s kb_id=%s%s retry=%d",
        task_name,
        task_id,
        str(knowledge_id),
        document_field,
        retry,
    )
    try:
        result = operation()
    except GraphPipelineConfigError as exc:
        logger.error(
            "[EvidenceGraph] task_failed"
            " task=%s task_id=%s kb_id=%s%s"
            " status=failure error_type=%s retry=%d elapsed_ms=%d",
            task_name,
            task_id,
            str(knowledge_id),
            document_field,
            type(exc).__name__,
            retry,
            int((time.perf_counter() - started_at) * 1000),
        )
        raise
    except Exception as exc:
        countdown = _graph_task_retry_countdown(task)
        retry_options = {}
        if isinstance(exc, _GraphDocumentDeletionPending):
            retry_options["max_retries"] = 8
        logger.warning(
            "[EvidenceGraph] task_retry"
            " task=%s task_id=%s kb_id=%s%s"
            " error_type=%s retry=%d countdown=%d elapsed_ms=%d",
            task_name,
            task_id,
            str(knowledge_id),
            document_field,
            type(exc).__name__,
            retry,
            countdown,
            int((time.perf_counter() - started_at) * 1000),
            exc_info=_redacted_graph_exc_info(exc),
        )
        raise task.retry(
            exc=exc,
            countdown=countdown,
            **retry_options,
        )

    status_value = str(result.get("status") or "completed")
    is_skip = status_value in {"skipped", "already_evidence"}
    reason = str(
        result.get("reason")
        or ("idempotent" if status_value == "already_evidence" else "none")
    )
    logger.info(
        "[EvidenceGraph] %s"
        " task=%s task_id=%s kb_id=%s%s"
        " status=%s reason=%s elapsed_ms=%d",
        "task_skip" if is_skip else "task_done",
        task_name,
        task_id,
        str(knowledge_id),
        document_field,
        status_value,
        reason,
        int((time.perf_counter() - started_at) * 1000),
    )
    return result


def _log_coalesced_rebuild_task(
        *,
        task_id: str,
        knowledge_id: str,
        retry: int,
        reason: str,
) -> None:
    logger.info(
        "[EvidenceGraph] task_coalesced"
        " task=rebuild_knowledge task_id=%s kb_id=%s retry=%d reason=%s",
        task_id,
        knowledge_id,
        retry,
        reason,
    )


def _retry_rebuild_guard_failure(
        task,
        *,
        task_id: str,
        knowledge_id: str,
        retry: int,
        exc: Exception,
):
    countdown = _graph_task_retry_countdown(task)
    logger.warning(
        "[EvidenceGraph] task_guard_retry"
        " task=rebuild_knowledge task_id=%s kb_id=%s"
        " error_type=%s retry=%d countdown=%d",
        task_id,
        knowledge_id,
        type(exc).__name__,
        retry,
        countdown,
        exc_info=_redacted_graph_exc_info(exc),
    )
    raise task.retry(exc=exc, countdown=countdown)


def _release_rebuild_attempt(
        knowledge_id: str,
        owner_token: str,
) -> None:
    try:
        released = release_rebuild_execution(knowledge_id, owner_token)
        if not released:
            logger.warning(
                "[EvidenceGraph] task_guard_release_skipped"
                " task=rebuild_knowledge kb_id=%s guard=execution",
                knowledge_id,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[EvidenceGraph] task_guard_release_failed"
            " task=rebuild_knowledge kb_id=%s guard=execution"
            " error_type=%s",
            knowledge_id,
            type(exc).__name__,
        )


def _finish_rebuild_task_guard(
        *,
        task_id: str,
        knowledge_id: str,
        owner_token: str,
        terminal: str,
) -> None:
    try:
        mark_rebuild_terminal(task_id, terminal)
    finally:
        _release_rebuild_attempt(knowledge_id, owner_token)
    try:
        released = release_rebuild_job(knowledge_id, task_id)
        if not released:
            logger.warning(
                "[EvidenceGraph] task_guard_release_skipped"
                " task=rebuild_knowledge task_id=%s kb_id=%s guard=job",
                task_id,
                knowledge_id,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "[EvidenceGraph] task_guard_release_failed"
            " task=rebuild_knowledge task_id=%s kb_id=%s guard=job"
            " error_type=%s",
            task_id,
            knowledge_id,
            type(exc).__name__,
        )


def _run_guarded_evidence_graph_rebuild(
        task,
        knowledge_id: str,
) -> dict[str, Any]:
    knowledge_id = _canonical_graph_uuid(knowledge_id, "knowledge id")
    task_id = str(getattr(task.request, "id", None) or "unknown")
    retry = int(getattr(task.request, "retries", 0) or 0)
    owner_token = f"{task_id}:{uuid.uuid4()}"

    try:
        if has_rebuild_terminal(task_id):
            _log_coalesced_rebuild_task(
                task_id=task_id,
                knowledge_id=knowledge_id,
                retry=retry,
                reason="terminal",
            )
            raise Ignore()
        if not refresh_rebuild_job(knowledge_id, task_id):
            _log_coalesced_rebuild_task(
                task_id=task_id,
                knowledge_id=knowledge_id,
                retry=retry,
                reason="foreign_job",
            )
            raise Ignore()
        if not acquire_rebuild_execution(knowledge_id, owner_token):
            _log_coalesced_rebuild_task(
                task_id=task_id,
                knowledge_id=knowledge_id,
                retry=retry,
                reason="active_attempt",
            )
            raise Ignore()
    except Ignore:
        raise
    except Exception as exc:  # noqa: BLE001
        _retry_rebuild_guard_failure(
            task,
            task_id=task_id,
            knowledge_id=knowledge_id,
            retry=retry,
            exc=exc,
        )

    try:
        task.update_state(
            state=states.STARTED,
            meta={
                "knowledge_id": knowledge_id,
                "retry": retry,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _release_rebuild_attempt(knowledge_id, owner_token)
        _retry_rebuild_guard_failure(
            task,
            task_id=task_id,
            knowledge_id=knowledge_id,
            retry=retry,
            exc=exc,
        )

    try:
        result = _run_observed_graph_task(
            task,
            task_name="rebuild_knowledge",
            knowledge_id=knowledge_id,
            operation=lambda: _run_evidence_graph_rebuild(knowledge_id),
        )
    except Retry:
        _release_rebuild_attempt(knowledge_id, owner_token)
        raise
    except Exception:
        _finish_rebuild_task_guard(
            task_id=task_id,
            knowledge_id=knowledge_id,
            owner_token=owner_token,
            terminal="failure",
        )
        raise

    try:
        _finish_rebuild_task_guard(
            task_id=task_id,
            knowledge_id=knowledge_id,
            owner_token=owner_token,
            terminal="success",
        )
    except Exception as exc:  # noqa: BLE001
        _retry_rebuild_guard_failure(
            task,
            task_id=task_id,
            knowledge_id=knowledge_id,
            retry=retry,
            exc=exc,
        )
    return result


def _resolve_model_api_key(db, model_config_id, tenant_id, role: str):
    if not model_config_id:
        raise RuntimeError(f"{role} model config is unavailable")
    api_key = ModelApiKeyService.get_available_api_key(db, model_config_id, tenant_id=tenant_id)
    if not api_key:
        raise RuntimeError(f"No available {role} api key found")
    return api_key


def _build_llm_config(db, llm_id, tenant_id):
    llm_key = _resolve_model_api_key(db, llm_id, tenant_id, "llm")
    return {
        "key": llm_key.api_key,
        "model_name": llm_key.model_name,
        "base_url": llm_key.api_base,
    }


def _build_chat_model(db, llm_id, tenant_id):
    llm_key = _resolve_model_api_key(db, llm_id, tenant_id, "llm")
    return Base(
        key=llm_key.api_key,
        model_name=llm_key.model_name,
        base_url=llm_key.api_base,
    )


def _build_embedding_model(db, embedding_id, tenant_id):
    embedding_key = _resolve_model_api_key(db, embedding_id, tenant_id, "embedding")
    return OpenAIEmbed(
        key=embedding_key.api_key,
        model_name=embedding_key.model_name,
        base_url=embedding_key.api_base,
    )


def _get_estimated_pages(file_name: str, file_binary: bytes) -> int | None:
    """快速获取 PDF 页数，失败返回 None（不阻断）"""
    ext = os.path.splitext(file_name)[1].lower()
    try:
        if ext == ".pdf":
            from app.core.rag.deepdoc.parser.pdf_parser import RAGPdfParser
            return RAGPdfParser.total_page_number("", binary=file_binary)
    except Exception:
        pass
    return None


# Redis keys for document parse task tracking
_PARSE_TASK_KEY = "doc:{doc_id}:parse_task"
_PARSE_CANCEL_KEY = "doc:{doc_id}:parse_cancel"
_PARSE_TASK_TTL = 7200


def _progress_ts() -> str:
    return to_iso_z(utcnow())


def _download_storage_file(file_key: str) -> bytes:
    from app.services.file_storage_service import FileStorageService

    storage_service = FileStorageService()

    async def _download():
        return await storage_service.download_file(file_key)

    try:
        return asyncio.run(_download())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_download())
        finally:
            loop.close()


def _upload_kb_file_content_sync(kb_id: uuid.UUID, file_id: uuid.UUID, file_ext: str, content: bytes) -> str:
    from app.services.file_storage_service import FileStorageService, generate_kb_file_key

    file_key = generate_kb_file_key(kb_id=kb_id, file_id=file_id, file_ext=file_ext)
    storage_service = FileStorageService()

    async def _upload():
        await storage_service.storage.upload(
            file_key=file_key,
            content=content,
            content_type="application/octet-stream",
        )

    try:
        asyncio.run(_upload())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_upload())
        finally:
            loop.close()
    return file_key


def _dispatch_parse_document(file_key: str | None, document_id: uuid.UUID, file_name: str) -> str | None:
    if not file_key:
        logger.warning(f"[ParseDoc] skip dispatch because file_key is empty: document={document_id}")
        return None

    task_key = _PARSE_TASK_KEY.format(doc_id=document_id)
    claimed = False
    try:
        claimed = bool(REDIS_CONN.REDIS.set(task_key, "CLAIMED", ex=_PARSE_TASK_TTL, nx=True))
    except Exception:
        logger.warning(f"[ParseDoc] failed to claim parse task: document={document_id}", exc_info=True)
        return None

    if not claimed:
        existing_task_id = REDIS_CONN.get(task_key)
        logger.info(f"[ParseDoc] parse already running: document={document_id}, task_id={existing_task_id}")
        return existing_task_id

    try:
        task = celery_app.send_task(
            "app.core.rag.tasks.parse_document",
            args=[file_key, str(document_id), file_name],
        )
    except Exception:
        try:
            REDIS_CONN.delete(task_key)
        except Exception:
            logger.warning(f"[ParseDoc] failed to rollback parse claim: document={document_id}", exc_info=True)
        raise

    try:
        REDIS_CONN.set(task_key, task.id, exp=_PARSE_TASK_TTL)
    except Exception:
        logger.warning(f"[ParseDoc] failed to record parse task id: document={document_id}", exc_info=True)
    return task.id


# 模块级同步 Redis 连接池，供 Celery 任务共享使用
# 连接 CELERY_BACKEND DB，与 write_message:last_done 时间戳写入保持一致
# 使用连接池而非单例客户端，提供更好的并发性能和自动重连
_sync_redis_pool: redis.ConnectionPool | None = None


def _get_or_create_redis_pool() -> redis.ConnectionPool | None:
    """获取或创建 Redis 连接池（懒初始化）"""
    global _sync_redis_pool
    if _sync_redis_pool is None:
        try:
            _sync_redis_pool = redis.ConnectionPool(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB_CELERY_BACKEND,
                password=settings.REDIS_PASSWORD,
                decode_responses=True,
                max_connections=100,
                socket_connect_timeout=5,
                socket_timeout=10,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            logger.info("Redis connection pool created for Celery tasks")
        except Exception as e:
            logger.error(f"Failed to create Redis connection pool: {e}", exc_info=True)
            return None
    return _sync_redis_pool


def get_sync_redis_client() -> Optional[redis.StrictRedis]:
    """获取同步 Redis 客户端（使用连接池）

    依赖连接池本身的 ``health_check_interval=30`` 做健康检查；
    每次取客户端不再发 ``PING``，避免在热路径上多一次 RTT。
    冷启动应通过 ``warmup_sync_redis_pool`` 预热，避免首次请求承担建池+握手成本。

    Returns:
        redis.StrictRedis: Redis 客户端实例；当连接池创建失败时返回 None。
    """
    try:
        pool = _get_or_create_redis_pool()
        if pool is None:
            return None
        return redis.StrictRedis(connection_pool=pool)
    except RedisError as e:
        logger.error(f"Redis connection failed: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting Redis client: {e}", exc_info=True)
        return None


def warmup_sync_redis_pool() -> bool:
    """应用启动时预热 Redis 连接池。

    复用 ``get_sync_redis_client`` 构造客户端，再发一次 ``PING`` 完成 TCP 握手，
    把"首次请求需要建池"的 50–200ms 冷启动开销前置到启动阶段。
    任何失败都只记录日志，不影响进程启动。

    Returns:
        bool: 预热成功返回 True；失败或 Redis 不可用返回 False。
    """
    try:
        client = get_sync_redis_client()
        if client is None:
            return False
        client.ping()
        logger.info("Sync Redis pool warmed up (PING ok)")
        return True
    except RedisError as e:
        logger.warning(f"Sync Redis pool warmup failed: {e}")
        return False
    except Exception as e:
        logger.warning(f"Unexpected error warming Sync Redis pool: {e}")
        return False


def set_asyncio_event_loop():
    """Ensure an open asyncio event loop exists for the current thread.

    Reuses the existing event loop if one is available and still open.
    Creates and installs a new event loop only when the current one is
    closed or missing (e.g. after ``_shutdown_loop_gracefully``).
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


def _shutdown_loop_gracefully(loop: asyncio.AbstractEventLoop):
    """Cancel pending tasks and finalize async generators, but keep the loop open for reuse.

    Not closing the loop avoids 'Event loop is closed' from httpx AsyncClient.__del__ during GC.
    """
    try:
        # Cancel remaining tasks to prevent leaks between Celery tasks
        all_tasks = asyncio.all_tasks(loop)
        if all_tasks:
            for task in all_tasks:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*all_tasks, return_exceptions=True))
        # Finalize async generators so network/client resources are properly cleaned up.
        # This does NOT close the loop.
        loop.run_until_complete(loop.shutdown_asyncgens())
    except Exception:
        pass


@celery_app.task(name="tasks.process_item")
def process_item(item: dict):
    """
    A simulated long-running task that processes an item.
    In a real-world scenario, this could be anything:
    - Sending an email
    - Generating a report
    - Performing a complex calculation
    - Calling a third-party API
    """
    print(f"Processing item: {item['name']}")
    # Simulate work for 5 seconds
    time.sleep(5)
    result = f"Item '{item['name']}' processed successfully at a price of ${item['price']}."
    print(result)
    return result


def _build_image_vision_model(db, image2text_id, tenant_id):
    """Build the knowledge-base image-to-text model for document parsing."""
    image2text_key = _resolve_model_api_key(db, image2text_id, tenant_id, "image2text")
    return QWenCV(
        key=image2text_key.api_key,
        model_name=image2text_key.model_name,
        lang=DEFAULT_PARSE_LANGUAGE,
        base_url=image2text_key.api_base,
    )


def _build_media_model(file_path: str):
    """Build the existing audio or video model when the file type requires one."""
    if AUDIO_PATTERN.search(file_path):
        omni_key = os.getenv("QWEN3_OMNI_API_KEY", "")
        omni_model = os.getenv("QWEN3_OMNI_MODEL_NAME", "qwen3-omni-flash")
        omni_base = os.getenv("QWEN3_OMNI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        return QWenSeq2txt(
            key=omni_key,
            model_name=omni_model,
            lang=DEFAULT_PARSE_LANGUAGE,
            base_url=omni_base,
        )
    if VIDEO_PATTERN.search(file_path):
        omni_key = os.getenv("QWEN3_OMNI_API_KEY", "")
        omni_model = os.getenv("QWEN3_OMNI_MODEL_NAME", "qwen3-omni-flash")
        omni_base = os.getenv("QWEN3_OMNI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        return QWenCV(
            key=omni_key,
            model_name=omni_model,
            lang=DEFAULT_PARSE_LANGUAGE,
            base_url=omni_base,
        )
    return None


@celery_app.task(name="app.core.rag.tasks.parse_document")
def parse_document(file_key: str, document_id: uuid.UUID, file_name: str = ""):
    """
    Document parsing, vectorization, and storage.

    This task intentionally keeps DB sessions short. File download, parsing,
    QA generation, embedding, and ES writes run after the initial DB context
    has been closed.
    """
    progress_lines: list[str] = [f"{_progress_ts()} Task has been received."]
    start_time = time.time()
    document_label = file_name or str(document_id)

    def _progress_msg() -> str:
        return "\n".join(progress_lines) + "\n"

    def _clear_redis_state(doc_id: uuid.UUID):
        try:
            REDIS_CONN.delete(_PARSE_TASK_KEY.format(doc_id=doc_id))
            REDIS_CONN.delete(_PARSE_CANCEL_KEY.format(doc_id=doc_id))
        except Exception:
            logger.warning(f"[ParseDoc] failed to clear Redis state for {doc_id}", exc_info=True)

    def _should_abort(doc_id: uuid.UUID) -> bool:
        cancel = REDIS_CONN.get(_PARSE_CANCEL_KEY.format(doc_id=doc_id))
        if cancel:
            logger.info(f"[ParseDoc] document={doc_id} cancelled via Redis -- aborting")
            return True
        if not REDIS_CONN.is_alive():
            with get_db_context() as check_db:
                doc = check_db.query(Document).filter(Document.id == doc_id).first()
                if doc is None:
                    logger.info(f"[ParseDoc] document={doc_id} deleted -- aborting")
                    return True
        return False

    def _update_document(doc_id: uuid.UUID, updater):
        with get_db_context() as update_db:
            doc = update_db.query(Document).filter(Document.id == doc_id).first()
            if doc is None:
                logger.warning(f"[ParseDoc] document={doc_id} not found when updating parse state")
                return None
            updater(doc)
            update_db.commit()
            return doc

    try:
        if not isinstance(document_id, uuid.UUID):
            document_id = uuid.UUID(str(document_id))

        with get_db_context() as db:
            db_document = db.query(Document).filter(Document.id == document_id).first()
            if db_document is None:
                raise ValueError(f"Document {document_id} not found")

            db_knowledge = db.query(Knowledge).filter(Knowledge.id == db_document.kb_id).first()
            if db_knowledge is None:
                raise ValueError(f"Knowledge {db_document.kb_id} not found")
            db_workspace = db.query(Workspace).filter(Workspace.id == db_knowledge.workspace_id).first()
            if db_workspace is None:
                raise ValueError(f"Workspace {db_knowledge.workspace_id} not found")

            if not file_name:
                file_name = db_document.file_name
            document_label = file_name or str(document_id)

            parser_config = db_document.parser_config or {}
            auto_questions_topn = parser_config.get("auto_questions", 0)
            document_info = {
                "id": str(db_document.id),
                "file_id": str(db_document.file_id),
                "file_name": db_document.file_name,
                "file_created_at": to_timestamp_ms(db_document.created_at),
                "knowledge_id": str(db_document.kb_id),
                "tenant_id": str(db_workspace.tenant_id),
                "workspace_id": str(db_knowledge.workspace_id),
                "parent_child_mode": bool(db_document.is_parent_child_mode),
            }
            tenant_id = db_workspace.tenant_id
            llm_config = None
            if auto_questions_topn:
                llm_config = _build_llm_config(db, db_knowledge.llm_id, tenant_id)
            knowledge_id = str(db_knowledge.id)
            image_vision_model = _build_image_vision_model(db, db_knowledge.image2text_id, tenant_id)
            vision_model = _build_media_model(file_name) or image_vision_model
            vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

            progress_lines.append(f"{_progress_ts()} Start to parse.")
            db_document.progress = 0.0
            db_document.progress_msg = _progress_msg()
            db_document.process_begin_at = utcnow_naive()
            db_document.process_duration = 0.0
            db_document.run = 1
            db.commit()

        if _should_abort(document_id):
            _clear_redis_state(document_id)
            logger.info(f"[ParseDoc] document={document_id} cancelled via Redis -- stopped")
            return f"parse document '{document_label}' aborted (deleted or cancelled)."

        file_binary = _download_storage_file(file_key)
        if not file_binary:
            raise IOError(f"Downloaded empty file from storage: {file_key}")
        logger.info(f"[ParseDoc] Downloaded {len(file_binary)} bytes from storage key: {file_key}")

        estimated_pages = _get_estimated_pages(file_name, file_binary)
        logger.info(f"[ParseDoc] document={document_id} estimated_pages={estimated_pages}")
        if estimated_pages is None:
            logger.info(f"[ParseDoc] document={document_id} page number unavailable, continue parsing.")
            progress_lines.append(_progress_ts() + f" parse document '{document_label}' page number unavailable.")
        elif estimated_pages > MAX_DOCUMENT_PAGES:
            logger.info(
                f"[ParseDoc] document={document_id}, estimated page number:({estimated_pages}), exceeds {MAX_DOCUMENT_PAGES}")
            progress_lines.append(_progress_ts() + f" parse document '{document_label}' failed: page limit exceeded")

            def _mark_page_limit_failed(doc):
                doc.progress = -1.0
                doc.run = 0
                doc.progress_msg = _progress_msg()

            _update_document(document_id, _mark_page_limit_failed)
            _clear_redis_state(document_id)
            return f"parse document '{document_label}' failed: page limit exceeded"

        def progress_callback(prog=None, msg=None):
            progress_lines.append(f"{_progress_ts()} parse progress: {prog} msg: {msg}.")

        from app.core.rag.chunk import chunk_pipeline as chunk
        from app.core.rag.chunk.context import ChunkOutputMode
        logger.info(
            f"[ParseDoc] file_binary size={len(file_binary)} bytes, type={type(file_binary).__name__}, bool={bool(file_binary)}")

        if _should_abort(document_id):
            _clear_redis_state(document_id)
            logger.info(f"[ParseDoc] document={document_id} cancelled via Redis -- stopped")
            return f"parse document '{document_label}' aborted (deleted or cancelled)."

        parent_child_mode = document_info["parent_child_mode"]
        if parent_child_mode:
            child_res, parent_res, parent_id_map = chunk(
                filename=file_name,
                binary=file_binary,
                from_page=0,
                to_page=DEFAULT_PARSE_TO_PAGE,
                callback=progress_callback,
                vision_model=vision_model,
                parser_config=parser_config,
                is_root=False,
                chunk_output_mode=ChunkOutputMode.PARENT_CHILD,
                tenant_id=document_info["tenant_id"],
                workspace_id=document_info["workspace_id"],
                knowledge_id=document_info["knowledge_id"],
                document_id=document_info["id"],
                source_file_id=document_info["file_id"],
                source_file_name=document_info["file_name"],
            )
            if isinstance(child_res, GroupedChildChunks):
                validate_parent_child_result(
                    child_res,
                    parent_res,
                    parent_id_map,
                    str(parser_config.get("parent_chunk_mode") or "paragraph"),
                )
        else:
            res = chunk(
                filename=file_name,
                binary=file_binary,
                from_page=0,
                to_page=DEFAULT_PARSE_TO_PAGE,
                callback=progress_callback,
                vision_model=vision_model,
                parser_config=parser_config,
                is_root=False,
                tenant_id=document_info["tenant_id"],
                workspace_id=document_info["workspace_id"],
                knowledge_id=document_info["knowledge_id"],
                document_id=document_info["id"],
                source_file_id=document_info["file_id"],
                source_file_name=document_info["file_name"],
            )

        progress_lines.append(f"{_progress_ts()} Finish parsing.")

        def _mark_parsed(doc):
            doc.progress = 0.8
            doc.progress_msg = _progress_msg()

        _update_document(document_id, _mark_parsed)

        if _should_abort(document_id):
            _clear_redis_state(document_id)
            logger.info(f"[ParseDoc] document={document_id} cancelled via Redis -- stopped")
            return f"parse document '{document_label}' aborted (deleted or cancelled)."

        total_chunks = len(child_res) if parent_child_mode else len(res)
        progress_lines.append(f"{_progress_ts()} Generate {total_chunks} chunks.")

        if total_chunks == 0:
            progress_lines.append(f"{_progress_ts()} No chunks generated, skipping vectorization.")
        else:
            vector_service.delete_by_metadata_field(key="document_id", value=str(document_id))
            qa_prompt = parser_config.get("qa_prompt", None)
            chat_model = None
            if auto_questions_topn:
                if llm_config is None:
                    raise RuntimeError("auto_questions is enabled but LLM config is unavailable")
                chat_model = Base(
                    key=llm_config["key"],
                    model_name=llm_config["model_name"],
                    base_url=llm_config["base_url"],
                )
                logger.info(f"[QA] LLM model: {llm_config['model_name']}, base_url: {llm_config['base_url']}")
                if qa_prompt:
                    logger.info(f"[QA] Using custom prompt ({len(qa_prompt)} chars)")

            all_batch_chunks: list[list[DocumentChunk]] = []

            if parent_child_mode:
                parent_chunks_list = []
                parent_id_to_doc_id = {}

                for idx, item in enumerate(parent_res):
                    parent_doc_id = uuid.uuid4().hex
                    parent_id_to_doc_id[idx] = parent_doc_id
                    meta = {
                        "doc_id": parent_doc_id,
                        "file_id": document_info["file_id"],
                        "file_name": document_info["file_name"],
                        "file_created_at": document_info["file_created_at"],
                        "document_id": document_info["id"],
                        "knowledge_id": document_info["knowledge_id"],
                        "sort_id": idx,
                        "status": 1,
                        "chunk_type": "parent",
                    }
                    parent_chunks_list.append(
                        DocumentChunk(
                            page_content=item["content_with_weight"],
                            metadata=merge_parser_metadata(meta, item),
                        )
                    )

                child_chunks_list = []
                for idx, item in enumerate(child_res):
                    parent_idx = parent_id_map.get(idx)
                    parent_doc_id = parent_id_to_doc_id.get(parent_idx, "")
                    meta = {
                        "doc_id": uuid.uuid4().hex,
                        "file_id": document_info["file_id"],
                        "file_name": document_info["file_name"],
                        "file_created_at": document_info["file_created_at"],
                        "document_id": document_info["id"],
                        "knowledge_id": document_info["knowledge_id"],
                        "sort_id": idx,
                        "status": 1,
                        "chunk_type": "child",
                        "parent_id": parent_doc_id,
                    }
                    child_chunks_list.append(
                        DocumentChunk(
                            page_content=item["content_with_weight"],
                            metadata=merge_parser_metadata(meta, item),
                        )
                    )

                all_chunks = prioritize_vectorized_chunks(parent_chunks_list + child_chunks_list)
                for batch_start in range(0, len(all_chunks), EMBEDDING_BATCH_SIZE):
                    batch_end = min(batch_start + EMBEDDING_BATCH_SIZE, len(all_chunks))
                    all_batch_chunks.append(all_chunks[batch_start:batch_end])

                progress_lines.append(
                    f"{_progress_ts()} Parent-child mode: {len(parent_chunks_list)} parent chunks + "
                    f"{len(child_chunks_list)} child chunks prepared."
                )

            elif auto_questions_topn:
                indexed_items = list(enumerate(res))

                def _generate_qa(idx_item: tuple[int, dict]) -> tuple[int, list]:
                    global_idx, item = idx_item
                    content = item["content_with_weight"]
                    cache_params = {"topn": auto_questions_topn}
                    if qa_prompt:
                        import hashlib
                        cache_params["prompt_hash"] = hashlib.md5(qa_prompt.encode()).hexdigest()[:8]
                    cached = get_llm_cache(chat_model.model_name, content, "qa", cache_params)
                    if not cached:
                        logger.info(f"[QA] Cache miss for chunk {global_idx}, calling LLM. cache_params={cache_params}")
                        try:
                            pairs = qa_proposal(chat_model, content, auto_questions_topn, custom_prompt=qa_prompt)
                        except Exception as e:
                            logger.error(
                                f"[QA] LLM call failed: model={chat_model.model_name}, base_url={getattr(chat_model, 'base_url', 'N/A')}, error={e}")
                            return global_idx, []
                        logger.info(f"[QA] Chunk {global_idx} generated {len(pairs)} QA pairs")
                        set_llm_cache(
                            chat_model.model_name,
                            content,
                            json.dumps(pairs, ensure_ascii=False),
                            "qa",
                            cache_params,
                        )
                        return global_idx, pairs
                    logger.info(
                        f"[QA] Cache hit for chunk {global_idx}, cache_params={cache_params}, cached_type={type(cached).__name__}")
                    if isinstance(cached, str):
                        try:
                            parsed = json.loads(cached)
                            if isinstance(parsed, list):
                                logger.info(f"[QA] Chunk {global_idx} loaded {len(parsed)} QA pairs from cache")
                                return global_idx, parsed
                        except (json.JSONDecodeError, TypeError):
                            pass
                        from app.core.rag.prompts.generator import parse_qa_pairs
                        return global_idx, parse_qa_pairs(cached) if cached else []
                    return global_idx, cached if isinstance(cached, list) else []

                qa_map: dict[int, list] = {}
                with ThreadPoolExecutor(max_workers=AUTO_QUESTIONS_MAX_WORKERS) as q_executor:
                    futures = {q_executor.submit(_generate_qa, item): item[0] for item in indexed_items}
                    for future in futures:
                        global_idx, pairs = future.result()
                        qa_map[global_idx] = pairs

                progress_lines.append(
                    f"{_progress_ts()} QA pairs generated for {total_chunks} chunks "
                    f"(workers={AUTO_QUESTIONS_MAX_WORKERS})."
                )

                source_chunks = []
                qa_chunks = []
                qa_sort_id = 0

                for global_idx in range(total_chunks):
                    item = res[global_idx]
                    source_chunk_id = uuid.uuid4().hex
                    source_meta = {
                        "doc_id": source_chunk_id,
                        "file_id": document_info["file_id"],
                        "file_name": document_info["file_name"],
                        "file_created_at": document_info["file_created_at"],
                        "document_id": document_info["id"],
                        "knowledge_id": document_info["knowledge_id"],
                        "sort_id": global_idx,
                        "status": 1,
                        "chunk_type": "source",
                    }
                    source_chunks.append(
                        DocumentChunk(
                            page_content=item["content_with_weight"],
                            metadata=merge_parser_metadata(source_meta, item),
                        )
                    )

                    pairs = qa_map.get(global_idx, [])
                    for pair in pairs:
                        qa_meta = {
                            "doc_id": uuid.uuid4().hex,
                            "file_id": document_info["file_id"],
                            "file_name": document_info["file_name"],
                            "file_created_at": document_info["file_created_at"],
                            "document_id": document_info["id"],
                            "knowledge_id": document_info["knowledge_id"],
                            "sort_id": qa_sort_id,
                            "status": 1,
                            "chunk_type": "qa",
                            "question": pair["question"],
                            "answer": pair["answer"],
                            "source_chunk_id": source_chunk_id,
                        }
                        qa_chunks.append(DocumentChunk(page_content=pair["question"], metadata=qa_meta))
                        qa_sort_id += 1

                all_chunks = prioritize_vectorized_chunks(source_chunks + qa_chunks)
                for batch_start in range(0, len(all_chunks), EMBEDDING_BATCH_SIZE):
                    batch_end = min(batch_start + EMBEDDING_BATCH_SIZE, len(all_chunks))
                    all_batch_chunks.append(all_chunks[batch_start:batch_end])

                progress_lines.append(
                    f"{_progress_ts()} QA mode: {len(source_chunks)} source chunks + "
                    f"{len(qa_chunks)} QA chunks prepared."
                )
            else:
                for batch_start in range(0, total_chunks, EMBEDDING_BATCH_SIZE):
                    batch_end = min(batch_start + EMBEDDING_BATCH_SIZE, total_chunks)
                    chunks = []
                    for global_idx in range(batch_start, batch_end):
                        item = res[global_idx]
                        metadata = {
                            "doc_id": uuid.uuid4().hex,
                            "file_id": document_info["file_id"],
                            "file_name": document_info["file_name"],
                            "file_created_at": document_info["file_created_at"],
                            "document_id": document_info["id"],
                            "knowledge_id": document_info["knowledge_id"],
                            "sort_id": global_idx,
                            "status": 1,
                        }
                        chunks.append(
                            DocumentChunk(
                                page_content=item["content_with_weight"],
                                metadata=merge_parser_metadata(metadata, item),
                            )
                        )
                    all_batch_chunks.append(chunks)

            total_batches = len(all_batch_chunks)
            batch_errors: dict[int, Exception] = {}

            def _embed_and_store(batch_idx: int, batch_chunks: list[DocumentChunk]):
                try:
                    vector_service.add_chunks(batch_chunks)
                except Exception as exc:
                    logger.warning(f"[ParseDoc] batch {batch_idx} failed, retrying: {exc}")
                    try:
                        vector_service.add_chunks(batch_chunks)
                    except Exception as retry_exc:
                        logger.error(f"[ParseDoc] batch {batch_idx} retry failed: {retry_exc}", exc_info=True)
                        batch_errors[batch_idx] = retry_exc

            bootstrap_batch_idx, bootstrap_batch = pop_vectorized_bootstrap_batch(all_batch_chunks)
            if bootstrap_batch is not None:
                logger.info(
                    "[ParseDoc] writing vectorized bootstrap batch before concurrent ES writes: "
                    f"batch={bootstrap_batch_idx}, chunks={len(bootstrap_batch)}"
                )
                _embed_and_store(bootstrap_batch_idx, bootstrap_batch)
                if bootstrap_batch_idx in batch_errors:
                    failed_detail = "; ".join(
                        f"batch {i}: {type(err).__name__}: {err}"
                        for i, err in sorted(batch_errors.items())
                    )
                    raise RuntimeError(
                        f"Embedding failed for {len(batch_errors)}/{total_batches} batch(es). {failed_detail}"
                    )

            with ThreadPoolExecutor(max_workers=EMBEDDING_MAX_WORKERS) as executor:
                futures = {
                    executor.submit(_embed_and_store, i, batch_chunks): i
                    for i, batch_chunks in enumerate(all_batch_chunks)
                }
                for future in futures:
                    future.result()

            if batch_errors:
                failed_detail = "; ".join(
                    f"batch {i}: {type(err).__name__}: {err}"
                    for i, err in sorted(batch_errors.items())
                )
                raise RuntimeError(
                    f"Embedding failed for {len(batch_errors)}/{total_batches} batch(es). {failed_detail}")

            progress_lines.append(
                f"{_progress_ts()} All {total_batches} batches embedded (workers={EMBEDDING_MAX_WORKERS}).")

            def _mark_vectorized(doc):
                doc.progress = 1.0
                doc.progress_msg = _progress_msg()
                doc.process_duration = time.time() - start_time
                doc.run = 0

            _update_document(document_id, _mark_vectorized)

        progress_lines.append(f"{_progress_ts()} Indexing done.")
        process_duration = time.time() - start_time
        progress_lines.append(f"{_progress_ts()} Task done ({process_duration}s).")

        def _mark_done(doc):
            doc.chunk_num = total_chunks
            doc.progress = 1.0
            doc.process_duration = process_duration
            doc.progress_msg = _progress_msg()
            doc.run = 0

        _update_document(document_id, _mark_done)

        with get_db_context() as graph_db:
            current_knowledge = graph_db.query(Knowledge).filter(
                Knowledge.id == uuid.UUID(knowledge_id)
            ).first()
            current_graph_config = (
                dict(current_knowledge.parser_config or {})
                if current_knowledge is not None
                else None
            )

        if current_graph_config is not None and is_graph_enabled(current_graph_config):
            if _should_abort(document_id):
                _clear_redis_state(document_id)
                logger.info(f"[ParseDoc] document={document_id} cancelled via Redis -- stopped")
                return f"parse document '{document_label}' aborted (deleted or cancelled)."
            progress_lines.append(
                f"{_progress_ts()} Knowledge graph enabled, dispatching async task."
            )

            def _mark_graphrag_dispatched(doc):
                doc.progress_msg = _progress_msg()

            _update_document(document_id, _mark_graphrag_dispatched)
            dispatch_document_graph_sync(
                knowledge_id,
                str(document_id),
                current_graph_config,
            )

        _clear_redis_state(document_id)
        result = f"parse document '{document_info['file_name']}' processed successfully."
        logger.info(
            f"[ParseDoc] document={document_id} file='{document_info['file_name']}' "
            f"done in {process_duration:.1f}s, chunks={total_chunks}"
        )
        return result
    except Exception as e:
        logger.error(f"[ParseDoc] document={document_id} failed: {e}", exc_info=True)
        _clear_redis_state(document_id)
        try:
            progress_lines.append(f"{_progress_ts()} Failed to vectorize and import the parsed document:{str(e)}")

            def _mark_failed(doc):
                doc.progress = -1.0
                doc.progress_msg = _progress_msg()
                doc.run = 0

            if isinstance(document_id, uuid.UUID):
                _update_document(document_id, _mark_failed)
        except Exception:
            logger.warning(f"[ParseDoc] document={document_id} failed to update error status in DB", exc_info=True)
        return f"parse document '{document_label}' failed."


@celery_app.task(name="app.core.rag.tasks.build_graphrag_for_kb")
def build_graphrag_for_kb(kb_id: uuid.UUID):
    """
    build knowledge graph
    """
    import importlib

    import trio
    importlib.reload(trio)

    try:
        if not isinstance(kb_id, uuid.UUID):
            kb_id = uuid.UUID(str(kb_id))

        with get_db_context() as db:
            db_knowledge = db.query(Knowledge).filter(Knowledge.id == kb_id).first()
            if db_knowledge is None:
                logger.error(f"[GraphRAG-KB] knowledge={kb_id} not found")
                return "build knowledge graph failed: knowledge not found"
            db_workspace = db.query(Workspace).filter(Workspace.id == db_knowledge.workspace_id).first()
            if db_workspace is None:
                logger.error(f"[GraphRAG-KB] workspace={db_knowledge.workspace_id} not found")
                return "build knowledge graph failed: workspace not found"

            kb_name = db_knowledge.name
            tenant_id = db_workspace.tenant_id
            parser_config = db_knowledge.parser_config or {}
            graphrag_conf = parser_config.get("graphrag", {})
            if not graphrag_conf.get("use_graphrag", False):
                return f"build knowledge graph '{kb_name}' skipped: graphrag not enabled"
            if resolve_graph_pipeline(parser_config) is not GraphPipeline.LEGACY:
                return f"build knowledge graph '{kb_name}' skipped: pipeline changed"

            db_documents = db.query(Document).filter(Document.kb_id == kb_id).all()
            document_ids = [str(doc.id) for doc in db_documents]

            chat_model = _build_chat_model(db, db_knowledge.llm_id, tenant_id)
            embedding_model = _build_embedding_model(db, db_knowledge.embedding_id, tenant_id)
            vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

            with_resolution = graphrag_conf.get("resolution", False)
            with_community = graphrag_conf.get("community", False)

            task = {
                "id": str(db_knowledge.id),
                "workspace_id": str(db_knowledge.workspace_id),
                "kb_id": str(db_knowledge.id),
                "parser_config": parser_config,
            }

        # init_graphrag
        vts, _ = embedding_model.encode(["ok"])
        vector_size = len(vts[0])
        init_graphrag(task, vector_size)

        def callback(*args, msg=None, **kwargs):
            message = msg or (args[0] if args else "No message")
            logger.info(f"[GraphRAG-KB] kb={kb_id} msg: {message}")

        start_time = time.time()

        async def _run() -> dict:
            return await run_graphrag_for_kb(
                row=task,
                document_ids=document_ids,
                language=DEFAULT_PARSE_LANGUAGE,
                parser_config=parser_config,
                vector_service=vector_service,
                chat_model=chat_model,
                embedding_model=embedding_model,
                callback=callback,
                with_resolution=with_resolution,
                with_community=with_community,
            )

        result = trio.run(_run)
        duration = time.time() - start_time
        logger.info(f"[GraphRAG-KB] kb={kb_id} done in {duration:.1f}s, result: {result}")

        return f"build knowledge graph '{kb_name}' processed successfully."
    except Exception as e:
        logger.error(f"[GraphRAG-KB] kb={kb_id} failed: {e}", exc_info=True)
        return f"build knowledge graph failed: {e}"


@celery_app.task(name="app.core.rag.tasks.build_graphrag_for_document")
def build_graphrag_for_document(document_id: str, knowledge_id: str):
    """
    为单个文档构建 GraphRAG，由 parse_document 异步派发。
    """
    import importlib

    import trio
    importlib.reload(trio)

    try:
        with get_db_context() as db:
            db_document = db.query(Document).filter(Document.id == uuid.UUID(document_id)).first()
            db_knowledge = db.query(Knowledge).filter(Knowledge.id == uuid.UUID(knowledge_id)).first()
            if db_document is None or db_knowledge is None:
                logger.error(f"[GraphRAG] document={document_id} or knowledge={knowledge_id} not found")
                return "build_graphrag_for_document failed: record not found"
            db_workspace = db.query(Workspace).filter(Workspace.id == db_knowledge.workspace_id).first()
            if db_workspace is None:
                logger.error(f"[GraphRAG] workspace={db_knowledge.workspace_id} not found")
                return "build_graphrag_for_document failed: workspace not found"

            tenant_id = db_workspace.tenant_id
            parser_config = db_knowledge.parser_config or {}
            graphrag_conf = parser_config.get("graphrag", {})
            if not graphrag_conf.get("use_graphrag", False):
                return (
                    f"build_graphrag_for_document '{document_id}' skipped: "
                    "graphrag not enabled"
                )
            if resolve_graph_pipeline(parser_config) is not GraphPipeline.LEGACY:
                return (
                    f"build_graphrag_for_document '{document_id}' skipped: "
                    "pipeline changed"
                )
            with_resolution = graphrag_conf.get("resolution", False)
            with_community = graphrag_conf.get("community", False)

            chat_model = _build_chat_model(db, db_knowledge.llm_id, tenant_id)
            embedding_model = _build_embedding_model(db, db_knowledge.embedding_id, tenant_id)
            vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

            task = {
                "id": document_id,
                "workspace_id": str(db_knowledge.workspace_id),
                "kb_id": str(db_knowledge.id),
                "parser_config": parser_config,
            }

        # init_graphrag
        vts, _ = embedding_model.encode(["ok"])
        vector_size = len(vts[0])
        init_graphrag(task, vector_size)

        def callback(*args, msg=None, **kwargs):
            message = msg or (args[0] if args else "No message")
            logger.info(f"[GraphRAG] doc={document_id} msg: {message}")

        start_time = time.time()

        async def _run() -> dict:
            await trio.sleep(5)
            return await run_graphrag_for_kb(
                row=task,
                document_ids=[document_id],
                language=DEFAULT_PARSE_LANGUAGE,
                parser_config=parser_config,
                vector_service=vector_service,
                chat_model=chat_model,
                embedding_model=embedding_model,
                callback=callback,
                with_resolution=with_resolution,
                with_community=with_community,
            )

        result = trio.run(_run)
        duration = time.time() - start_time
        logger.info(f"[GraphRAG] doc={document_id} done in {duration:.1f}s")

        with get_db_context() as db:
            db_document = db.query(Document).filter(Document.id == uuid.UUID(document_id)).first()
            if db_document is None:
                logger.warning(f"[GraphRAG] document={document_id} not found when updating progress")
                return f"build_graphrag_for_document '{document_id}' processed successfully."
            # 更新文档进度信息
            db_document.progress_msg = (db_document.progress_msg or "") + \
                                       f"{_progress_ts()} Knowledge Graph done ({duration:.1f}s)\n"
            db.commit()

        return f"build_graphrag_for_document '{document_id}' processed successfully."
    except Exception as e:
        logger.error(f"[GraphRAG] doc={document_id} failed: {e}", exc_info=True)
        return f"build_graphrag_for_document '{document_id}' failed: {e}"


@celery_app.task(
    bind=True,
    name="app.core.rag.tasks.sync_evidence_graph_document",
    max_retries=5,
)
def sync_evidence_graph_document(
        self,
        knowledge_id: str,
        document_id: str,
        document_deleted: bool = False,
):
    return _run_observed_graph_task(
        self,
        task_name="sync_document",
        knowledge_id=str(knowledge_id),
        document_id=str(document_id),
        operation=lambda: (
            _run_evidence_graph_document(
                knowledge_id,
                document_id,
                document_deleted=True,
            )
            if document_deleted
            else _run_evidence_graph_document(
                knowledge_id,
                document_id,
            )
        ),
    )


@celery_app.task(
    bind=True,
    name="app.core.rag.tasks.rebuild_evidence_graph_knowledge",
    max_retries=5,
    acks_late=False,
    reject_on_worker_lost=False,
    track_started=False,
)
def rebuild_evidence_graph_knowledge(self, knowledge_id: str):
    return _run_guarded_evidence_graph_rebuild(
        self,
        str(knowledge_id),
    )


@celery_app.task(
    bind=True,
    name="app.core.rag.tasks.migrate_evidence_graph_knowledge",
    max_retries=5,
)
def migrate_evidence_graph_knowledge(self, knowledge_id: str):
    return _run_observed_graph_task(
        self,
        task_name="migrate_knowledge",
        knowledge_id=str(knowledge_id),
        operation=lambda: _run_evidence_graph_migration(knowledge_id),
    )


@celery_app.task(
    bind=True,
    name="app.core.rag.tasks.clear_all_knowledge_graph_data",
    max_retries=5,
)
def clear_all_knowledge_graph_data(
        self,
        knowledge_id: str,
        force: bool = False,
):
    return _run_observed_graph_task(
        self,
        task_name="clear_knowledge",
        knowledge_id=str(knowledge_id),
        operation=lambda: _run_clear_all_knowledge_graph_data(
            knowledge_id,
            force=force,
        ),
    )


@celery_app.task(name="app.core.rag.tasks.import_qa_chunks", queue="qa_import")
def import_qa_chunks(
        kb_id: str,
        document_id: str,
        filename: str,
        contents: bytes | None = None,
        file_key: str | None = None,
        clear_parse_task: bool = False,
):
    """
    异步导入 QA 问答对（CSV/Excel）
    
    文件格式：第一行标题（跳过），第一列问题，第二列答案
    """
    import csv as csv_module
    import io

    db = None
    start_time = time.time()
    progress_lines: list[str] = [f"{_progress_ts()} QA import task has been received."]

    def _qa_progress_msg() -> str:
        return "\n".join(progress_lines) + "\n"

    try:
        with get_db_context() as db:
            db_document = db.query(Document).filter(Document.id == uuid.UUID(document_id)).first()
            db_knowledge = db.query(Knowledge).filter(Knowledge.id == uuid.UUID(kb_id)).first()
            if not db_document or not db_knowledge:
                logger.error(f"[ImportQA] document={document_id} or knowledge={kb_id} not found")
                return {"error": "document or knowledge not found", "imported": 0}

            document_info = {
                "file_id": str(db_document.file_id),
                "file_name": db_document.file_name,
                "file_created_at": to_timestamp_ms(db_document.created_at),
            }
            vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

            progress_lines.append(f"{_progress_ts()} Start to import QA.")
            db_document.progress = 0.0
            db_document.progress_msg = _qa_progress_msg()
            db_document.process_begin_at = utcnow_naive()
            db_document.process_duration = 0.0
            db_document.run = 1
            db.commit()

        if contents is None:
            if not file_key:
                raise ValueError("contents or file_key is required for QA import")
            contents = _download_storage_file(file_key)
            if not contents:
                raise IOError(f"Downloaded empty QA file from storage: {file_key}")
            logger.info(f"[ImportQA] Downloaded {len(contents)} bytes from storage key: {file_key}")

        # 1. 解析文件
        qa_pairs = []
        failed_rows = []

        if filename.endswith(".csv"):
            try:
                text = contents.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = contents.decode("gbk", errors="ignore")

            sniffer = csv_module.Sniffer()
            try:
                dialect = sniffer.sniff(text[:2048])
                delimiter = dialect.delimiter
            except csv_module.Error:
                delimiter = "," if "," in text[:500] else "\t"

            reader = csv_module.reader(io.StringIO(text), delimiter=delimiter)
            for i, row in enumerate(reader):
                if i == 0:
                    continue
                if len(row) >= 2 and row[0].strip():
                    qa_pairs.append({"question": row[0].strip(), "answer": row[1].strip() if row[1].strip() else ""})
                elif len(row) >= 1 and row[0].strip():
                    failed_rows.append(i + 1)

        elif filename.endswith(".xlsx") or filename.endswith(".xls"):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(contents), read_only=True)
                for sheet in wb.worksheets:
                    for i, row in enumerate(sheet.iter_rows(values_only=True)):
                        if i == 0:
                            continue
                        if len(row) >= 2 and row[0]:
                            q = str(row[0]).strip()
                            a = str(row[1]).strip() if row[1] else ""
                            if q:
                                qa_pairs.append({"question": q, "answer": a})
                        elif len(row) >= 1 and row[0]:
                            failed_rows.append(i + 1)
                wb.close()
            except Exception as e:
                logger.error(f"[ImportQA] Excel parse failed: {e}")
                raise RuntimeError(f"Excel parse failed: {e}") from e

        if not qa_pairs:
            logger.warning(f"[ImportQA] No valid QA pairs found in {filename}")
            raise ValueError("No valid QA pairs found")

        logger.info(f"[ImportQA] Parsed {len(qa_pairs)} QA pairs from {filename}, failed_rows={failed_rows}")
        progress_lines.append(f"{_progress_ts()} Parsed {len(qa_pairs)} QA pairs.")

        # 2. 写入 ES
        sort_id = 0
        if clear_parse_task:
            vector_service.delete_by_metadata_field(key="document_id", value=document_id)
        else:
            total, items = vector_service.search_by_segment(document_id=document_id, pagesize=1, page=1, asc=False)
            if items:
                sort_id = items[0].metadata["sort_id"]

        chunks = []
        for pair in qa_pairs:
            sort_id += 1
            doc_id = uuid.uuid4().hex
            metadata = {
                "doc_id": doc_id,
                "file_id": document_info["file_id"],
                "file_name": document_info["file_name"],
                "file_created_at": document_info["file_created_at"],
                "document_id": document_id,
                "knowledge_id": kb_id,
                "sort_id": sort_id,
                "status": 1,
                "chunk_type": "qa",
                "question": pair["question"],
                "answer": pair["answer"],
            }
            chunks.append(DocumentChunk(page_content=pair["question"], metadata=metadata))

        batch_size = min(EMBEDDING_BATCH_SIZE or 10, 20)
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            vector_service.add_chunks(batch)

        with get_db_context() as db:
            db_document = db.query(Document).filter(Document.id == uuid.UUID(document_id)).first()
            if not db_document:
                logger.warning(f"[ImportQA] document={document_id} not found when updating completion state")
                return {"error": "document not found", "imported": 0}
            if clear_parse_task:
                db_document.chunk_num = 0
            # 3. 更新 chunk_num 和 progress
            db_document.chunk_num += len(chunks)
            db_document.progress = 1.0
            db_document.process_duration = time.time() - start_time
            db_document.run = 0
            progress_lines.append(f"{_progress_ts()} QA import done: {len(chunks)} chunks.")
            db_document.progress_msg = _qa_progress_msg()
            db.commit()

        result = {"imported": len(chunks), "failed_rows": failed_rows}
        logger.info(f"[ImportQA] Done: imported={len(chunks)}, failed={len(failed_rows)}")
        return result

    except Exception as e:
        logger.error(f"[ImportQA] Failed: {e}", exc_info=True)
        try:
            with get_db_context() as err_db:
                doc = err_db.query(Document).filter(Document.id == uuid.UUID(document_id)).first()
                if doc:
                    progress_lines.append(f"{_progress_ts()} QA import failed: {str(e)[:200]}")
                    doc.progress = -1.0
                    doc.progress_msg = _qa_progress_msg()
                    doc.process_duration = time.time() - start_time
                    doc.run = 0
                    err_db.commit()
        except Exception:
            pass
        return {"error": str(e), "imported": 0}
    finally:
        if clear_parse_task:
            try:
                REDIS_CONN.delete(_PARSE_TASK_KEY.format(doc_id=document_id))
            except Exception:
                logger.warning(f"[ImportQA] failed to clear Redis state for {document_id}", exc_info=True)


@celery_app.task(name="app.core.rag.tasks.sync_knowledge_for_kb")
def sync_knowledge_for_kb(kb_id: uuid.UUID):
    """
    sync knowledge document and Document parsing, vectorization, and storage
    """
    default_parser_config = {
        "layout_recognize": "DeepDOC",
        "chunk_token_num": 130,
        "delimiter": "\n",
        "auto_keywords": 0,
        "auto_questions": 0,
        "html4excel": "false",
    }

    def _snapshot_file(db_file: File) -> dict:
        return {
            "id": db_file.id,
            "kb_id": db_file.kb_id,
            "created_by": db_file.created_by,
            "parent_id": db_file.parent_id,
            "file_name": db_file.file_name,
            "file_ext": db_file.file_ext,
            "file_size": db_file.file_size,
            "file_url": db_file.file_url,
            "file_key": db_file.file_key,
            "created_at": db_file.created_at,
        }

    def _snapshot_document(db_document: Document | None) -> dict | None:
        if db_document is None:
            return None
        return {
            "id": db_document.id,
            "file_id": db_document.file_id,
            "file_name": db_document.file_name,
        }

    def _load_knowledge_state(kb_uuid: uuid.UUID) -> tuple[dict, Any]:
        with get_db_context() as db:
            db_knowledge = db.query(Knowledge).filter(Knowledge.id == kb_uuid).first()
            if db_knowledge is None:
                raise ValueError("knowledge not found")
            vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
            return {
                "id": db_knowledge.id,
                "name": db_knowledge.name,
                "type": db_knowledge.type,
                "parser_config": db_knowledge.parser_config or {},
                "created_by": db_knowledge.created_by,
            }, vector_service

    def _get_existing_files(kb_uuid: uuid.UUID) -> list[dict]:
        with get_db_context() as db:
            return [
                _snapshot_file(db_file)
                for db_file in db.query(File).filter(
                    File.kb_id == kb_uuid,
                    File.file_role == FILE_ROLE_SOURCE,
                ).all()
            ]

    def _get_file_by_url(kb_uuid: uuid.UUID, file_url: str) -> dict | None:
        with get_db_context() as db:
            db_file = db.query(File).filter(
                File.kb_id == kb_uuid,
                File.file_role == FILE_ROLE_SOURCE,
                File.file_url == file_url,
            ).first()
            return _snapshot_file(db_file) if db_file else None

    def _create_file_record(
            knowledge_state: dict,
            *,
            file_name: str,
            file_ext: str,
            file_size: int,
            file_url: str,
            created_at: datetime | None = None,
    ) -> dict:
        with get_db_context() as db:
            upload_file = file_schema.FileCreate(
                kb_id=knowledge_state["id"],
                created_by=knowledge_state["created_by"],
                parent_id=knowledge_state["id"],
                file_name=file_name,
                file_ext=file_ext,
                file_size=file_size,
                file_url=file_url,
                created_at=created_at,
            )
            db_file = File(**upload_file.model_dump())
            db.add(db_file)
            db.commit()
            db.refresh(db_file)
            return _snapshot_file(db_file)

    def _update_file_record(
            kb_uuid: uuid.UUID,
            file_id: uuid.UUID,
            *,
            file_name: str,
            file_ext: str,
            file_size: int,
            file_key: str,
            created_at: datetime | None = None,
            sync_document_created_at: bool = False,
    ) -> tuple[dict | None, dict | None]:
        with get_db_context() as db:
            db_file = db.query(File).filter(File.id == file_id).first()
            if db_file is None:
                logger.warning(f"[SyncKB] file={file_id} not found when updating synced file")
                return None, None

            db_file.file_name = file_name
            db_file.file_ext = file_ext
            db_file.file_size = file_size
            db_file.file_key = file_key
            if created_at is not None:
                db_file.created_at = created_at
            db.commit()
            db.refresh(db_file)

            db_document = db.query(Document).filter(Document.kb_id == kb_uuid, Document.file_id == db_file.id).first()
            if db_document:
                db_document.file_name = db_file.file_name
                db_document.file_ext = db_file.file_ext
                db_document.file_size = db_file.file_size
                if sync_document_created_at:
                    db_document.created_at = db_file.created_at
                db_document.updated_at = utcnow_naive()
                db.commit()
                db.refresh(db_document)
            return _snapshot_file(db_file), _snapshot_document(db_document)

    def _create_document_record(knowledge_state: dict, file_state: dict) -> dict:
        with get_db_context() as db:
            create_document_data = document_schema.DocumentCreate(
                kb_id=knowledge_state["id"],
                created_by=knowledge_state["created_by"],
                file_id=file_state["id"],
                file_name=file_state["file_name"],
                file_ext=file_state["file_ext"],
                file_size=file_state["file_size"],
                file_meta={},
                parser_id="naive",
                parser_config=default_parser_config,
            )
            db_document = Document(**create_document_data.model_dump())
            db.add(db_document)
            db.commit()
            db.refresh(db_document)
            return _snapshot_document(db_document)

    def _legacy_file_path(kb_uuid: uuid.UUID, parent_id: uuid.UUID, file_id: uuid.UUID, file_ext: str) -> Path:
        return Path(settings.FILE_PATH, str(kb_uuid), str(parent_id), f"{file_id}{file_ext}")

    def _write_legacy_file(kb_uuid: uuid.UUID, parent_id: uuid.UUID, file_id: uuid.UUID, file_ext: str,
                           content: bytes) -> Path:
        file_path = _legacy_file_path(kb_uuid, parent_id, file_id, file_ext)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if file_path.exists():
            file_path.unlink()
        file_path.write_bytes(content)
        return file_path

    def _copy_legacy_file(kb_uuid: uuid.UUID, parent_id: uuid.UUID, file_id: uuid.UUID, file_ext: str,
                          source_path: str) -> Path:
        file_path = _legacy_file_path(kb_uuid, parent_id, file_id, file_ext)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        if file_path.exists():
            file_path.unlink()
        shutil.copyfile(source_path, file_path)
        return file_path

    def _dispatch_if_document(file_state: dict | None, document_state: dict | None):
        if file_state and document_state:
            _dispatch_parse_document(file_state["file_key"], document_state["id"], file_state["file_name"])
        elif file_state and not document_state:
            logger.warning(f"[SyncKB] skip parse because document is missing: file={file_state['id']}")

    def _delete_stale_files(kb_uuid: uuid.UUID, file_urls: set, vector_service):
        with get_db_context() as db:
            stale_files = []
            db_files = db.query(File).filter(
                File.kb_id == kb_uuid,
                File.file_role == FILE_ROLE_SOURCE,
                File.file_url.notin_(file_urls),
            ).all()
            for db_file in db_files:
                db_document = db.query(Document).filter(Document.kb_id == kb_uuid,
                                                        Document.file_id == db_file.id).first()
                file_state = _snapshot_file(db_file)
                file_state["document_id"] = db_document.id if db_document else None
                stale_files.append(file_state)

        for file_state in stale_files:
            document_id = file_state.get("document_id")
            if document_id:
                vector_service.delete_by_metadata_field(key="document_id", value=str(document_id))
                try:
                    cleanup_mineru_v3_images(document_id)
                except Exception:
                    logger.warning(
                        "[SyncKB] failed to delete derived image assets: document_id=%s",
                        document_id,
                        exc_info=True,
                    )
            if file_state.get("file_key"):
                from app.services.file_storage_service import FileStorageService

                storage_service = FileStorageService()
                try:
                    asyncio.run(storage_service.delete_file(file_state["file_key"]))
                except Exception:
                    logger.warning(f"[SyncKB] failed to delete storage file: file_key={file_state['file_key']}",
                                   exc_info=True)
            legacy_path = _legacy_file_path(
                file_state["kb_id"],
                file_state["parent_id"],
                file_state["id"],
                file_state["file_ext"],
            )
            if legacy_path.exists():
                legacy_path.unlink()

        with get_db_context() as db:
            for file_state in stale_files:
                db_document = db.query(Document).filter(Document.kb_id == kb_uuid,
                                                        Document.file_id == file_state["id"]).first()
                if db_document:
                    db.delete(db_document)
                db_file = db.query(File).filter(File.id == file_state["id"]).first()
                if db_file:
                    db.delete(db_file)
            db.commit()

    try:
        if not isinstance(kb_id, uuid.UUID):
            kb_id = uuid.UUID(str(kb_id))

        try:
            knowledge_state, vector_service = _load_knowledge_state(kb_id)
        except ValueError:
            logger.error(f"[SyncKB] knowledge={kb_id} not found")
            return "sync knowledge failed: knowledge not found"

        match knowledge_state["type"]:
            case "Web":  # Crawl webpages in batches through a web crawler
                parser_config = knowledge_state["parser_config"]
                crawler = WebCrawler(
                    entry_url=parser_config.get("entry_url", ""),
                    max_pages=parser_config.get("max_pages", 20),
                    delay_seconds=parser_config.get("delay_seconds", 1.0),
                    timeout_seconds=parser_config.get("timeout_seconds", 10),
                    user_agent=parser_config.get("user_agent", "KnowledgeBaseCrawler/1.0"),
                )
                try:
                    file_urls = set()
                    for crawled_document in crawler.crawl():
                        file_urls.add(crawled_document.url)
                        if not crawled_document.content_length:
                            continue

                        file_state = _get_file_by_url(knowledge_state["id"], crawled_document.url)
                        if file_state and file_state["file_size"] == crawled_document.content_length:
                            continue

                        content_bytes = crawled_document.content.encode("utf-8")
                        is_new_file = file_state is None
                        if is_new_file:
                            file_state = _create_file_record(
                                knowledge_state,
                                file_name=f"{crawled_document.title}.txt",
                                file_ext=".txt",
                                file_size=crawled_document.content_length,
                                file_url=crawled_document.url,
                            )

                        _write_legacy_file(
                            knowledge_state["id"],
                            knowledge_state["id"],
                            file_state["id"],
                            ".txt",
                            content_bytes,
                        )
                        file_key = _upload_kb_file_content_sync(
                            knowledge_state["id"],
                            file_state["id"],
                            ".txt",
                            content_bytes,
                        )
                        file_state, existing_document_state = _update_file_record(
                            knowledge_state["id"],
                            file_state["id"],
                            file_name=f"{crawled_document.title}.txt",
                            file_ext=".txt",
                            file_size=crawled_document.content_length,
                            file_key=file_key,
                        )
                        if file_state is None:
                            continue
                        document_state = _create_document_record(knowledge_state,
                                                                 file_state) if is_new_file else existing_document_state
                        _dispatch_if_document(file_state, document_state)

                    _delete_stale_files(knowledge_state["id"], file_urls, vector_service)

                except Exception as e:
                    logger.error(f"[SyncKB] Error during crawl: {e}", exc_info=True)
            case "Third-party":  # Integration of knowledge bases from three parties
                parser_config = knowledge_state["parser_config"]
                yuque_user_id = parser_config.get("yuque_user_id", "")
                feishu_app_id = parser_config.get("feishu_app_id", "")

                existing_files = _get_existing_files(knowledge_state["id"])
                has_yuque = any(f["file_url"] and "yuque.com" in f["file_url"] for f in existing_files)
                has_feishu = any(f["file_url"] and "feishu.cn" in f["file_url"] for f in existing_files)

                if yuque_user_id and yuque_user_id not in ("User ID", "", None) \
                        and (not existing_files or has_yuque):  # Yuque Knowledge Base
                    yuque_token = parser_config.get("yuque_token", "")
                    api_client = YuqueAPIClient(
                        user_id=yuque_user_id,
                        token=yuque_token
                    )
                    try:
                        # 初始化存储获取语雀 URLs 的集合
                        file_urls = set()

                        # Get all files from all repos
                        async def async_get_files(api_client: YuqueAPIClient):
                            async with api_client as client:
                                repos = await client.get_user_repos()
                                all_files = []
                                for repo in repos:
                                    docs = await client.get_repo_docs(repo.id)
                                    all_files.extend(docs)
                                return all_files

                        files = asyncio.run(async_get_files(api_client))
                        for doc in files:
                            file_urls.add(doc.slug)
                            file_state = _get_file_by_url(knowledge_state["id"], doc.slug)
                            if file_state and file_state["created_at"] == doc.updated_at:
                                continue

                            save_dir = os.path.join(settings.FILE_PATH, str(knowledge_state["id"]),
                                                    str(knowledge_state["id"]))

                            async def async_download_document(api_client: YuqueAPIClient, doc: YuqueDocInfo,
                                                              save_dir: str):
                                async with api_client as client:
                                    return await client.download_document(doc, save_dir)

                            file_path = asyncio.run(async_download_document(api_client, doc, save_dir))
                            file_name = os.path.basename(file_path)
                            _, file_extension = os.path.splitext(file_name)
                            file_ext = file_extension.lower()
                            file_size = os.path.getsize(file_path)
                            is_new_file = file_state is None
                            if is_new_file:
                                file_state = _create_file_record(
                                    knowledge_state,
                                    file_name=file_name,
                                    file_ext=file_ext,
                                    file_size=file_size,
                                    file_url=doc.slug,
                                    created_at=doc.updated_at,
                                )

                            save_path = _copy_legacy_file(
                                knowledge_state["id"],
                                knowledge_state["id"],
                                file_state["id"],
                                file_ext,
                                file_path,
                            )
                            content = save_path.read_bytes()
                            file_key = _upload_kb_file_content_sync(
                                knowledge_state["id"],
                                file_state["id"],
                                file_ext,
                                content,
                            )
                            file_state, existing_document_state = _update_file_record(
                                knowledge_state["id"],
                                file_state["id"],
                                file_name=file_name,
                                file_ext=file_ext,
                                file_size=file_size,
                                file_key=file_key,
                                created_at=doc.updated_at,
                                sync_document_created_at=True,
                            )
                            if file_state is None:
                                continue
                            document_state = _create_document_record(knowledge_state,
                                                                     file_state) if is_new_file else existing_document_state
                            _dispatch_if_document(file_state, document_state)

                        _delete_stale_files(knowledge_state["id"], file_urls, vector_service)

                    except Exception as e:
                        logger.error(f"[SyncKB] Error during fetch yuque: {e}", exc_info=True)
                if feishu_app_id and feishu_app_id not in ("App ID", "", None) \
                        and (not existing_files or has_feishu):  # Feishu Knowledge Base
                    feishu_app_secret = parser_config.get("feishu_app_secret", "")
                    feishu_folder_token = parser_config.get("feishu_folder_token", "")
                    api_client = FeishuAPIClient(
                        app_id=feishu_app_id,
                        app_secret=feishu_app_secret
                    )
                    try:
                        # 初始化存储获取飞书 URLs 的集合
                        file_urls = set()

                        # Get all files from folder
                        async def async_get_files(api_client: FeishuAPIClient, feishu_folder_token: str):
                            async with api_client as client:
                                files = await client.list_all_folder_files(feishu_folder_token, recursive=True)
                                return files

                        files = asyncio.run(async_get_files(api_client, feishu_folder_token))
                        # Filter out folders, only sync documents
                        documents = [f for f in files if f.type in ["doc", "docx", "sheet", "bitable", "file"]]
                        for doc in documents:
                            file_urls.add(doc.url)
                            file_state = _get_file_by_url(knowledge_state["id"], doc.url)
                            if file_state and file_state["created_at"] == doc.modified_time:
                                continue

                            save_dir = tempfile.mkdtemp()

                            async def async_download_document(api_client: FeishuAPIClient, doc: FileInfo,
                                                              save_dir: str):
                                async with api_client as client:
                                    return await client.download_document(document=doc, save_dir=save_dir)

                            file_path = asyncio.run(async_download_document(api_client, doc, save_dir))
                            file_name = os.path.basename(file_path)
                            _, file_extension = os.path.splitext(file_name)
                            file_ext = file_extension.lower()
                            file_size = os.path.getsize(file_path)
                            is_new_file = file_state is None
                            if is_new_file:
                                file_state = _create_file_record(
                                    knowledge_state,
                                    file_name=file_name,
                                    file_ext=file_ext,
                                    file_size=file_size,
                                    file_url=doc.url,
                                    created_at=doc.modified_time,
                                )

                            with open(file_path, "rb") as _f:
                                content = _f.read()
                            file_key = _upload_kb_file_content_sync(
                                knowledge_state["id"],
                                file_state["id"],
                                file_ext,
                                content,
                            )
                            try:
                                if os.path.exists(file_path):
                                    os.remove(file_path)
                            except Exception:
                                pass

                            file_state, existing_document_state = _update_file_record(
                                knowledge_state["id"],
                                file_state["id"],
                                file_name=file_name,
                                file_ext=file_ext,
                                file_size=file_size,
                                file_key=file_key,
                                created_at=doc.modified_time,
                                sync_document_created_at=True,
                            )
                            if file_state is None:
                                continue
                            document_state = _create_document_record(knowledge_state,
                                                                     file_state) if is_new_file else existing_document_state
                            _dispatch_if_document(file_state, document_state)

                        _delete_stale_files(knowledge_state["id"], file_urls, vector_service)

                    except Exception as e:
                        logger.error(f"[SyncKB] Error during fetch feishu: {e}", exc_info=True)
            case _:  # General
                logger.info(f"[SyncKB] kb={kb_id} type={knowledge_state['type']}: no synchronization needed")

        result = f"sync knowledge '{knowledge_state['name']}' processed successfully."
        return result
    except Exception as e:
        logger.error(f"[SyncKB] kb={kb_id} failed: {e}", exc_info=True)
        kb_name = locals().get("knowledge_state", {}).get("name", kb_id)
        return f"sync knowledge '{kb_name}' failed: {e}"


@celery_app.task(name="app.core.memory.agent.write_message", bind=True, acks_late=False, max_retries=0)
def write_message_task(
        self,
        end_user_id: str,
        target_message: Optional[dict] = None,
        context_before: Optional[List[dict]] = None,
        context_after: Optional[List[dict]] = None,
        config_id: str = "",
        workspace_id: str = "",
        conversation_id: str = "",
        message_seq: int = 0,
        language: str = "zh",
        skip_cursor_advance: bool = False,
        dispatch_at: str = "",  # 任务执行时间
        source: str = "",  # 写入来源（agent/service_api/mcp/workflow）
        # MCP 入口兼容字段（不经过 memory_messages 表，直接写入）
        messages: Optional[List[dict]] = None,
        storage_type: str = "neo4j",
        user_rag_memory_id: str = "",
) -> Dict[str, Any]:
    """统一写入任务 — 纯净入口，接收完整参数直接写入。

    Args:
        end_user_id: 终端用户 ID（分片键）
        target_message: 目标消息 {"role": "user", "content": "...", "dialog_at": "..."}
        context_before: 上文消息列表
        context_after: 下文消息列表
        config_id: 记忆配置 ID
        workspace_id: 工作空间 ID
        conversation_id: 对话 ID
        message_seq: 消息序号
        language: 语言
        skip_cursor_advance: 是否跳过 cursor 推进（MCP 等直接写入路径）
        dispatch_at: 任务派发时刻的 UTC ISO 8601 时间戳，由 push_write_task 自动注入
        messages: MCP 入口兼容字段，单条消息列表 [{"role", "content", "dialog_at"}]
        storage_type: MCP 入口兼容字段，存储类型（neo4j / rag）
        user_rag_memory_id: MCP 入口兼容字段，RAG 记忆 ID

    Returns:
        Dict containing status, result, elapsed_time, task_id
    """
    loop = set_asyncio_event_loop()
    # MCP 入口兼容：收到 messages 但无 target_message 时，转换为新格式
    if target_message is None and messages:
        msg = messages[0] if messages else {"role": "user", "content": ""}
        target_message = msg
        context_before = []
        context_after = []
        skip_cursor_advance = True

    # 解析 end_user_id：若排队期间用户已被合并，自动路由到目标用户
    resolved_end_user_id = end_user_id
    try:
        with get_db_context() as db:
            from app.repositories.end_user_repository import EndUserRepository
            repo = EndUserRepository(db)
            resolved = repo.resolve_merge_by_origin_id(uuid.UUID(end_user_id))
            if resolved:
                logger.info(
                    f"[CELERY WRITE] end_user_id merged, redirecting: "
                    f"{end_user_id} → {resolved.id}"
                )
                resolved_end_user_id = str(resolved.id)
    except Exception as e:
        logger.warning(
            f"[CELERY WRITE] merge resolution failed for {end_user_id}: {e}, "
            f"falling back to original ID"
        )

    # RAG 存储类型走独立路径
    if storage_type and storage_type.lower() == "rag":
        try:
            async def _rag_write():
                from app.core.memory.memory_service import MemoryService
                await MemoryService.write_messages_to_rag(
                    messages=messages,
                    end_user_id=resolved_end_user_id,
                    user_rag_memory_id=user_rag_memory_id,
                )

            loop.run_until_complete(_rag_write())
            return {"status": "SUCCESS", "result": "rag_write_complete", "task_id": self.request.id}
        except Exception as e:
            logger.error(f"[CELERY WRITE] RAG write failed: {e}", exc_info=True)
            return {"status": "FAILURE", "error": str(e), "task_id": self.request.id}
        finally:
            if loop:
                _shutdown_loop_gracefully(loop)

    # 新格式：直接调用 MemoryService.write()
    logger.info(
        f"[CELERY WRITE] Starting - end_user_id={resolved_end_user_id}, "
        f"config_id={config_id}, conv={conversation_id or '-'}, "
        f"seq={message_seq}, language={language}"
    )
    start_time = time.time()

    async def _run() -> dict:
        from app.core.memory.memory_service import MemoryService

        service = MemoryService(
            config_id=uuid.UUID(config_id),
            end_user_id=resolved_end_user_id,
            workspace_id=workspace_id,
            language=language,
        )

        result = await service.write(
            target_message=target_message or {"role": "user", "content": ""},
            context_before=context_before or [],
            context_after=context_after or [],
            conversation_id=conversation_id,
            message_seq=message_seq,
            language=language,
            skip_cursor_advance=skip_cursor_advance,
            dispatch_at=dispatch_at,
            source=source,
        )
        return {"status": result.status, "extraction": result.extraction}

    try:
        task_start_time = int(time.time())

        result = loop.run_until_complete(_run())
        elapsed_time = time.time() - start_time

        logger.info(f"[CELERY WRITE] Task completed - elapsed_time={elapsed_time:.2f}s")

        # 记录最近一次写入完成时间戳
        redis_client = get_sync_redis_client()
        try:
            if redis_client is not None:
                from datetime import timezone as _tz
                _now_utc = to_iso_z(datetime.now(_tz.utc))
                redis_client.set(f"write_message:last_done:{resolved_end_user_id}", _now_utc, ex=86400 * 30)
        except Exception as _e:
            logger.warning(f"[CELERY WRITE] 写入 last_done 时间戳失败: {_e}")

        # 同步 end_user 记忆计数
        try:
            from app.core.memory.utils.memory_count_utils import sync_end_user_memory_count_from_neo4j
            from app.repositories.neo4j.neo4j_connector import Neo4jConnector

            async def _sync_count():
                connector = Neo4jConnector()
                try:
                    return await sync_end_user_memory_count_from_neo4j(resolved_end_user_id, connector)
                finally:
                    await connector.close()

            loop.run_until_complete(_sync_count())
        except Exception as _count_e:
            logger.warning(f"[CELERY WRITE] 同步记忆计数失败: {_count_e}")

        # 刷新「最后写入时间」：用于反思活跃用户判断，覆盖 API/MCP 等不更新 conversations 行的写入
        try:
            from app.services.memory_reflection_service import WorkspaceAppService
            with get_db_context() as db:
                WorkspaceAppService(db).update_end_user_write_time(resolved_end_user_id)
        except Exception as _wt_e:
            logger.warning(f"[CELERY WRITE] 更新 write_time 失败: {_wt_e}")

        try:
            safe_result = jsonable_encoder(result)
        except Exception:
            safe_result = str(result)

        return {
            "status": "SUCCESS",
            "result": safe_result,
            "start_at": task_start_time,
            "end_user_id": end_user_id,
            "config_id": str(config_id) if config_id else None,
            "elapsed_time": elapsed_time,
            "task_id": self.request.id,
        }
    except BaseException as e:
        from app.schemas.memory_config_schema import (
            ModelNotFoundError,
            ModelInactiveError,
            InvalidConfigError,
        )

        elapsed_time = time.time() - start_time
        if hasattr(e, 'exceptions'):
            error_messages = [f"{type(sub_e).__name__}: {str(sub_e)}" for sub_e in e.exceptions]
            detailed_error = "; ".join(error_messages)
        else:
            detailed_error = str(e)

        logger.error(f"[CELERY WRITE] Task failed - elapsed_time={elapsed_time:.2f}s, error={detailed_error}",
                     exc_info=True)

        # 配置类确定性错误：直接 raise，让 Celery 将任务标记为 FAILURE，不触发重试
        if isinstance(e, (ModelNotFoundError, ModelInactiveError, InvalidConfigError)):
            logger.error(
                f"[CELERY WRITE] Configuration error detected, task will not be retried - "
                f"error_type={type(e).__name__}, error={detailed_error}"
            )
            raise

        # 瞬时错误（网络超时、Neo4j 死锁等）：交由 Celery 按 max_retries 自动重试
        raise self.retry(exc=e)
    finally:
        if loop:
            _shutdown_loop_gracefully(loop)


@celery_app.task(
    name="app.core.memory.fast_write_message",
    bind=True,
    acks_late=False,
    max_retries=0
)
def fast_write_message_task(
        self,
        end_user_id: str,
        target_message: Optional[dict] = None,
        config_id: str = "",
        workspace_id: str = "",
        conversation_id: str = "",
        message_seq: int = 0,
        language: str = "zh",
        dispatch_at: str = "",
        source: str = "",
) -> Dict[str, Any]:
    """快速写入任务 — 构造 MemoryService 并驱动 fast_write。

    职责：提供事件循环 + 计时 + backend 状态映射，不夹带业务加载逻辑。

    backend 状态与业务结果分层：
    - ``success`` / ``dropped`` 是 Pipeline 业务结果，放在返回值的 ``result`` 中；
      任务正常返回时 backend 为 ``SUCCESS``。
    - 持久化 / 配置 / 代码异常必须抛出任务函数，backend 才会标记 ``FAILURE``，
      scheduler tracker 与失败率监控才能拿到真实状态。
    - ``max_retries=0``：不做 Celery 层重试；Neo4j deadlock 的有界重试在 Pipeline 内完成。

    Args:
        end_user_id: 终端用户 ID（分片键）
        target_message: 目标消息 {"role": "user", "content": "...", "dialog_at": "..."}
        config_id: 记忆配置 ID
        workspace_id: 工作空间 ID
        conversation_id: 对话 ID（会话类入口非空，用于确定性 ID 生成）
        message_seq: 消息序号
        language: 语言
        dispatch_at: 任务派发时刻的 UTC ISO 8601 时间戳
        source: 写入来源（agent/service_api/mcp/workflow）

    Returns:
        Dict containing status, result, task_id
    """
    # 解析 end_user_id：若排队期间用户已被合并，自动路由到目标用户
    resolved_end_user_id = end_user_id
    try:
        with get_db_context() as db:
            from app.repositories.end_user_repository import EndUserRepository
            repo = EndUserRepository(db)
            resolved = repo.resolve_merge_by_origin_id(uuid.UUID(end_user_id))
            if resolved:
                logger.info(
                    f"[CELERY FAST WRITE] end_user_id merged, redirecting: "
                    f"{end_user_id} → {resolved.id}"
                )
                resolved_end_user_id = str(resolved.id)
    except Exception as e:
        logger.warning(
            f"[CELERY FAST WRITE] merge resolution failed for {end_user_id}: {e}, "
            f"falling back to original ID"
        )

    logger.info(
        f"[CELERY FAST WRITE] Starting - end_user_id={resolved_end_user_id}, "
        f"config_id={config_id}, conv={conversation_id or '-'}, "
        f"seq={message_seq}, language={language}, source={source or '-'}"
    )
    start_time = time.time()

    async def _run() -> dict:
        from app.core.memory.memory_service import MemoryService

        service = MemoryService(
            config_id=uuid.UUID(config_id),
            end_user_id=resolved_end_user_id,
            workspace_id=workspace_id,
            language=language,
        )

        return await service.fast_write(
            target_message=target_message or {"role": "user", "content": ""},
            conversation_id=conversation_id,
            message_seq=message_seq,
            source=source,
            dispatch_at=dispatch_at,
        )

    loop = None
    try:
        loop = set_asyncio_event_loop()

        result = loop.run_until_complete(_run())
        elapsed_time = time.time() - start_time

        logger.info(f"[CELERY FAST WRITE] Task completed - elapsed_time={elapsed_time:.2f}s")

        try:
            safe_result = jsonable_encoder(result)
        except Exception:
            safe_result = str(result)

        return {
            "status": "SUCCESS",
            "result": safe_result,
            "task_id": self.request.id,
        }
    except BaseException:
        elapsed_time = time.time() - start_time
        logger.exception(f"[CELERY FAST WRITE] Failed - elapsed_time={elapsed_time:.2f}s")
        # 异常必须逃出任务函数，Celery backend 才会标记 FAILURE
        raise
    finally:
        if loop:
            _shutdown_loop_gracefully(loop)


def _is_active_recently(db, end_user_id: str, inactive_hours: int | None = None) -> bool:
    """用户是否活跃：end_user.write_time 距今 < inactive_hours 小时（NULL 或读取失败视为不活跃）。

    write_time 由 write_message_task 写入成功后刷新，覆盖 API / MCP 等全部写入路径。
    inactive_hours 为 None 时取 settings.REFLECT_LAYER2_INACTIVE_HOURS。
    """
    from app.services.memory_reflection_service import WorkspaceAppService

    if inactive_hours is None:
        inactive_hours = settings.REFLECT_LAYER2_INACTIVE_HOURS

    last_write = WorkspaceAppService(db).get_end_user_write_time(end_user_id)
    if last_write is None:
        return False
    last_write = as_utc_aware(last_write).replace(tzinfo=None)
    return (utcnow_naive() - last_write).total_seconds() / 3600 < inactive_hours


def _should_skip_reflection_by_inactivity(db, end_user_id: str, inactive_hours: int | None = None) -> bool:
    """低频反思前置过滤：不活跃则跳过（True=跳过，False=执行）。

    仅按 write_time 做活跃过滤，不含周期判断——低频全量去重的增量节奏由
    run_dedup_full_scan 内部按实体类型的扫描时间自行控制。
    """
    return not _is_active_recently(db, end_user_id, inactive_hours)


def _should_reflect_now(db, end_user_id: str, reflection_time, iteration_period: int) -> bool:
    """高频反思：判断该用户现在是否需要反思。scan 派发前和 do 执行前都用它（保证一致 + 幂等）。

    放行需同时满足：活跃（_is_active_recently，口径 write_time）+ 
    到周期（距上次反思 reflection_time >= iteration_period 小时）。
    reflection_time 为 None 表示从未反思，活跃即放行（首次反思）。
    """
    if not _is_active_recently(db, end_user_id):
        return False  # 不活跃（无 write_time 或距今超阈值）→ 无需反思

    now = utcnow_naive()
    if reflection_time is None:
        return True  # 从未反思：活跃即放行（首次反思）

    reflection_time = as_utc_aware(reflection_time).replace(tzinfo=None)  # 统一 naive UTC
    period_reached = (now - reflection_time).total_seconds() / 3600 >= iteration_period  # 距上次反思够周期
    return period_reached


@celery_app.task(
    name="app.tasks.scan_layer2_reflection",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
)
def scan_layer2_reflection(self) -> Dict[str, Any]:
    """高频反思扫描器：遍历所有用户，筛选出需要反思的，派发 do_layer2_reflection。
    轻量、无事件循环、无单例锁、无超时。
    """
    start_time = time.time()
    from app.models.workspace_model import Workspace
    from app.services.memory_reflection_service import WorkspaceAppService

    redis_client = get_sync_redis_client()
    dispatched = 0
    dispatched_user_ids = []
    skip_period_or_new = 0
    skip_inflight = 0

    # db-session 规范：先用只读短 session 取 workspace 列表，
    # 再【按 workspace 粒度】开独立 session，处理完即释放，避免 identity-map 累积。
    with get_db_read() as db:
        workspace_ids = [str(w.id) for w in db.query(Workspace.id).all()]

    for ws_id in workspace_ids:
        ws_id_uuid = uuid.UUID(ws_id)
        with get_db_context() as db:
            service = WorkspaceAppService(db)
            memory_config_service = MemoryConfigService(db)
            try:
                config_id = memory_config_service.get_workspace_active_config_id(ws_id_uuid)
                config = memory_config_service.load_memory_config(config_id)
            except Exception as e:
                # 单个 workspace 配置异常（无启用配置 / 缺 embedding / 模型被删）只跳过该 workspace
                logger.warning(f"高频反思scan 跳过配置异常的 workspace={ws_id}: {e}")
                continue
            iteration_period = config.reflexion_iteration_period or 24
            end_users = get_end_users_by_workspace(db, ws_id_uuid)
            for user in end_users:
                uid = str(user.id)
                try:
                    rt = service.get_end_user_reflection_time(uid)
                    if not _should_reflect_now(db, uid, rt, iteration_period):  # 频率+活跃+有新对话
                        skip_period_or_new += 1
                        continue
                    # 在途锁：抢不到说明该用户已有反思任务在途，跳过（纯 SET NX EX 粗过滤）
                    if redis_client is not None:
                        ok = redis_client.set(
                            f"reflection:inflight:{uid}", "1", nx=True, ex=1500,
                        )
                        if not ok:
                            skip_inflight += 1
                            continue
                    do_layer2_reflection.apply_async(
                        kwargs={
                            "end_user_id": uid,
                            "config_id": str(config_id),
                            "workspace_id": ws_id,
                            "iteration_period": iteration_period,
                        },
                        queue="reflection_tasks",
                    )
                    dispatched += 1
                    dispatched_user_ids.append(uid)
                    # 每派发 10 个用户打印一次进度
                    if dispatched % 10 == 0:
                        logger.info(
                            f"scan_layer2_reflection 进度: 已派发 {dispatched} 个用户, "
                            f"最近10个: {dispatched_user_ids[-10:]}"
                        )
                except Exception as e:
                    logger.error(f"高频反思scan 处理用户失败 user={uid}: {e}")
                    try:
                        db.rollback()
                    except Exception:
                        pass

    logger.info(
        f"scan_layer2_reflection 完成: 派发 {dispatched} {dispatched_user_ids}, "
        f"跳过(未到周期/无新增) {skip_period_or_new}, 在途 {skip_inflight}, "
        f"耗时 {time.time() - start_time:.1f}s"
    )
    return {"status": "SUCCESS", "dispatched": dispatched,
            "dispatched_user_ids": dispatched_user_ids,
            "skip_period_or_new": skip_period_or_new, "skip_inflight": skip_inflight}


@celery_app.task(
    name="app.tasks.do_layer2_reflection",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=600,
    soft_time_limit=540,
)
def do_layer2_reflection(self, end_user_id: str | None = None, config_id: str = "",
                         workspace_id: str = "", iteration_period: int = 24,
                         from_retry: bool = False, user_id: str | None = None) -> Dict[str, Any]:
    """对【单个用户】执行一次 Layer2 反思（实体去重 / 描述合并 / 未识别实体处理等）。

    由 scan_layer2_reflection 派发，每个用户一个独立任务、独立 db session，跑完即释放内存。
    返回 status 取值：
        success            反思成功执行
        skipped_idempotent 执行前发现已不需要反思（排队期间被别的任务做过）
        lock_timeout       抢用户写锁超时，本次放弃（下一轮 scan 会重派）
        failed             执行报错
    """
    # HACK: 兼容旧参数 user_id，v0.3.15 后移除
    end_user_id = end_user_id or user_id
    if not end_user_id:
        raise ValueError("end_user_id is required")

    start_time = time.time()
    inflight_key = f"reflection:inflight:{end_user_id}"

    async def _run() -> Dict[str, Any]:
        from app.services.memory_reflection_service import WorkspaceAppService
        from app.core.memory.memory_service import MemoryService

        # 步骤1 执行前再判一次是否真的要反思：
        #   任务从 scan 派发到这里可能排队了一段时间，期间该用户可能已被别的
        #   反思任务处理过（reflection_time 已更新），这里复判避免重复反思。
        #   from_retry=True（重试派发）跳过活跃/周期幂等门，否则刚被闸门挡掉的用户重派进来又被自己挡掉。
        if not from_retry:
            with get_db_read() as db:
                ws_svc = WorkspaceAppService(db)
                rt = ws_svc.get_end_user_reflection_time(end_user_id)
                if not _should_reflect_now(db, end_user_id, rt, iteration_period):
                    return {"status": "skipped_idempotent"}

        # 步骤2 抢该用户的写锁：与该用户的记忆写入 pipeline、去重任务互斥，
        #   保证同一用户的图谱不被并发修改。抢不到（超时30s）就本次放弃。
        write_lock = None
        redis_client = get_sync_redis_client()
        if redis_client is not None:
            write_lock = RedisFairLock(
                key=f"memory_write:{end_user_id}",
                redis_client=redis_client,
                expire=600, timeout=30, auto_renewal=True,
            )
            if not await asyncio.to_thread(write_lock.acquire):
                logger.warning(f"反思高频do 获取写锁超时，跳过 user={end_user_id}")
                return {"status": "lock_timeout"}
        try:
            # 步骤2.5 double-check：拿到写锁后再复查一次是否仍需反思。
            #   并发下（concurrency>1 或多 worker 副本）另一个 do 可能在我们抢锁
            #   期间已完成同一用户的反思并刷新了 reflection_time，
            #   不满足则放弃，避免同一批数据被反思两次。from_retry 同样跳过该门。
            if not from_retry:
                with get_db_read() as db:
                    ws_svc = WorkspaceAppService(db)
                    rt_recheck = ws_svc.get_end_user_reflection_time(end_user_id)
                    if not _should_reflect_now(db, end_user_id, rt_recheck, iteration_period):
                        logger.info(f"反思高频do 拿锁后复查已无需反思，跳过 user={end_user_id}")
                        return {"status": "skipped_idempotent"}

            # 步骤2.8 开工租约：通过幂等门 + 抢到写锁后、run() 前登记，进程被硬杀也能被租约兜底重派。
            _rc = get_sync_redis_client()
            rr.lease(_rc, "high_freq", end_user_id,
                     {"config_id": config_id, "workspace_id": workspace_id,
                      "iteration_period": iteration_period},
                     from_retry=from_retry)

            # 步骤3 执行反思（读图谱 → LLM → 写回，全程持锁）
            memory_service = MemoryService(
                config_id=uuid.UUID(config_id),
                end_user_id=end_user_id,
                workspace_id=workspace_id,
            )
            r = await memory_service.run_reflection_layer2()

            completion = rr.completion_of_layer2(r)
            progressed = rr.progressed_layer2(r)

            unresolved_info = r.get("unresolved_entity", {})
            alias_info = r.get("alias_merge", {})
            dedup_info = r.get("entity_dedup", {})
            meta_info = r.get("metadata_extraction", {})
            merge_info = r.get("description_merge", {})

            if completion == "full":
                # 步骤4 完整跑完：刷新"上次反思时间"，注销重试登记
                with get_db_context() as db:
                    WorkspaceAppService(db).update_end_user_reflection_time(end_user_id)
                rr.resolve(_rc, "high_freq", end_user_id)
                logger.info(
                    f"反思高频do 完成 user={end_user_id} status=success "
                    f"未识别解析={unresolved_info.get('resolved', 0)}/{unresolved_info.get('total', 0)} "
                    f"别名归并={alias_info.get('alias_merged', 0)} "
                    f"实体去重={dedup_info.get('merged_count', 0)}(候选{dedup_info.get('candidate_count', 0)}) "
                    f"元数据提取={meta_info.get('extracted', 0)} "
                    f"描述合并={merge_info.get('merged_count', 0)}(候选{merge_info.get('candidate_count', 0)}) "
                    f"耗时={time.time() - start_time:.1f}s"
                )
                # 返回各步骤关键计数（扁平标量，便于 Flower / 调用方一眼查看）
                return {
                    "status": "success",
                    "unresolved_resolved": unresolved_info.get("resolved", 0),
                    "alias_merged": alias_info.get("alias_merged", 0),
                    "dedup_merged": dedup_info.get("merged_count", 0),
                    "metadata_extracted": meta_info.get("extracted", 0),
                    "desc_merged": merge_info.get("merged_count", 0),
                }

            # partial：有步骤被熔断跳过/超时。已有推进，刷新 reflection_time（重派交给重试队列独占）。
            # failed 不会到这里（真异常冒到外层 except 处理）。
            if completion == "partial":
                with get_db_context() as db:
                    WorkspaceAppService(db).update_end_user_reflection_time(end_user_id)
            skipped_steps = rr.skipped_steps_of_layer2(r)
            rr.record(_rc, "high_freq", end_user_id, completion, progressed,
                      skipped_steps=skipped_steps)
            logger.warning(
                f"反思高频do 未完整完成 user={end_user_id} completion={completion} "
                f"progressed={progressed} skipped={skipped_steps} "
                f"耗时={time.time() - start_time:.1f}s"
            )
            # 收尾已 record/refresh。partial 不当报错：正常 return（Celery SUCCESS），
            # Result 带 status=partial + 提示，便于在 flower 一眼区分「熔断未完成」与真报错(FAILURE)。
            return {
                "status": "partial",
                "progressed": progressed,
                "skipped": skipped_steps,
                "note": "步骤级熔断/未完成（预期，非报错）；已登记重试队列，后续多轮收敛",
            }
        finally:
            # 步骤5 释放写锁（无论成功失败）
            if write_lock is not None:
                await asyncio.to_thread(write_lock.release)

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
    except Exception as e:
        # 真异常：run() 抛出未达 completion 逻辑，补登记 failed（无推进），再 re-raise（FAILURE + traceback，需排查）
        logger.error(f"反思高频do 失败 user={end_user_id}: {e}", exc_info=True)
        try:
            _rc = get_sync_redis_client()
            rr.record(_rc, "high_freq", end_user_id, "failed", progressed=False, last_error=str(e))
        except Exception:
            pass
        raise
    finally:
        _shutdown_loop_gracefully(loop)
        # 步骤6 删除在途标记：放行下一轮 scan 对该用户的派发（成功/失败/跳过都要删）
        try:
            _rc = get_sync_redis_client()
            if _rc is not None:
                _rc.delete(inflight_key)
        except Exception:
            pass
    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    return result


@celery_app.task(
    name="app.tasks.scan_layer2_dedup_full_scan",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
)
def scan_layer2_dedup_full_scan(self) -> Dict[str, Any]:
    """低频去重扫描器：遍历用户，启用反思 + 最近活跃 + 未在途 的派发 do_layer2_dedup_full_scan。"""
    start_time = time.time()
    from app.models.workspace_model import Workspace

    redis_client = get_sync_redis_client()
    dispatched = 0
    dispatched_user_ids = []
    skip_inactive = 0
    skip_inflight = 0

    # db-session 规范：先用只读短 session 取 workspace 列表，
    # 再【按 workspace 粒度】开独立 session，处理完即释放，避免 identity-map 累积。
    with get_db_read() as db:
        workspace_ids = [str(w.id) for w in db.query(Workspace.id).all()]

    for ws_id in workspace_ids:
        ws_id_uuid = uuid.UUID(ws_id)
        with get_db_context() as db:
            memory_config_service = MemoryConfigService(db)
            try:
                config_id = memory_config_service.get_workspace_active_config_id(ws_id_uuid)
                config = memory_config_service.load_memory_config(config_id)
            except Exception as e:
                # 单个 workspace 配置异常（无启用配置 / 缺 embedding / 模型被删）只跳过该 workspace
                logger.warning(f"反思低频去重scan 跳过配置异常的 workspace={ws_id}: {e}")
                continue
            if not config.reflexion_enabled:
                continue
            for user in get_end_users_by_workspace(db, ws_id_uuid):
                uid = str(user.id)
                try:
                    # 最近活跃度过滤（复用现有函数，阈值取 settings）
                    if _should_skip_reflection_by_inactivity(db, uid):
                        skip_inactive += 1
                        continue
                    # 在途锁：抢不到说明该用户已有去重任务在途，跳过（独立 key）
                    if redis_client is not None:
                        ok = redis_client.set(
                            f"dedup:inflight:{uid}", "1", nx=True, ex=1500,
                        )
                        if not ok:
                            skip_inflight += 1
                            continue
                    do_layer2_dedup_full_scan.apply_async(
                        kwargs={
                            "end_user_id": uid,
                            "config_id": str(config_id),
                            "workspace_id": ws_id,
                        },
                        queue="reflection_tasks",
                    )
                    dispatched += 1
                    dispatched_user_ids.append(uid)
                except Exception as e:
                    logger.error(f"反思低频去重scan 处理用户失败 user={uid}: {e}")
                    try:
                        db.rollback()
                    except Exception:
                        pass

    logger.info(
        f"scan_layer2_dedup_full_scan 完成: 派发 {dispatched} {dispatched_user_ids}, "
        f"跳过(不活跃) {skip_inactive}, 在途 {skip_inflight}, "
        f"耗时 {time.time() - start_time:.1f}s"
    )
    return {"status": "SUCCESS", "dispatched": dispatched,
            "dispatched_user_ids": dispatched_user_ids,
            "skip_inactive": skip_inactive, "skip_inflight": skip_inflight}


@celery_app.task(
    name="app.tasks.do_layer2_dedup_full_scan",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=600,
    soft_time_limit=540,
)
def do_layer2_dedup_full_scan(self, end_user_id: str | None = None, config_id: str = "",
                              workspace_id: str = "", from_retry: bool = False,
                              user_id: str | None = None) -> Dict[str, Any]:
    """对【单个用户】执行一次低频全量去重扫描。

    由 scan_layer2_dedup_full_scan 派发。精确的增量判断在 run_dedup_full_scan 内部
    （check_new_entities 按实体类型查 Neo4j 新增数），do 这层不重复做。
    返回 status：success / lock_timeout / failed。
    """
    # HACK: 兼容旧参数 user_id，v0.3.15 后移除
    end_user_id = end_user_id or user_id
    if not end_user_id:
        raise ValueError("end_user_id is required")

    start_time = time.time()
    inflight_key = f"dedup:inflight:{end_user_id}"

    async def _run() -> Dict[str, Any]:
        from app.core.memory.memory_service import MemoryService

        # 抢该用户写锁：与反思 do、写入 pipeline 互斥。
        # 去重低频、半夜跑，给更长抢锁等待（120s），避免被高频反思挤掉；抢不到本次放弃。
        write_lock = None
        redis_client = get_sync_redis_client()
        if redis_client is not None:
            write_lock = RedisFairLock(
                key=f"memory_write:{end_user_id}",
                redis_client=redis_client,
                expire=600, timeout=120, auto_renewal=True,
            )
            if not await asyncio.to_thread(write_lock.acquire):
                logger.warning(f"反思低频去重do 获取写锁超时，跳过 user={end_user_id}")
                return {"status": "lock_timeout"}
        try:
            _rc = get_sync_redis_client()
            rr.lease(_rc, "dedup", end_user_id,
                     {"config_id": config_id, "workspace_id": workspace_id},
                     from_retry=from_retry)

            memory_service = MemoryService(
                config_id=uuid.UUID(config_id),
                end_user_id=end_user_id, 
                workspace_id=workspace_id,
            )
            r = await memory_service.run_dedup_full_scan()
            completion = rr.completion_of_dedup(r)
            progressed = rr.progressed_dedup(r)
            merged = r.get("merged_count", 0)

            if completion == "full":
                rr.resolve(_rc, "dedup", end_user_id)
                logger.info(
                    f"反思低频去重do 完成 user={end_user_id} status=success "
                    f"扫描类型={r.get('scanned_types', 0)} 合并={merged} "
                    f"耗时={time.time() - start_time:.1f}s"
                )
                return {"status": "success", "merged_count": merged}

            # partial：truncated / had_type_error。低频不刷 reflection_time（靠 update_scan_time 续扫）。
            rr.record(_rc, "dedup", end_user_id, completion, progressed)
            logger.warning(
                f"反思低频去重do 未完整完成 user={end_user_id} completion={completion} "
                f"progressed={progressed} truncated={r.get('truncated')} "
                f"had_type_error={r.get('had_type_error')} 合并={merged} "
                f"耗时={time.time() - start_time:.1f}s"
            )
            # partial 不当报错：正常 return（Celery SUCCESS），Result 带 status=partial + 提示。
            return {
                "status": "partial",
                "progressed": progressed,
                "merged_count": merged,
                "truncated": bool(r.get("truncated")),
                "had_type_error": bool(r.get("had_type_error")),
                "note": "低频去重未扫完（预期，非报错）；已登记重试队列，后续多轮收敛",
            }
        finally:
            if write_lock is not None:
                await asyncio.to_thread(write_lock.release)

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
    except Exception as e:
        logger.error(f"反思低频去重do 失败 user={end_user_id}: {e}", exc_info=True)
        try:
            _rc = get_sync_redis_client()
            rr.record(_rc, "dedup", end_user_id, "failed", progressed=False, last_error=str(e))
        except Exception:
            pass
        raise
    finally:
        _shutdown_loop_gracefully(loop)
        # 删除在途标记：放行下一轮 scan 对该用户的派发（成功/失败都删）
        try:
            _rc = get_sync_redis_client()
            if _rc is not None:
                _rc.delete(inflight_key)
        except Exception:
            pass
    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    return result


@celery_app.task(
    name="app.tasks.scan_reflection_retry",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
)
def scan_reflection_retry(self) -> Dict[str, Any]:
    """重试派发：扫两个重试 ZSet，对「已到点」用户绕活跃闸门重派对应 do。

    租约到期且仍 in_progress → 判进程死亡 mark_dead；meta 缺失的孤儿 member → zrem 清理；
    仍走 inflight 锁与正常 scan 互斥去重；派发的 do 进 reflection_tasks 队列（与正常 scan 同队列）。
    Redis 不可用时整轮 no-op，不影响反思主流程。
    """
    start_time = time.time()
    rc = get_sync_redis_client()
    if rc is None:
        logger.warning("scan_reflection_retry: Redis 不可用，跳过本轮")
        return {"status": "SKIPPED", "reason": "redis_unavailable"}

    now = time.time()
    batch = rr.RETRY_BATCH
    dispatched = 0
    cleaned = 0
    dispatched_uids: Dict[str, List[str]] = {"high_freq": [], "dedup": []}

    for task_type, do_task, inflight_prefix in (
        ("high_freq", do_layer2_reflection, "reflection:inflight"),
        ("dedup", do_layer2_dedup_full_scan, "dedup:inflight"),
    ):
        zkey = f"reflection:retry:{task_type}"
        try:
            uids = rc.zrangebyscore(zkey, "-inf", now, start=0, num=batch)
        except Exception as e:
            logger.warning(f"scan_reflection_retry: zrangebyscore 失败 {zkey}: {e}")
            continue
        for uid in uids:
            if isinstance(uid, bytes):
                uid = uid.decode("utf-8")
            try:
                meta = rr.load_meta(rc, task_type, uid)
                if not meta:
                    rc.zrem(zkey, uid)            # 孤儿（meta 已 TTL 过期）→ 清理
                    cleaned += 1
                    continue
                if meta.get("completion") == "exhausted":
                    continue
                if meta.get("completion") == "in_progress":   # 租约到期 = 上次开工后进程死亡
                    if not rr.mark_dead(rc, task_type, uid):   # 达 dead 上限置 exhausted 返回 False
                        continue
                # 仍走 inflight 锁，避免与正常 scan 派的同一用户撞车
                if not rc.set(f"{inflight_prefix}:{uid}", "1", nx=True, ex=1500):
                    continue
                kwargs = {"end_user_id": uid, "config_id": meta["config_id"],
                          "workspace_id": meta["workspace_id"], "from_retry": True}
                if task_type == "high_freq":
                    kwargs["iteration_period"] = meta.get("iteration_period", 24)
                do_task.apply_async(kwargs=kwargs, queue="reflection_tasks")
                dispatched += 1
                dispatched_uids[task_type].append(uid)
            except Exception as e:
                logger.error(f"scan_reflection_retry 处理用户失败 task_type={task_type} uid={uid}: {e}")

    logger.info(f"scan_reflection_retry 完成: 派发 {dispatched}, 清理孤儿 {cleaned}, "
                f"耗时 {time.time() - start_time:.1f}s")
    return {
        "status": "SUCCESS",
        "dispatched": dispatched,
        "cleaned": cleaned,
        "dispatched_uids": dispatched_uids,
    }


# unused task
#     """Call read_service and write latest status to Redis.

#     Returns status data dict that gets written to Redis.
#     """
#     client = redis.Redis(
#         host=settings.REDIS_HOST,
#         port=settings.REDIS_PORT,
#         db=settings.REDIS_DB,
#         password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None
#     )
#     try:
#         api_url = f"http://{settings.SERVER_IP}:8000/api/memory/read_service"
#         payload = {
#             "user_id": "健康检查",
#             "apply_id": "健康检查",
#             "group_id": "健康检查",
#             "message": "你好",
#             "history": [],
#             "search_switch": "2",
#         }
#         resp = requests.post(api_url, json=payload, timeout=15)
#         ok = resp.status_code == 200
#         status = "Success" if ok else "Fail"
#         msg = "接口请求成功" if ok else f"接口请求失败: {resp.status_code}"
#         error = "" if ok else resp.text
#         code = 0 if ok else 500
#     except Exception as e:
#         status = "Fail"
#         msg = "接口请求失败"
#         error = str(e)
#         code = 500

#     data = {
#         "status": status,
#         "msg": msg,
#         "error": error,
#         "code": str(code),
#         "time": str(int(time.time())),
#     }

#     client.hset("memsci:health:read_service", mapping=data)
#     client.expire("memsci:health:read_service", int(settings.HEALTH_CHECK_SECONDS))

#     return data


@celery_app.task(name="app.tasks.write_total_memory_task")
def write_total_memory_task(workspace_id: str) -> Dict[str, Any]:
    """定时任务：查询工作空间下所有宿主的记忆总量并写入数据库

    Args:
        workspace_id: 工作空间ID

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.models.app_model import App
        from app.repositories.end_user_repository import EndUserRepository
        from app.repositories.memory_increment_repository import MemoryIncrementRepository
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.services.memory_storage_service import search_all_batch

        workspace_uuid = uuid.UUID(workspace_id)

        # --- Session A：查询 apps + end_users，立即关闭 ---
        has_apps = False
        end_user_id_list: list[str] = []
        with get_db_context() as db:
            apps = db.query(App).filter(
                App.workspace_id == workspace_uuid,
                App.is_active.is_(True)
            ).all()
            has_apps = len(apps) > 0

            if has_apps:
                end_user_repo = EndUserRepository(db)
                end_users = end_user_repo.get_end_users_by_workspace(workspace_uuid)
                end_user_id_list = [str(eu.id) for eu in end_users]

        # 没有 app 时直接写入 0
        if not has_apps:
            with get_db_context() as db:
                memory_increment = MemoryIncrementRepository(db).write_memory_increment(
                    workspace_id=workspace_uuid,
                    total_num=0
                )
                return {
                    "status": "SUCCESS",
                    "workspace_id": workspace_id,
                    "total_num": 0,
                    "end_user_count": 0,
                    "memory_increment_id": str(memory_increment.id),
                    "created_at": to_iso_z(memory_increment.created_at),
                }

        # --- Neo4j 查询：用独立 connector 避免跨 loop 问题 ---
        connector = Neo4jConnector()
        try:
            batch_result = await search_all_batch(end_user_id_list, connector=connector)
        finally:
            await connector.close()

        total_num = sum(batch_result.values())
        end_user_details = [
            {"end_user_id": uid, "total": batch_result.get(uid, 0)}
            for uid in end_user_id_list
        ]

        # --- Session B：写入统计结果 ---
        with get_db_context() as db:
            memory_increment = MemoryIncrementRepository(db).write_memory_increment(
                workspace_id=workspace_uuid,
                total_num=total_num
            )

            return {
                "status": "SUCCESS",
                "workspace_id": workspace_id,
                "total_num": total_num,
                "end_user_count": len(end_user_id_list),
                "end_user_details": end_user_details,
                "memory_increment_id": str(memory_increment.id),
                "created_at": to_iso_z(memory_increment.created_at), # 这样返回字符串是否正确
            }

    try:
        # 尝试获取现有事件循环，如果不存在则创建新的（与 write_all_workspaces_memory_task 一致，
        # 避免 asyncio.run 每次新建并关闭 loop，导致进程内共享 Neo4j driver 跨 loop 复用报错）
        loop = set_asyncio_event_loop()

        result = loop.run_until_complete(_run())
        elapsed_time = time.time() - start_time
        result["elapsed_time"] = elapsed_time
        return result
    except Exception as e:
        elapsed_time = time.time() - start_time
        return {
            "status": "FAILURE",
            "error": str(e),
            "workspace_id": workspace_id,
            "elapsed_time": elapsed_time,
        }


@celery_app.task(
    name="app.tasks.write_all_workspaces_memory_task",
    bind=True,
    ignore_result=False,
    max_retries=3,
    acks_late=True,
    time_limit=3600,
    soft_time_limit=3300,
)
def write_all_workspaces_memory_task(self) -> Dict[str, Any]:
    """定时任务：遍历所有工作空间，统计并写入记忆增量

    此任务会：
    1. 查询所有活跃的工作空间
    2. 对每个工作空间统计记忆总量
    3. 将统计结果写入 memory_increments 表

    改造说明：拆分 DB session 与 Neo4j 查询，避免 PG 连接在 Neo4j I/O 期间空占。

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.models.app_model import App
        from app.models.workspace_model import Workspace
        from app.repositories.end_user_repository import EndUserRepository
        from app.repositories.memory_increment_repository import MemoryIncrementRepository
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.services.memory_storage_service import search_all_batch

        # --- 短 session：获取活跃 workspace 列表后立即关闭 ---
        workspace_list: list[dict] = []
        with get_db_context() as db:
            workspaces = db.query(Workspace.id, Workspace.name).filter(
                Workspace.is_active.is_(True)
            ).all()
            workspace_list = [{"id": workspace.id, "name": workspace.name} for workspace in workspaces]

        if not workspace_list:
            logger.warning("没有找到活跃的工作空间")
            return {
                "status": "SUCCESS",
                "message": "没有找到活跃的工作空间",
                "workspace_count": 0,
                "workspace_results": []
            }

        logger.info(f"开始统计 {len(workspace_list)} 个工作空间的记忆增量")
        results: list[dict] = []

        # 独立 Neo4j connector：绑定当前 loop，避免跨 loop 问题
        connector = Neo4jConnector()
        try:
            # 逐 workspace 处理，每轮独立短 session
            for workspace_info in workspace_list:
                workspace_id = workspace_info["id"]
                workspace_name = workspace_info["name"]

                try:
                    logger.info(f"开始处理工作空间: {workspace_name} (ID: {workspace_id})")

                    # --- Session A：判断是否有活跃 app + 获取 end_users → 关闭 ---
                    end_user_id_list: list[str] = []
                    with get_db_context() as db:
                        has_apps = (
                            db.query(App.id)
                            .filter(App.workspace_id == workspace_id, App.is_active.is_(True))
                            .first()
                            is not None
                        )
                        if has_apps:
                            end_users = EndUserRepository(db).get_end_users_by_workspace(workspace_id)
                            end_user_id_list = [str(eu.id) for eu in end_users]

                    # 无 app 或无 end_user → 直接写 0，跳过 Neo4j 调用
                    if not has_apps or not end_user_id_list:
                        total_num = 0
                    else:
                        # --- Neo4j 查询：无 PG 连接占用 ---
                        batch_result = await search_all_batch(end_user_id_list, connector=connector)
                        total_num = sum(batch_result.values())

                    # --- Session B：写入统计结果 ---
                    with get_db_context() as db:
                        memory_increment = MemoryIncrementRepository(db).write_memory_increment(
                            workspace_id=workspace_id,
                            total_num=total_num,
                        )
                        # 在 session 内提取标量，避免 detached 访问
                        increment_id = str(memory_increment.id)
                        increment_created_at = to_iso_z(memory_increment.created_at)

                    results.append({
                        "workspace_id": str(workspace_id),
                        "workspace_name": workspace_name,
                        "status": "SUCCESS",
                        "total_num": total_num,
                        "end_user_count": len(end_user_id_list),
                        "memory_increment_id": increment_id,
                        "created_at": increment_created_at,
                    })
                    logger.info(
                        f"工作空间 {workspace_name} 统计完成: 总量={total_num}, 用户数={len(end_user_id_list)}"
                    )

                except Exception as e:
                    # 单 workspace 失败不影响其他 workspace
                    logger.error(f"处理工作空间 {workspace_name} (ID: {workspace_id}) 失败: {e}")
                    results.append({
                        "workspace_id": str(workspace_id),
                        "workspace_name": workspace_name,
                        "status": "FAILURE",
                        "error": str(e),
                        "total_num": 0,
                        "end_user_count": 0,
                    })
        finally:
            await connector.close()

        total_memory = sum(r.get("total_num", 0) for r in results)
        success_count = sum(1 for r in results if r["status"] == "SUCCESS")

        return {
            "status": "SUCCESS",
            "message": f"成功处理 {success_count}/{len(workspace_list)} 个工作空间，总记忆量: {total_memory}",
            "workspace_count": len(workspace_list),
            "success_count": success_count,
            "total_memory": total_memory,
            "workspace_results": results,
        }

    try:
        # 尝试获取现有事件循环，如果不存在则创建新的
        loop = set_asyncio_event_loop()

        result = loop.run_until_complete(_run())
        elapsed_time = time.time() - start_time
        result["elapsed_time"] = elapsed_time
        result["task_id"] = self.request.id

        return result
    except Exception as e:
        elapsed_time = time.time() - start_time
        return {
            "status": "FAILURE",
            "error": str(e),
            "elapsed_time": elapsed_time,
            "task_id": self.request.id
        }


# ============================================================
# 洞察/摘要缓存刷新：扫描 + 派发模式（替代旧 refresh_memory_insight_and_summary_cache 单任务）
# ============================================================

# 在途锁 key 与 TTL：TTL 略大于 do 任务的 time_limit(900s)，兜底 worker 崩溃不会永久占用
CACHE_INFLIGHT_KEY_FMT = "insight_summary_cache:inflight:{end_user_id}"
CACHE_INFLIGHT_TTL_SEC = 1800


@celery_app.task(
    name="app.tasks.scan_refresh_insight_summary_cache",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=600,        # 10 分钟硬超时（仅枚举 + 派发，足够）
    soft_time_limit=540,
)
def scan_refresh_insight_summary_cache(self) -> Dict[str, Any]:
    """扫描原始刷新字段，并派发需要更新洞察或摘要的用户。"""
    start_time = time.time()
    from app.core.memory.analytics.memory_insight import classify_memory_cache_refresh
    from app.repositories.end_user_repository import EndUserRepository

    redis_client = get_sync_redis_client()
    dispatched = 0
    dispatched_user_ids: List[str] = []
    skip_no_change = 0      # write_time 为 null 或 数据未变
    skip_fresh = 0          # 数据有变但缓存刚刷过（未到最短刷新间隔）
    skip_inflight = 0       # 在途锁未抢到

    # db-session 规范：先用只读短 session 取 workspace 列表，
    # 再【按 workspace 粒度】开独立 session，处理完即释放，避免 identity-map 累积。
    with get_db_read() as db:
        workspace_ids = EndUserRepository(db).get_all_active_workspaces()

    for ws_id in workspace_ids:
        # 列裁剪查询：返回普通元组，不受 session 关闭后 detach 影响，且内存更省
        with get_db_read() as db:
            rows = EndUserRepository(db).get_neo4j_memory_cache_refresh_fields(ws_id)

        for row in rows:
            eu_id = str(row.end_user_id)
            try:
                decisions = classify_memory_cache_refresh(
                    insight_at=row.memory_insight_updated_at,
                    summary_at=row.user_summary_updated_at,
                    write_at=row.write_time,
                    metadata_updated_at=row.metadata_updated_at,
                )
                refresh_insight = decisions.insight == "dispatch"
                refresh_summary = decisions.summary == "dispatch"
                if not refresh_insight and not refresh_summary:
                    if "skip_fresh" in (decisions.insight, decisions.summary):
                        skip_fresh += 1
                    else:
                        skip_no_change += 1
                    continue

                # 在途锁：抢不到说明该用户已有刷新任务在途，跳过
                if redis_client is not None:
                    ok = redis_client.set(
                        CACHE_INFLIGHT_KEY_FMT.format(end_user_id=eu_id),
                        "1", nx=True, ex=CACHE_INFLIGHT_TTL_SEC,
                    )
                    if not ok:
                        skip_inflight += 1
                        continue

                # 派发：用 countdown 错峰，每 60 个一波、每波摊到 0~295s，平滑 LLM 调用
                countdown = (dispatched % 60) * 5
                do_refresh_insight_summary_cache.apply_async(
                    kwargs={
                        "end_user_id": eu_id,
                        "workspace_id": str(row.workspace_id),
                        "language": "zh",  # 与旧任务行为对齐
                        "refresh_insight": refresh_insight,
                        "refresh_summary": refresh_summary,
                    },
                    countdown=countdown,
                )
                dispatched += 1
                dispatched_user_ids.append(eu_id)
                if dispatched % 50 == 0:
                    logger.info(
                        f"scan_refresh_insight_summary_cache 进度: 已派发 {dispatched}, "
                        f"最近 10 个: {dispatched_user_ids[-10:]}"
                    )
            except Exception as e:
                logger.error(f"洞察/摘要缓存scan 处理用户失败 user={eu_id}: {e}")

    logger.info(
        f"scan_refresh_insight_summary_cache 完成: 派发 {dispatched}, "
        f"跳过(数据未变) {skip_no_change}, 跳过(刚刷过) {skip_fresh}, "
        f"跳过(在途) {skip_inflight}, 耗时 {time.time() - start_time:.1f}s"
    )
    return {
        "status": "SUCCESS",
        "dispatched": dispatched,
        "dispatched_user_ids": dispatched_user_ids,
        "skip_no_change": skip_no_change,
        "skip_fresh": skip_fresh,
        "skip_inflight": skip_inflight,
        "elapsed_time": time.time() - start_time,
        "task_id": self.request.id,
    }


@celery_app.task(
    name="app.tasks.do_refresh_insight_summary_cache",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=900,                         # 15 分钟硬超时
    soft_time_limit=840,                    # 14 分钟软超时
)
def do_refresh_insight_summary_cache(
    self,
    end_user_id: str,
    workspace_id: str,
    language: str = "zh",
    refresh_insight: bool = True,
    refresh_summary: bool = True,
) -> Dict[str, Any]:
    """按 scan 的独立判定刷新单个用户的记忆洞察或用户摘要缓存。

    由 scan_refresh_insight_summary_cache 派发，每个用户一个独立任务；PostgreSQL
    读写使用独立同步短 Session，Neo4j/LLM 保持异步。刷新标记默认开启，
    兼容发布前已入队的旧消息。
    """
    start_time = time.time()
    inflight_key = CACHE_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)

    async def _run() -> Dict[str, Any]:
        from app.services.user_memory_service import UserMemoryService

        service = UserMemoryService()
        ws_uuid = uuid.UUID(workspace_id)
        insight = None
        summary = None

        # 旧 Celery 异步 PG 编排保留如下：
        # async with get_async_db_context() as db:
        #     insight = await service.generate_and_cache_insight(db, ...)
        #     summary = await service.generate_and_cache_summary(db, ...)
        # 模块级 asyncpg pool 会跨 Task/event loop 复用连接，存在
        # "Future attached to a different loop" 和连接协议状态损坏风险。
        if refresh_insight:
            insight = await service.generate_and_cache_insight_for_worker(
                end_user_id,
                ws_uuid,
                language=language,
            )
        if refresh_summary:
            summary = await service.generate_and_cache_summary_for_worker(
                end_user_id,
                ws_uuid,
                language=language,
            )

        insight_success = bool(insight and insight.get("success"))
        summary_success = bool(summary and summary.get("success"))
        return {
            "insight_success": insight_success if refresh_insight else None,
            "summary_success": summary_success if refresh_summary else None,
            "insight_status": (
                "success" if insight_success else "failed"
            ) if refresh_insight else "skipped",
            "summary_status": (
                "success" if summary_success else "failed"
            ) if refresh_summary else "skipped",
            "insight_error": insight.get("error") if insight else None,
            "summary_error": summary.get("error") if summary else None,
        }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        requested_successes = []
        if refresh_insight:
            requested_successes.append(result["insight_success"])
        if refresh_summary:
            requested_successes.append(result["summary_success"])
        if requested_successes and not any(requested_successes):
            raise RuntimeError(
                f"all requested cache refreshes failed: "
                f"insight_error={result.get('insight_error')}, "
                f"summary_error={result.get('summary_error')}"
            )
        if not requested_successes:
            result["status"] = "skipped"
        elif all(requested_successes):
            result["status"] = "success"
        else:
            result["status"] = "partial"
        logger.info(
            f"do_refresh_insight_summary_cache 完成 user={end_user_id} status={result['status']} "
            f"insight={result['insight_status']} summary={result['summary_status']} "
            f"耗时={time.time() - start_time:.1f}s"
        )
    # 异常不再 catch，直接冒出 → Celery FAILURE
    finally:
        _shutdown_loop_gracefully(loop)
        # 删除在途标记：放行下一轮 scan 对该用户的派发。
        try:
            _rc = get_sync_redis_client()
            if _rc is not None:
                _rc.delete(inflight_key)
        except Exception:
            pass

    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    result["end_user_id"] = end_user_id
    return result


# 用户名片 Tag 定时刷新任务

USER_TAG_INFLIGHT_KEY_FMT = "user_tags:inflight:{end_user_id}"
USER_TAG_INFLIGHT_TTL_SEC = 600
USER_TAG_SCAN_PAGE_SIZE = 500


@celery_app.task(
    name="app.tasks.scan_refresh_user_tags",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=600,
    soft_time_limit=540,
)
def scan_refresh_user_tags(self) -> Dict[str, Any]:
    """分页扫描待刷新用户，并为每个用户派发独立的 Tag 刷新任务。

    扫描任务只负责筛选和派发，不读取完整 metadata，也不调用 LLM，避免一个长任务持续
    占用数据库连接。实际生成由 ``do_refresh_user_tags`` 在 heavy worker 中完成。
    """
    from app.repositories.end_user_repository import EndUserRepository

    start_time = time.time()
    redis_client = get_sync_redis_client()
    if redis_client is None:
        logger.error("用户名片Tag scan终止：Redis客户端不可用，拒绝无锁派发")
        raise RuntimeError("Redis unavailable: user tag scan requires inflight locks")

    after_id: uuid.UUID | None = None
    candidates_count = 0
    dispatched = 0
    skip_inflight = 0
    failed = 0

    while True:
        with get_db_read() as db:
            candidates = EndUserRepository(db).get_user_tag_refresh_candidates(
                after_id=after_id,
                limit=USER_TAG_SCAN_PAGE_SIZE,
            )
        if not candidates:
            break

        candidates_count += len(candidates)
        for candidate in candidates:
            end_user_id = str(candidate.end_user_id)
            inflight_key = USER_TAG_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
            try:
                # Redis 在途标记防止相邻两轮扫描为同一用户重复派发任务。
                lock_acquired = bool(
                    redis_client.set(
                        inflight_key,
                        "1",
                        nx=True,
                        ex=USER_TAG_INFLIGHT_TTL_SEC,
                    )
                )
            except Exception as exc:
                logger.error(
                    "用户名片Tag scan终止：Redis在途锁不可用 user=%s error=%s",
                    end_user_id,
                    str(exc),
                    exc_info=True,
                )
                raise RuntimeError("Redis unavailable: failed to acquire user tag inflight lock") from exc

            if not lock_acquired:
                skip_inflight += 1
                continue

            try:
                # 每 60 个任务分散到 5 分钟内启动，削平 LLM 和数据库的瞬时压力。
                countdown = (dispatched % 60) * 5
                do_refresh_user_tags.apply_async(
                    kwargs={
                        "end_user_id": end_user_id,
                        "workspace_id": str(candidate.workspace_id),
                    },
                    countdown=countdown,
                    queue="memory_heavy_tasks",
                )
                dispatched += 1
            except Exception as exc:
                failed += 1
                logger.error(
                    "用户名片Tag scan派发失败 user=%s error=%s",
                    end_user_id,
                    str(exc),
                    exc_info=True,
                )
                try:
                    redis_client.delete(inflight_key)
                except Exception:
                    logger.warning("用户名片Tag scan回滚在途锁失败 user=%s", end_user_id, exc_info=True)

        after_id = candidates[-1].end_user_id
        if len(candidates) < USER_TAG_SCAN_PAGE_SIZE:
            break

    result = {
        "status": "SUCCESS",
        "candidates": candidates_count,
        "dispatched": dispatched,
        "skip_inflight": skip_inflight,
        "failed": failed,
        "elapsed_time": time.time() - start_time,
        "task_id": self.request.id,
    }
    logger.info(
        "scan_refresh_user_tags完成 candidates=%s dispatched=%s skip_inflight=%s failed=%s",
        candidates_count,
        dispatched,
        skip_inflight,
        failed,
    )
    return result


@celery_app.task(
    name="app.tasks.do_refresh_user_tags",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=120,
    soft_time_limit=90,
)
def do_refresh_user_tags(
    self,
    end_user_id: str,
    workspace_id: str,
) -> Dict[str, Any]:
    """在 heavy worker 中调用记忆领域入口，刷新单个用户的名片 Tag。

    Celery 任务本身是同步函数，新事件循环只用于驱动异步 LLM 调用；领域层中的 PostgreSQL
    操作仍使用同步短会话，并且不会在等待 LLM 时持有数据库连接。
    """
    inflight_key = USER_TAG_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.core.memory.memory_service import MemoryService

        return await MemoryService.refresh_user_card_tags(end_user_id, workspace_id)

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
    finally:
        _shutdown_loop_gracefully(loop)
        # 无论任务成功还是异常都释放在途标记，让后续扫描可以再次处理该用户。
        try:
            redis_client = get_sync_redis_client()
            if redis_client is not None:
                redis_client.delete(inflight_key)
        except Exception:
            logger.warning("用户名片Tag do释放在途锁失败 user=%s", end_user_id, exc_info=True)

    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    result["end_user_id"] = end_user_id
    logger.info("do_refresh_user_tags完成 user=%s status=%s", end_user_id, result["status"])
    return result


# @celery_app.task(
#     name="app.tasks.run_forgetting_cycle_task",
#     bind=True,
#     ignore_result=False,  # 改为 False 以便在 Flower 中查看结果
#     max_retries=0,
#     acks_late=False,
#     time_limit=7200,
#     soft_time_limit=7000,
# )
# def run_forgetting_cycle_task(self, config_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
#     """定时任务：运行遗忘周期
    
#     遍历所有终端用户，执行遗忘周期。
#     """
#     start_time = time.time()

#     async def _process_users() -> Dict[str, Any]:
#         from app.repositories.end_user_repository import EndUserRepository
#         with get_db_context() as db:
#             end_users = EndUserRepository(db).get_all_active()
#             if not end_users:
#                 logger.info("没有终端用户，跳过遗忘周期")
#                 return {"status": "SUCCESS", "message": "没有终端用户",
#                         "report": {"merged_count": 0, "failed_count": 0, "processed_users": 0},
#                         "duration_seconds": time.time() - start_time}

#             logger.info(f"开始处理 {len(end_users)} 个终端用户的遗忘周期")
#             forget_service = MemoryForgetService()
#             total_merged = total_failed = processed_users = 0
#             failed_users = []

#             for end_user in end_users:
#                 try:
#                     config_id = MemoryConfigService(db).get_workspace_active_config_id(end_user.workspace_id)

#                     # 执行遗忘周期
#                     report = await forget_service.trigger_forgetting_cycle(
#                         db=db, end_user_id=str(end_user.id), config_id=config_id
#                     )

#                     total_merged += report.get('merged_count', 0)
#                     total_failed += report.get('failed_count', 0)
#                     processed_users += 1

#                     logger.info(f"用户 {end_user.id}: 融合 {report.get('merged_count', 0)} 对节点")

#                 except Exception as e:
#                     logger.error(f"处理用户 {end_user.id} 失败: {e}", exc_info=True)
#                     failed_users.append({"end_user_id": str(end_user.id), "error": str(e)})

#             duration = time.time() - start_time
#             logger.info(f"遗忘周期完成: {processed_users}/{len(end_users)} 用户, "
#                         f"融合 {total_merged} 对, 耗时 {duration:.2f}s")

#             return {
#                 "status": "SUCCESS",
#                 "message": f"处理 {processed_users} 个用户",
#                 "report": {
#                     "merged_count": total_merged,
#                     "failed_count": total_failed,
#                     "processed_users": processed_users,
#                     "total_users": len(end_users),
#                     "failed_users": failed_users
#                 },
#                 "duration_seconds": duration
#             }

#     # 直接运行异步函数，全局异常自然冒出 → Celery FAILURE；
#     # 内层逐用户 try/except 已在 _process_users 中隔离单用户失败。
#     # asyncio.run 自行管理 event loop 生命周期，无需手动清理。
#     result = asyncio.run(_process_users())
#     result["elapsed_time"] = time.time() - start_time
#     result["task_id"] = self.request.id
#     return result


_FORGET_CANDIDATES_KEY = "forget:candidates"
_FORGET_INFLIGHT_KEY = "forget:inflight"


@celery_app.task(
    name="app.tasks.scan_forget_candidates",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
)
def scan_forget_candidates(self) -> Dict[str, Any]:
    """扫描 Redis 中超过配额的用户，派发 do_forget_for_user。

    通过 smove 原子地将 end_user_id 从 candidates → inflight，
    避免重复派发同一个用户。
    """

    async def _run() -> Dict[str, Any]:
        from app.aioRedis import get_thread_safe_redis

        start_time = time.time()
        redis_client = get_thread_safe_redis()
        if redis_client is None:
            return {"status": "FAILED", "message": "Redis 不可用"}

        candidates = await redis_client.smembers(_FORGET_CANDIDATES_KEY)
        if not candidates:
            return {"status": "SUCCESS", "dispatched": 0}

        dispatched = 0
        skipped_inflight = 0
        for uid in candidates:
            if await redis_client.sismember(_FORGET_INFLIGHT_KEY, uid):
                await redis_client.srem(_FORGET_CANDIDATES_KEY, uid)
                skipped_inflight += 1
                continue

            moved = await redis_client.smove(_FORGET_CANDIDATES_KEY, _FORGET_INFLIGHT_KEY, uid)
            if not moved:
                skipped_inflight += 1
                continue
            try:
                do_forget_for_user.apply_async(
                    kwargs={"end_user_id": uid},
                    queue="memory_heavy_tasks",
                )
                dispatched += 1
            except Exception as e:
                await redis_client.smove(_FORGET_INFLIGHT_KEY, _FORGET_CANDIDATES_KEY, uid)
                logger.error(f"[ForgetScan] 派发失败 user={uid}: {e}")

        logger.info(
            f"[ForgetScan] 完成: dispatched={dispatched}/{len(candidates)}, "
            f"skip_inflight={skipped_inflight}, "
            f"耗时={time.time() - start_time:.1f}s"
        )
        return {
            "status": "SUCCESS",
            "dispatched": dispatched,
            "candidates": len(candidates),
            "skip_inflight": skipped_inflight,
        }

    return asyncio.run(_run())


@celery_app.task(
    name="app.tasks.do_forget_for_user",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=1200,
    soft_time_limit=1080,
)
def do_forget_for_user(self, end_user_id: str) -> Dict[str, Any]:
    """对单个用户执行遗忘。由 scan_forget_candidates 派发。

    ForgettingPipeline.run() 内部已调用 sync，sync 会根据最新计数
    自行决定 sadd / srem。这里只清理 inflight，保证不泄漏。
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.repositories.end_user_repository import get_by_id as _get_user
        from app.services.memory_config_service import MemoryConfigService
        from app.core.memory.memory_service import MemoryService
        redis_client = get_thread_safe_redis()
        with get_db_context() as db:
            end_user = _get_user(db, uuid.UUID(end_user_id))
            if end_user is None:
                logger.warning(f"[ForgetDo] 用户不存在: {end_user_id}")
                if redis_client:
                    await redis_client.srem(_FORGET_INFLIGHT_KEY, end_user_id)
                    await redis_client.srem(_FORGET_CANDIDATES_KEY, end_user_id)
                return {"status": "skipped", "reason": "not_found"}

            config_id = MemoryConfigService(db).get_workspace_active_config_id(end_user.workspace_id)
            workspace_id = str(end_user.workspace_id)

        service = MemoryService(
            config_id=config_id,
            end_user_id=end_user_id,
            workspace_id=workspace_id,
        )

        # 抢该用户的写锁：与反思 / 去重任务互斥，保证同一用户的图谱不被并发修改。
        # Celery 任务线程独占 event loop，阻塞不影响其他任务，直接用同步上下文管理器。
        sync_redis = get_sync_redis_client()
        if sync_redis is not None:
            write_lock = RedisFairLock(
                key=f"memory_write:{end_user_id}",
                redis_client=sync_redis,
                expire=1200, timeout=60, auto_renewal=True,
            )
            try:
                with write_lock:
                    result = await service.forget()
            except RuntimeError:
                logger.warning(f"[ForgetDo] 获取写锁超时，跳过 user={end_user_id}")
                if redis_client:
                    # 移回候选集，等下一轮 scan 重新派发（与 scan_forget_candidates 派发失败的处理一致）
                    await redis_client.smove(_FORGET_INFLIGHT_KEY, _FORGET_CANDIDATES_KEY, end_user_id)
                return {"status": "lock_timeout"}
        else:
            result = await service.forget()

        if redis_client:
            await redis_client.srem(_FORGET_INFLIGHT_KEY, end_user_id)

        logger.info(
            f"[ForgetDo] 完成: end_user_id={end_user_id}, "
            f"elapsed={time.time() - start_time:.1f}s"
        )
        return {"status": "success", "result": result}

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
    except Exception as e:
        logger.error(f"[ForgetDo] 失败 user={end_user_id}: {e}", exc_info=True)
        result = {"status": "failed", "error": str(e)}
    finally:
        _shutdown_loop_gracefully(loop)

    result["end_user_id"] = end_user_id
    result["elapsed_time"] = time.time() - start_time
    return result


# =============================================================================
# 隐性记忆和情绪数据更新：扫描-派发模式
# =============================================================================

_IMPLICIT_EMOTIONS_INFLIGHT_KEY_FMT = "implicit_emotions:inflight:{end_user_id}"
_IMPLICIT_EMOTIONS_INFLIGHT_TTL_SEC = 600
_INIT_EMOTIONS_INFLIGHT_KEY_FMT = "init_emotions:inflight:{end_user_id}"
_INIT_EMOTIONS_INFLIGHT_TTL_SEC = 600

# 需要在work-periodic执行扫描任务
@celery_app.task(
    name="app.tasks.scan_implicit_emotions_storage",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=300,
    soft_time_limit=270,
)
def scan_implicit_emotions_storage(self) -> Dict[str, Any]:
    """扫描器：分页读取需刷新的用户 ID，逐用户派发 do_implicit_emotions_for_user。

    替代旧 update_implicit_emotions_storage 单任务串行模式。
    每个用户一个独立 Celery 任务，独立重试、独立超时、故障隔离。
    """
    from app.repositories.implicit_emotions_storage_repository import (
        ImplicitEmotionsStorageRepository,
        TimeFilterUnavailableError,
    )

    start_time = time.time()
    redis_client = get_sync_redis_client()
    if redis_client is None:
        logger.error("scan_implicit_emotions_storage 终止：Redis 不可用，拒绝无锁派发")
        raise RuntimeError("Redis unavailable: implicit emotions scan requires inflight locks")

    dispatched = 0
    skip_inflight = 0

    # --- 短 session：收集需刷新的用户 ID 列表后立即关闭 ---
    user_ids: list[str] = []
    new_user_ids: list[str] = []
    with get_db_context() as db:
        repo = ImplicitEmotionsStorageRepository(db)
        try:
            user_ids = list(repo.get_users_needing_refresh(redis_client, batch_size=200))
        except TimeFilterUnavailableError as e:
            logger.warning(f"时间轴筛选不可用，回退到全量: {e}")
            user_ids = list(repo.get_all_user_ids(batch_size=200))
        except Exception as e:
            logger.warning(f"获取需刷新用户列表异常，回退到全量: {e}")
            user_ids = list(repo.get_all_user_ids(batch_size=200))
        new_user_ids = list(repo.get_new_user_ids_today(batch_size=200))
    # --- session 已关闭 ---

    all_ids = list(set(user_ids + new_user_ids))  # 去重：用户可能同时出现在存量和新用户列表中
    logger.info(
        f"scan_implicit_emotions_storage: 存量需刷新 {len(user_ids)}, "
        f"当天新增 {len(new_user_ids)}, 总候选 {len(all_ids)}"
    )

    for end_user_id in all_ids:
        # 互斥检查：若 init 任务正在处理该用户，跳过
        init_inflight_key = _INIT_EMOTIONS_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
        if redis_client.exists(init_inflight_key):
            skip_inflight += 1
            continue

        # inflight 锁：防止重复派发
        inflight_key = _IMPLICIT_EMOTIONS_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
        ok = redis_client.set(inflight_key, "1", nx=True, ex=_IMPLICIT_EMOTIONS_INFLIGHT_TTL_SEC)
        if not ok:
            skip_inflight += 1
            continue

        do_implicit_emotions_for_user.apply_async(
            kwargs={"end_user_id": end_user_id},
            queue="memory_heavy_tasks",
        )
        dispatched += 1

    elapsed = time.time() - start_time
    logger.info(
        f"scan_implicit_emotions_storage 完成: 派发 {dispatched}, "
        f"跳过(在途) {skip_inflight}, 耗时 {elapsed:.1f}s"
    )
    return {
        "status": "SUCCESS",
        "dispatched": dispatched,
        "skip_inflight": skip_inflight,
        "total_candidates": len(all_ids),
        "elapsed_time": elapsed,
        "task_id": self.request.id,
    }


@celery_app.task(
    name="app.tasks.do_implicit_emotions_for_user",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=600,
    soft_time_limit=540,
)
def do_implicit_emotions_for_user(self, end_user_id: str) -> Dict[str, Any]:
    """对【单个用户】生成隐性记忆画像 + 情绪建议。

    由 scan_implicit_emotions_storage 派发，每个用户一个独立 Celery 任务。
    三段式短 session：Session A（工厂方法内）→ LLM（无 PG）→ Session B（写回）。
    """
    start_time = time.time()
    inflight_key = _IMPLICIT_EMOTIONS_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)

    async def _run() -> Dict[str, Any]:
        from app.services.emotion_analytics_service import EmotionAnalyticsService
        from app.services.implicit_memory_service import ImplicitMemoryService

        implicit_success = False
        emotion_success = False
        errors = []

        # --- 隐性记忆画像 ---
        try:
            # Session A 内置于工厂方法（短 session 查 config + 构造 LLM 客户端 → 关闭）
            implicit_service = ImplicitMemoryService.create_without_session(end_user_id)

            # LLM + Neo4j 生成（无 PG session）
            try:
                profile_data = await implicit_service.generate_complete_profile(user_id=end_user_id)
            finally:
                # 释放独立 Neo4j driver 连接池，防止泄漏
                await implicit_service.neo4j_connector.close()

            # Session B：写回
            with get_db_context() as db:
                await implicit_service.save_profile_cache(
                    end_user_id=end_user_id, profile_data=profile_data, db=db
                )
            implicit_success = True
            logger.info(f"成功更新用户 {end_user_id} 的隐性记忆画像")
        except Exception as e:
            errors.append(f"隐性记忆更新失败: {str(e)}")
            logger.error(f"用户 {end_user_id} 隐性记忆更新失败: {e}")

        # --- 情绪建议 ---
        try:
            emotion_service = EmotionAnalyticsService()
            # db=None：内部自行开短 session 查 config → 关闭 → Neo4j + LLM
            suggestions_data = await emotion_service.generate_emotion_suggestions(
                end_user_id=end_user_id, language="zh"
            )

            # Session C：写回
            with get_db_context() as db:
                await emotion_service.save_suggestions_cache(
                    end_user_id=end_user_id, suggestions_data=suggestions_data, db=db
                )
            emotion_success = True
            logger.info(f"成功更新用户 {end_user_id} 的情绪建议")
        except Exception as e:
            errors.append(f"情绪建议更新失败: {str(e)}")
            logger.error(f"用户 {end_user_id} 情绪建议更新失败: {e}")

        return {
            "implicit_success": implicit_success,
            "emotion_success": emotion_success,
            "errors": errors,
        }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        # 双失败 = 完全失败
        if not result["implicit_success"] and not result["emotion_success"]:
            raise RuntimeError(
                f"implicit and emotion both failed for user {end_user_id}: {result['errors']}"
            )
        result["status"] = (
            "success" if (result["implicit_success"] and result["emotion_success"]) else "partial"
        )
        logger.info(
            f"do_implicit_emotions_for_user 完成 user={end_user_id} "
            f"status={result['status']} 耗时={time.time() - start_time:.1f}s"
        )
    finally:
        # 清理 pending tasks + asyncgens，但不关闭 loop：
        # shared_driver=True 的 Neo4j 连接池绑定在此 loop 上，关闭会导致后续任务
        # 报 "Future attached to a different loop"。loop 在 worker 进程内复用。
        _shutdown_loop_gracefully(loop)
        # 删除在途标记
        try:
            _rc = get_sync_redis_client()
            if _rc is not None:
                _rc.delete(inflight_key)
        except Exception:
            pass

    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    result["end_user_id"] = end_user_id
    return result


# =============================================================================
# 隐性记忆和情绪数据更新定时任务（已废弃，由 scan_implicit_emotions_storage + do_implicit_emotions_for_user 替代）
# =============================================================================

@celery_app.task(
    name="app.tasks.update_implicit_emotions_storage",
    bind=True,
    ignore_result=True,
    max_retries=0,
    acks_late=False,
    time_limit=7200,  # 2小时硬超时
    soft_time_limit=6900,  # 1小时55分钟软超时
)
def update_implicit_emotions_storage(self) -> Dict[str, Any]:
    """定时任务：更新所有用户的隐性记忆画像和情绪建议数据·

    遍历数据库中所有已存在数据的用户，为每个用户重新生成隐性记忆画像和情绪建议。
    实现错误隔离，单个用户失败不影响其他用户的处理。

    Returns:
        包含任务执行结果的字典，包括：
        - status: 任务状态 (SUCCESS/FAILURE)
        - message: 执行消息
        - total_users: 总用户数
        - successful_implicit: 成功更新隐性记忆的用户数
        - successful_emotion: 成功更新情绪建议的用户数
        - failed: 失败的用户数
        - user_results: 每个用户的详细结果
        - elapsed_time: 执行耗时（秒）
        - task_id: 任务ID
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from sqlalchemy import select

        from app.models.implicit_emotions_storage_model import ImplicitEmotionsStorage
        from app.repositories.implicit_emotions_storage_repository import (
            ImplicitEmotionsStorageRepository,
            TimeFilterUnavailableError,
        )
        from app.services.emotion_analytics_service import EmotionAnalyticsService
        from app.services.implicit_memory_service import ImplicitMemoryService

        logger.info("开始执行隐性记忆和情绪数据更新定时任务")

        total_users = 0
        successful_implicit = 0
        successful_emotion = 0
        failed = 0
        user_results = []

        with get_db_context() as db:
            repo = ImplicitEmotionsStorageRepository(db)

            # 先统计总数用于日志
            from sqlalchemy import func
            total_users = db.execute(
                select(func.count()).select_from(ImplicitEmotionsStorage)
            ).scalar() or 0
            logger.info(f"表中存量用户总数: {total_users}，开始时间轴筛选")

            # 构建 Redis 同步客户端，用于时间轴筛选
            _redis_client = get_sync_redis_client()

            # 只处理 last_done > updated_at 的用户（有新记忆写入的用户）
            # Redis 不可用时回退到全量处理
            try:
                refresh_iter = repo.get_users_needing_refresh(_redis_client, batch_size=100)
            except TimeFilterUnavailableError as e:
                logger.warning(f"时间轴筛选不可用，回退到全量刷新: {e}")
                refresh_iter = repo.get_all_user_ids(batch_size=100)

            for end_user_id in refresh_iter:
                logger.info(f"开始处理用户: {end_user_id}")
                user_start_time = time.time()

                implicit_success = False
                emotion_success = False
                errors = []

                try:
                    # 更新隐性记忆画像
                    try:
                        implicit_service = ImplicitMemoryService(db=db, end_user_id=end_user_id)
                        profile_data = await implicit_service.generate_complete_profile(user_id=end_user_id)
                        await implicit_service.save_profile_cache(
                            end_user_id=end_user_id,
                            profile_data=profile_data,
                            db=db
                        )
                        implicit_success = True
                        logger.info(f"成功更新用户 {end_user_id} 的隐性记忆画像")
                    except Exception as e:
                        error_msg = f"隐性记忆更新失败: {str(e)}"
                        errors.append(error_msg)
                        logger.error(f"用户 {end_user_id} {error_msg}")

                    # 更新情绪建议
                    try:
                        emotion_service = EmotionAnalyticsService()
                        suggestions_data = await emotion_service.generate_emotion_suggestions(
                            end_user_id=end_user_id,
                            db=db,
                            language="zh"
                        )
                        await emotion_service.save_suggestions_cache(
                            end_user_id=end_user_id,
                            suggestions_data=suggestions_data,
                            db=db
                        )
                        emotion_success = True
                        logger.info(f"成功更新用户 {end_user_id} 的情绪建议")
                    except Exception as e:
                        error_msg = f"情绪建议更新失败: {str(e)}"
                        errors.append(error_msg)
                        logger.error(f"用户 {end_user_id} {error_msg}")

                    # 统计结果
                    if implicit_success:
                        successful_implicit += 1
                    if emotion_success:
                        successful_emotion += 1
                    if not implicit_success and not emotion_success:
                        failed += 1

                    user_elapsed = time.time() - user_start_time

                    # 记录用户处理结果
                    user_result = {
                        "end_user_id": end_user_id,
                        "implicit_success": implicit_success,
                        "emotion_success": emotion_success,
                        "errors": errors,
                        "elapsed_time": user_elapsed
                    }
                    user_results.append(user_result)

                    logger.info(
                        f"用户 {end_user_id} 处理完成: "
                        f"隐性记忆={'成功' if implicit_success else '失败'}, "
                        f"情绪建议={'成功' if emotion_success else '失败'}, "
                        f"耗时={user_elapsed:.2f}秒"
                    )

                except Exception as e:
                    # 单个用户失败不影响其他用户（错误隔离）
                    failed += 1
                    user_elapsed = time.time() - user_start_time
                    error_info = {
                        "end_user_id": end_user_id,
                        "implicit_success": False,
                        "emotion_success": False,
                        "errors": [str(e)],
                        "elapsed_time": user_elapsed
                    }
                    user_results.append(error_info)
                    logger.error(f"处理用户 {end_user_id} 时出错: {str(e)}")

            # ---- 当天新增用户兜底初始化 ----
            new_users_initialized = 0
            new_users_failed = 0
            logger.info("开始处理当天新增用户的兜底初始化")

            for end_user_id in repo.get_new_user_ids_today(batch_size=100):
                logger.info(f"开始初始化新用户: {end_user_id}")
                user_start_time = time.time()
                implicit_success = False
                emotion_success = False
                errors = []

                try:
                    try:
                        implicit_service = ImplicitMemoryService(db=db, end_user_id=end_user_id)
                        profile_data = await implicit_service.generate_complete_profile(user_id=end_user_id)
                        await implicit_service.save_profile_cache(
                            end_user_id=end_user_id, profile_data=profile_data, db=db
                        )
                        implicit_success = True
                        logger.info(f"成功初始化新用户 {end_user_id} 的隐性记忆画像")
                    except Exception as e:
                        errors.append(f"隐性记忆初始化失败: {str(e)}")
                        logger.error(f"新用户 {end_user_id} 隐性记忆初始化失败: {e}")

                    try:
                        emotion_service = EmotionAnalyticsService()
                        suggestions_data = await emotion_service.generate_emotion_suggestions(
                            end_user_id=end_user_id, db=db, language="zh"
                        )
                        await emotion_service.save_suggestions_cache(
                            end_user_id=end_user_id, suggestions_data=suggestions_data, db=db
                        )
                        emotion_success = True
                        logger.info(f"成功初始化新用户 {end_user_id} 的情绪建议")
                    except Exception as e:
                        errors.append(f"情绪建议初始化失败: {str(e)}")
                        logger.error(f"新用户 {end_user_id} 情绪建议初始化失败: {e}")

                    if implicit_success or emotion_success:
                        new_users_initialized += 1
                    else:
                        new_users_failed += 1

                    user_elapsed = time.time() - user_start_time
                    user_results.append({
                        "end_user_id": end_user_id,
                        "type": "new_user_init",
                        "implicit_success": implicit_success,
                        "emotion_success": emotion_success,
                        "errors": errors,
                        "elapsed_time": user_elapsed
                    })

                except Exception as e:
                    new_users_failed += 1
                    user_elapsed = time.time() - user_start_time
                    user_results.append({
                        "end_user_id": end_user_id,
                        "type": "new_user_init",
                        "implicit_success": False,
                        "emotion_success": False,
                        "errors": [str(e)],
                        "elapsed_time": user_elapsed
                    })
                    logger.error(f"初始化新用户 {end_user_id} 时出错: {str(e)}")

            logger.info(f"当天新增用户兜底初始化完成: 成功={new_users_initialized}, 失败={new_users_failed}")
            # ---- 新增用户兜底初始化结束 ----

            logger.info(
                f"隐性记忆和情绪数据更新定时任务完成: "
                f"存量用户总数={total_users}, "
                f"隐性记忆成功={successful_implicit}, "
                f"情绪建议成功={successful_emotion}, "
                f"存量失败={failed}, "
                f"新增用户初始化成功={new_users_initialized}, "
                f"新增用户初始化失败={new_users_failed}"
            )

            return {
                "status": "SUCCESS",
                "message": (
                    f"存量用户 {total_users} 个，隐性记忆 {successful_implicit} 个成功，情绪建议 {successful_emotion} 个成功；"
                    f"当天新增用户初始化 {new_users_initialized} 个成功，{new_users_failed} 个失败"
                ),
                "total_users": total_users,
                "successful_implicit": successful_implicit,
                "successful_emotion": successful_emotion,
                "failed": failed,
                "new_users_initialized": new_users_initialized,
                "new_users_failed": new_users_failed,
                "user_results": user_results[:50]
            }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id
        return result
    # 不再 catch 全局异常，直接冒出 → Celery FAILURE
    finally:
        _shutdown_loop_gracefully(loop)


# =============================================================================

@celery_app.task(
    name="app.tasks.init_implicit_emotions_for_users",
    bind=True,
    ignore_result=True,
    max_retries=0,
    acks_late=False,
    time_limit=3600,
    soft_time_limit=3300,
    # 触发型任务标识，区别于 periodic_tasks 队列中的定时任务
    triggered=True,
)
def init_implicit_emotions_for_users(self, end_user_ids: List[str]) -> Dict[str, Any]:
    """事件触发任务：对指定用户列表做存在性检查，无记录则执行首次初始化。

    由 /dashboard/end_users 接口触发，已有数据的用户直接跳过。
    存量用户的数据刷新由定时任务 scan_implicit_emotions_storage 负责。

    改造说明：逐用户三段式短 session + per-user inflight 锁，
    避免单个 session 跨多用户 LLM 调用期间空占 PG 连接。

    Args:
        end_user_ids: 需要检查的用户ID列表

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.repositories.implicit_emotions_storage_repository import (
            ImplicitEmotionsStorageRepository,
        )
        from app.services.emotion_analytics_service import EmotionAnalyticsService
        from app.services.implicit_memory_service import ImplicitMemoryService

        logger.info(f"开始按需初始化隐性记忆/情绪数据，候选用户数: {len(end_user_ids)}")

        redis_client = get_sync_redis_client()
        if redis_client is None:
            logger.error("init_implicit_emotions_for_users 终止：Redis 不可用，拒绝无锁执行")
            raise RuntimeError("Redis unavailable: init implicit emotions requires inflight locks")

        initialized = 0
        failed = 0
        skip_inflight = 0
        skip_existing = 0

        for end_user_id in end_user_ids:
            # 互斥检查：若 scan 派发的 do_implicit_emotions_for_user 正在处理该用户，跳过
            scan_inflight_key = _IMPLICIT_EMOTIONS_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
            if redis_client.exists(scan_inflight_key):
                skip_inflight += 1
                continue

            # Per-user inflight 锁：防止与 scan 任务并发处理同一用户
            inflight_key = _INIT_EMOTIONS_INFLIGHT_KEY_FMT.format(end_user_id=end_user_id)
            ok = redis_client.set(inflight_key, "1", nx=True, ex=_INIT_EMOTIONS_INFLIGHT_TTL_SEC)
            if not ok:
                skip_inflight += 1
                continue

            try:
                # --- Session A：查存在性 → 关闭 ---
                existing = None
                with get_db_context() as db:
                    existing = ImplicitEmotionsStorageRepository(db).get_by_end_user_id(end_user_id)

                if existing is not None:
                    skip_existing += 1
                    continue

                logger.info(f"用户 {end_user_id} 无记录，开始初始化")
                implicit_ok = False
                emotion_ok = False

                # --- 隐性记忆画像：LLM 生成（无 PG session）---
                try:
                    implicit_service = ImplicitMemoryService.create_without_session(end_user_id)
                    try:
                        profile_data = await implicit_service.generate_complete_profile(user_id=end_user_id)
                    finally:
                        # 释放独立 Neo4j driver 连接池，防止泄漏
                        await implicit_service.neo4j_connector.close()
                    # Session B：写回
                    with get_db_context() as db:
                        await implicit_service.save_profile_cache(
                            end_user_id=end_user_id, profile_data=profile_data, db=db
                        )
                    implicit_ok = True
                except Exception as e:
                    logger.error(f"用户 {end_user_id} 隐性记忆初始化失败: {e}")

                # --- 情绪建议：LLM 生成（内部自管理 session）---
                try:
                    emotion_service = EmotionAnalyticsService()
                    suggestions_data = await emotion_service.generate_emotion_suggestions(
                        end_user_id=end_user_id, language="zh"
                    )
                    # Session C：写回
                    with get_db_context() as db:
                        await emotion_service.save_suggestions_cache(
                            end_user_id=end_user_id, suggestions_data=suggestions_data, db=db
                        )
                    emotion_ok = True
                except Exception as e:
                    logger.error(f"用户 {end_user_id} 情绪建议初始化失败: {e}")

                if implicit_ok or emotion_ok:
                    initialized += 1
                else:
                    failed += 1

            except Exception as e:
                failed += 1
                logger.error(f"用户 {end_user_id} 初始化异常: {e}")
            finally:
                # 清理 inflight 锁
                try:
                    redis_client.delete(inflight_key)
                except Exception:
                    pass

        logger.info(
            f"按需初始化完成: 初始化={initialized}, "
            f"跳过(在途)={skip_inflight}, 跳过(已有)={skip_existing}, 失败={failed}"
        )
        return {
            "status": "SUCCESS",
            "initialized": initialized,
            "skipped": skip_inflight + skip_existing,
            "skip_inflight": skip_inflight,
            "skip_existing": skip_existing,
            "failed": failed,
        }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        # 全部失败（无初始化、无跳过）= 完全失败：raise → Celery FAILURE
        if result["failed"] > 0 and result["initialized"] == 0 and result["skipped"] == 0:
            raise RuntimeError(
                f"all {result['failed']} users failed to initialize implicit emotions"
            )
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id
        return result
    # 不再 catch 全局异常，直接冒出 → Celery FAILURE
    finally:
        _shutdown_loop_gracefully(loop)


# =============================================================================

@celery_app.task(
    name="app.tasks.init_interest_distribution_for_users",
    bind=True,
    ignore_result=True,
    max_retries=0,
    acks_late=False,
    time_limit=3600,
    soft_time_limit=3300,
)
def init_interest_distribution_for_users(self, end_user_ids: List[str]) -> Dict[str, Any]:
    """事件触发任务：检查指定用户列表的兴趣分布缓存，无缓存则生成并写入 Redis。

    由 /dashboard/end_users 接口触发，已有缓存的用户直接跳过。
    默认生成中文（zh）兴趣分布数据。

    Args:
        self: task object
        end_user_ids: 需要检查的用户ID列表

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.cache.memory.interest_memory import InterestMemoryCache, INTEREST_CACHE_EXPIRE
        from app.services.memory_agent_service import MemoryAgentService

        logger.info(f"开始按需初始化兴趣分布缓存，候选用户数: {len(end_user_ids)}")

        initialized = 0
        failed = 0
        skipped = 0
        not_cached = 0
        language = "zh"

        service = MemoryAgentService()

        # 预校验：逐个解析 UUID，无效格式直接记 failed 跳过
        valid_uuids: list[uuid.UUID] = []
        invalid_ids: list[str] = []
        for eid in end_user_ids:
            try:
                valid_uuids.append(uuid.UUID(eid))
            except (ValueError, AttributeError):
                invalid_ids.append(eid)
                failed += 1
                logger.warning(f"用户 {eid} UUID 格式无效，跳过兴趣分布初始化")

        # 查询 DB 中实际存在的 end_user_id
        with get_db_context() as db:
            from app.repositories.end_user_repository import EndUserRepository
            existing_ids = EndUserRepository(db).filter_existing_ids(valid_uuids)

        for end_user_id in end_user_ids:
            # 存在性校验：不存在的用户直接记失败
            if end_user_id not in existing_ids:
                failed += 1
                logger.warning(f"用户 {end_user_id} 不存在，跳过兴趣分布初始化")
                continue

            # 存在性检查：缓存有数据则跳过
            cached = await InterestMemoryCache.get_interest_distribution(
                end_user_id=end_user_id,
                language=language,
            )
            if cached is not None:
                skipped += 1
                continue

            logger.info(f"用户 {end_user_id} 无兴趣分布缓存，开始生成")
            try:
                result, cacheable = await service.generate_interest_distribution_by_user(
                    end_user_id=end_user_id,
                    limit=5,
                    language=language,
                )
                if cacheable:
                    await InterestMemoryCache.set_interest_distribution(
                        end_user_id=end_user_id,
                        language=language,
                        data=result,
                        expire=INTEREST_CACHE_EXPIRE,
                    )
                    initialized += 1
                    logger.info(f"用户 {end_user_id} 兴趣分布缓存生成成功")
                else:
                    not_cached += 1
                    logger.info(f"用户 {end_user_id} 兴趣分布结果不可缓存，本次不写缓存")
            except Exception as e:
                failed += 1
                logger.error(f"用户 {end_user_id} 兴趣分布缓存生成失败: {e}")

        logger.info(
            f"兴趣分布按需初始化完成: 初始化={initialized}, "
            f"未缓存={not_cached}, 跳过={skipped}, 失败={failed}"
        )
        return {
            "status": "SUCCESS",
            "initialized": initialized,
            "not_cached": not_cached,
            "skipped": skipped,
            "failed": failed,
        }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        # 全部失败（无初始化、无未缓存成功结果、无跳过）才标记 Celery FAILURE。
        if (
            result["failed"] > 0
            and result["initialized"] == 0
            and result["not_cached"] == 0
            and result["skipped"] == 0
        ):
            raise RuntimeError(
                f"all {result['failed']} users failed to initialize interest distribution"
            )
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id
        return result
    # 不再 catch 全局异常，直接冒出 → Celery FAILURE
    finally:
        _shutdown_loop_gracefully(loop)


@celery_app.task(
    name="app.tasks.refresh_hot_memory_tags_cache",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=3600,
    soft_time_limit=3300,
)
def refresh_hot_memory_tags_cache(self) -> Dict[str, Any]:
    """定时任务：为所有活跃 workspace 预热热门记忆标签缓存（limit=10）。

    执行时间由 settings.HOT_MEMORY_TAGS_REFRESH_HOUR（UTC 小时）决定，
    默认 19（= 北京时间 03:00）。缓存过期 28h，使白天请求全程命中缓存。
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        import json as _json

        from app.aioRedis import aio_redis_get, aio_redis_set
        from app.models.workspace_model import Workspace
        from app.services.memory_storage_service import (
            HOT_MEMORY_TAGS_CACHE_EXPIRE,
            HOT_MEMORY_TAGS_CACHE_PREFIX,
            compute_hot_memory_tags,
        )

        limit = 10  # 前端首页固定 limit

        # 1. 取全量启用（is_active=True）的 workspace id（短事务，取完即出）
        #    与 write_all_workspaces_memory_task 一致，仅排除已停用/软删除的 workspace
        with get_db_context() as db:
            workspace_ids = [
                str(wid) for (wid,) in db.query(Workspace.id).filter(
                    Workspace.is_active.is_(True)
                ).all()
            ]

        if not workspace_ids:
            return {"status": "SUCCESS", "message": "无活跃工作空间", "total": 0}

        logger.info(f"[HotTagsRefresh] 开始预热 {len(workspace_ids)} 个 workspace 的热门标签缓存")

        refreshed = 0
        empty = 0
        failed = 0

        # 2. 逐个 workspace 计算并写缓存（串行，避免 LLM 并发压力）
        for workspace_id in workspace_ids:
            try:
                result = await compute_hot_memory_tags(workspace_id, limit)
                if not result:
                    empty += 1
                cache_key = f"{HOT_MEMORY_TAGS_CACHE_PREFIX}:{workspace_id}:{limit}"
                cache_data = _json.dumps(result, ensure_ascii=False)
                await aio_redis_set(cache_key, cache_data, expire=HOT_MEMORY_TAGS_CACHE_EXPIRE)

                # aio_redis_set 内部吞异常（写失败仅记日志、不抛），这里写后读回校验，
                # 确保 refreshed 计数真实反映「缓存确实写入」，而非虚报成功
                verify = await aio_redis_get(cache_key)
                if verify is None:
                    failed += 1
                    logger.error(f"[HotTagsRefresh] 缓存写入校验失败（读回为空） key={cache_key}")
                    continue

                refreshed += 1
                logger.info(
                    f"[HotTagsRefresh] 缓存写入成功 key={cache_key} "
                    f"tags={len(result)} expire={HOT_MEMORY_TAGS_CACHE_EXPIRE}s"
                )
            except Exception as e:
                failed += 1
                logger.error(f"[HotTagsRefresh] workspace={workspace_id} 预热失败: {e}", exc_info=True)

        logger.info(f"[HotTagsRefresh] 预热完成: refreshed={refreshed}, empty={empty}, failed={failed}")
        return {
            "status": "SUCCESS",
            "total": len(workspace_ids),
            "refreshed": refreshed,
            "empty": empty,
            "failed": failed,
        }

    try:
        loop = set_asyncio_event_loop()
        result = loop.run_until_complete(_run())
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id
        return result
    except Exception as e:
        return {
            "status": "FAILURE",
            "error": str(e),
            "elapsed_time": time.time() - start_time,
            "task_id": self.request.id,
        }


# =============================================================================
# 社区聚类补全任务（触发型）
# =============================================================================

@celery_app.task(
    name="app.tasks.run_incremental_clustering",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=1800,  # 30分钟硬超时
    soft_time_limit=1700,
)
def run_incremental_clustering(
    self,
    end_user_id: str,
    new_entity_ids: List[str],
    config_id: Optional[str] = None,
    language: str = "zh",
) -> Dict[str, Any]:
    """增量聚类任务：处理新增实体的社区分配和元数据生成。
    
    此任务在后台异步执行，不阻塞 write_message 主流程。
    
    Args:
        end_user_id: 用户 ID
        new_entity_ids: 新增实体 ID 列表
        config_id: 记忆配置 ID（可选）。任务内经 load_memory_config 重建完整
            MemoryConfig（内含 tenant_id + 各 model_id，同源），交由引擎使用。
        language: 语言类型 ("zh" | "en")
    
    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.core.logging_config import get_logger
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.core.memory.storage_services.clustering_engine.label_propagation import LabelPropagationEngine

        logger = get_logger(__name__)
        logger.info(
            f"[IncrementalClustering] 开始增量聚类任务 - end_user_id={end_user_id}, "
            f"实体数={len(new_entity_ids)}, config_id={config_id}"
        )

        # 跨进程只传 config_id，任务内重建完整 MemoryConfig：
        # tenant_id 与各 model_id 同源加载，杜绝拍扁传参时漏传 tenant。
        with get_db_context() as db:
            from app.services.memory_config_service import MemoryConfigService
            memory_config = MemoryConfigService(db).load_memory_config(config_id=config_id)

        connector = Neo4jConnector()
        try:
            engine = LabelPropagationEngine(
                connector=connector,
                memory_config=memory_config,
                language=language,
            )

            # 执行增量聚类
            await engine.run(end_user_id=end_user_id, new_entity_ids=new_entity_ids)

            logger.info(f"[IncrementalClustering] 增量聚类完成 - end_user_id={end_user_id}")

            return {
                "status": "SUCCESS",
                "end_user_id": end_user_id,
                "entity_count": len(new_entity_ids),
            }
        except Exception as e:
            logger.error(f"[IncrementalClustering] 增量聚类失败: {e}", exc_info=True)
            raise
        finally:
            await connector.close()

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id

        logger.info(
            f"[IncrementalClustering] 任务完成 - task_id={self.request.id}, "
            f"elapsed_time={result['elapsed_time']:.2f}s"
        )

        return result
    # 不再 catch 全局异常，直接冒出 → Celery FAILURE
    finally:
        _shutdown_loop_gracefully(loop)


@celery_app.task(
    name="app.tasks.init_community_clustering_for_users",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=7200,  # 2小时硬超时
    soft_time_limit=6900,
)
def init_community_clustering_for_users(self, end_user_ids: List[str], workspace_id: Optional[str] = None) -> Dict[
    str, Any]:
    """触发型任务：检查指定用户列表，对有 ExtractedEntity 但无 Community 节点的用户执行全量聚类。

    由 /dashboard/end_users 接口触发，已有社区节点的用户直接跳过。
    任务完成且所有用户数据均完整时，写入 Redis 标记，避免下次重复投递。

    Args:
        end_user_ids: 需要检查的用户 ID 列表
        workspace_id: 工作空间 ID，用于完成标记

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.core.logging_config import get_logger
        from app.repositories.neo4j.community_repository import CommunityRepository
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.core.memory.storage_services.clustering_engine.label_propagation import LabelPropagationEngine

        logger = get_logger(__name__)
        logger.info(f"[CommunityCluster] 开始社区聚类补全任务，候选用户数: {len(end_user_ids)}")

        initialized = 0
        skipped = 0
        failed = 0

        connector = Neo4jConnector()
        try:
            repo = CommunityRepository(connector)

            # 批量预取所有用户的 MemoryConfig（tenant 与 model_id 同源），避免循环内逐个查库。
            # 加载失败的用户不存入 map，循环内检测到缺失时直接 skip。
            user_config_map: Dict[str, Any] = {}
            try:
                with get_db_context() as db:
                    from app.services.memory_agent_service import get_end_users_connected_configs_batch
                    from app.services.memory_config_service import MemoryConfigService
                    batch_configs = get_end_users_connected_configs_batch(end_user_ids, db)
                    for uid, cfg_info in batch_configs.items():
                        config_id = cfg_info.get("memory_config_id")
                        if config_id:
                            try:
                                user_config_map[uid] = MemoryConfigService(db).load_memory_config(config_id=config_id)
                            except Exception as e:
                                logger.error(f"[CommunityCluster] 用户 {uid} 加载配置失败，将跳过: {e}")
            except Exception as e:
                logger.error(f"[CommunityCluster] 批量获取配置失败: {e}")

            for end_user_id in end_user_ids:
                try:
                    # 配置加载失败的用户直接跳过
                    memory_config = user_config_map.get(end_user_id)
                    if not memory_config:
                        failed += 1
                        logger.warning(f"[CommunityCluster] 用户 {end_user_id} 无有效配置，跳过聚类")
                        continue

                    # 已有社区节点时，检查是否存在属性不完整的节点
                    has_communities = await repo.has_communities(end_user_id)
                    if has_communities:
                        incomplete_ids = await repo.get_incomplete_communities(
                            end_user_id,
                            check_embedding=bool(memory_config.embedding_model_id),
                        )
                        if not incomplete_ids:
                            skipped += 1
                            logger.debug(f"[CommunityCluster] 用户 {end_user_id} 社区节点均完整，跳过")
                            continue

                        # 对不完整的社区节点逐一补全元数据
                        engine = LabelPropagationEngine(
                            connector=connector,
                            memory_config=memory_config,
                        )
                        logger.info(
                            f"[CommunityCluster] 用户 {end_user_id} 发现 {len(incomplete_ids)} 个属性不完整的社区，开始补全"
                        )
                        patch_ok = 0
                        patch_fail = 0
                        for cid in incomplete_ids:
                            try:
                                await engine._generate_community_metadata([cid], end_user_id)
                                patch_ok += 1
                            except Exception as patch_err:
                                patch_fail += 1
                                logger.error(f"[CommunityCluster] 社区 {cid} 元数据补全失败: {patch_err}")
                        logger.info(
                            f"[CommunityCluster] 用户 {end_user_id} 社区补全完成: 成功={patch_ok}, 失败={patch_fail}"
                        )
                        initialized += 1
                        continue

                    # 检查是否有 ExtractedEntity 节点
                    entities = await repo.get_all_entities(end_user_id)
                    if not entities:
                        skipped += 1
                        logger.debug(f"[CommunityCluster] 用户 {end_user_id} 无实体节点，跳过")
                        continue

                    # 每个用户使用自己的 MemoryConfig（tenant 与 model_id 同源）
                    engine = LabelPropagationEngine(
                        connector=connector,
                        memory_config=memory_config,
                    )

                    logger.info(
                        f"[CommunityCluster] 用户 {end_user_id} 有 {len(entities)} 个实体，开始全量聚类，"
                        f"llm_model_id={memory_config.llm_model_id}")
                    await engine.full_clustering(end_user_id)
                    initialized += 1
                    logger.info(f"[CommunityCluster] 用户 {end_user_id} 聚类完成")

                except Exception as e:
                    failed += 1
                    logger.error(f"[CommunityCluster] 用户 {end_user_id} 聚类失败: {e}")

        finally:
            await connector.close()

        logger.info(
            f"[CommunityCluster] 任务完成: 初始化={initialized}, 跳过={skipped}, 失败={failed}"
        )
        return {
            "status": "SUCCESS",
            "initialized": initialized,
            "skipped": skipped,
            "failed": failed,
        }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
        # 全部失败（无初始化、无跳过）= 完全失败：raise → Celery FAILURE
        if result["failed"] > 0 and result["initialized"] == 0 and result["skipped"] == 0:
            raise RuntimeError(
                f"all {result['failed']} users failed in community clustering"
            )
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id
        return result
    # 不再 catch 全局异常，直接冒出 → Celery FAILURE；
    # 内层 _run() 中 connector.close() 已由 try/finally 保证释放。
    finally:
        _shutdown_loop_gracefully(loop)


# ─── User Metadata Extraction Task ───────────────────────────────────────────

# ──────────────────────────────────────────────
# 滑动窗口写入相关常量
# ──────────────────────────────────────────────

# Redis key 前缀
CONV_ACTIVE_KEY_PREFIX = "conv_active:"


@celery_app.task(
    bind=True,
    name="app.tasks.flush_conversation",
    queue="periodic_tasks",
    max_retries=0,
    acks_late=True,
)
def flush_conversation_task(self) -> None:
    """兜底写入任务（Beat 定时调度）。

    扫描所有空闲对话，逐个派发兜底写入任务。

    优先从 Redis Set (pending_conversations) 获取候选对话 ID，避免全表 JOIN 扫描。
    若 Set 不可用则回退到数据库查询。

    扫描条件（两者同时满足才派发）：
    1. 对话存在未写入消息（来自 Redis Set 或 DB 查询）
    2. Redis 中 conv_active:{conversation_id} 已过期或不存在（对话空闲 >5 分钟）

    dispatcher 内部逐条派发 write_message_task 并推进 write_cursor，
    下次扫描时 cursor 已推进不会重复触发，无需额外幂等锁。

    所有实际写入均收敛到 write_message_task 路径，由 memory_write 锁在 worker 侧保证串行。

    Fire-and-forget：异常时记录日志，不重试。
    """
    from sqlalchemy import func, select

    from app.core.memory.memory_service import MemoryService as _MS
    _dispatch_flush = _MS.dispatch_flush_conversation
    from app.models.conversation_model import Conversation

    # Ensure an event loop is available for awaiting the async dispatch function.
    _loop = set_asyncio_event_loop()

    redis_client = get_sync_redis_client()
    if redis_client is None:
        logger.error("[FlushScan] Redis 不可用，跳过本次扫描")
        return

    # 连接到 settings.REDIS_DB 的客户端，用于读取 conv_active key 和 pending_conversations Set
    active_redis_client = None
    try:
        active_redis_client = redis.StrictRedis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None,
            decode_responses=True,
        )
        active_redis_client.ping()
    except Exception as e:
        logger.warning(
            f"[FlushScan] 无法连接 conv_active 所在 Redis DB，"
            f"将跳过空闲检查（所有对话视为活跃）: err={e}"
        )
        active_redis_client = None

    dispatched = 0
    skipped_active = 0

    try:
        # 优先从 Redis Set 获取候选对话 ID
        candidate_conv_ids: list[str] | None = None
        if active_redis_client is not None:
            try:
                from app.core.memory.pipelines.dispatcher import PENDING_CONVERSATIONS_SET_KEY
                candidates = active_redis_client.smembers(PENDING_CONVERSATIONS_SET_KEY)
                if candidates:
                    candidate_conv_ids = list(candidates)
                    logger.info(f"[FlushScan] 从 Redis Set 获取 {len(candidate_conv_ids)} 个候选对话")
            except Exception as e:
                logger.warning(f"[FlushScan] 读取 pending_conversations Set 失败，回退到 DB 查询: {e}")

        # 回退：Redis Set 不可用或为空时，走数据库查询
        if candidate_conv_ids is None:
            with get_db_context() as db:
                from app.models.memory_message_model import MemoryMessage

                max_seq_subq = (
                    select(
                        MemoryMessage.conversation_id,
                        func.max(MemoryMessage.message_seq).label("max_seq"),
                    )
                    .where(MemoryMessage.conversation_id.isnot(None))
                    .group_by(MemoryMessage.conversation_id)
                    .subquery()
                )

                rows = (
                    db.execute(
                        select(Conversation.id)
                        .join(
                            max_seq_subq,
                            Conversation.id == max_seq_subq.c.conversation_id,
                        )
                        .where(max_seq_subq.c.max_seq > Conversation.write_cursor)
                    )
                    .scalars()
                    .all()
                )
                candidate_conv_ids = [str(r) for r in rows]

        logger.info(f"[FlushScan] 发现 {len(candidate_conv_ids)} 个对话存在未写入消息")

        # 过滤：确保对话所属 app 已存在已发布版本
        if candidate_conv_ids:
            try:
                from app.models.app_model import App

                with get_db_context() as db:
                    valid_conv_ids = [
                        str(cid) for cid in db.execute(
                            select(Conversation.id)
                            .join(App, App.id == Conversation.app_id)
                            .where(
                                Conversation.id.in_(candidate_conv_ids),
                                App.current_release_id.isnot(None),
                            )
                            .distinct()
                        ).scalars().all()
                    ]

                skipped_no_release = len(candidate_conv_ids) - len(valid_conv_ids)
                if skipped_no_release:
                    logger.info(f"[FlushScan] 跳过 {skipped_no_release} 个 app 未发布的对话")
                candidate_conv_ids = valid_conv_ids
            except Exception as e:
                logger.warning(f"[FlushScan] 过滤未发布 app 失败: err={e}")

        for conv_id_str in candidate_conv_ids:
            # 检查 conv_active key 是否存在（存在则对话仍活跃，跳过）
            if active_redis_client is not None:
                try:
                    active_key = f"{CONV_ACTIVE_KEY_PREFIX}{conv_id_str}"
                    if active_redis_client.exists(active_key):
                        skipped_active += 1
                        continue
                except Exception as e:
                    logger.warning(f"[FlushScan] 检查 conv_active 失败: conv={conv_id_str}, err={e}")
                    skipped_active += 1
                    continue
            else:
                skipped_active += 1
                continue

            # 派发单个对话的兜底写入
            try:
                _loop.run_until_complete(_dispatch_flush(conv_id_str))
                dispatched += 1
                logger.info(f"[FlushScan] 已处理: conv={conv_id_str}")
            except Exception as e:
                logger.error(f"[FlushScan] 处理失败: conv={conv_id_str}, err={e}", exc_info=True)

    except Exception as e:
        logger.error(f"[FlushScan] 扫描任务失败: err={e}", exc_info=True)
    finally:
        if active_redis_client is not None:
            try:
                active_redis_client.close()
            except Exception:
                pass

    logger.info(
        f"[FlushScan] 扫描完成: 处理={dispatched}, 跳过(活跃)={skipped_active}"
    )


@celery_app.task(name="app.tasks.scan_workflow_schedule_triggers", queue="periodic_tasks", time_limit=50,
                 soft_time_limit=45)
def scan_workflow_schedule_triggers():
    """扫描并派发已发布工作流中的定时触发器。"""
    from app.services.workflow_service import WorkflowService

    now = utcnow()
    triggered = 0

    with get_db_context() as db:
        service = WorkflowService(db)
        due_triggers = service.get_due_schedule_triggers(now)
        logger.info(f"[WorkflowSchedule] 扫描到 {len(due_triggers)} 个待执行触发器")

        for app, release, _config, trigger in due_triggers:
            trigger_id = trigger.get("id")
            try:
                run_workflow_schedule_trigger.apply_async(
                    kwargs={
                        "app_id": str(app.id),
                        "release_id": str(release.id),
                        "trigger_id": trigger_id,
                        "scheduled_at": to_iso_z(now),
                    },
                    queue="workflow_trigger_tasks",
                )
                runtime = {
                    **(trigger.get("runtime") or {}),
                    "dispatch_status": "queued",
                    "last_dispatched_at": to_iso_z(now),
                    "last_scheduled_at": to_iso_z(now),
                    "last_error": None,
                }
                service.update_release_trigger_runtime_state(release.id, trigger_id, runtime)
                service.update_trigger_runtime_state(app.id, trigger_id, runtime)
                triggered += 1
                logger.info(
                    f"[WorkflowSchedule] 已派发: app_id={app.id}, release_id={release.id}, trigger_id={trigger_id}"
                )
            except Exception as exc:
                logger.error(
                    f"[WorkflowSchedule] 派发失败: app_id={app.id}, trigger_id={trigger_id}, error={exc}",
                    exc_info=True,
                )

    return {"triggered": triggered, "scanned_at": to_iso_z(now)}


@celery_app.task(name="app.tasks.run_workflow_schedule_trigger", queue="workflow_trigger_tasks")
def run_workflow_schedule_trigger(app_id: str, release_id: str, trigger_id: str, scheduled_at: str | None = None):
    """执行单个已发布的 schedule trigger。"""
    from app.services.workflow_service import WorkflowService

    run_at = as_utc_aware(parse_iso_to_utc_naive(scheduled_at)) if scheduled_at else utcnow()
    with get_db_context() as db:
        service = WorkflowService(db)
        app = db.get(App, uuid.UUID(app_id))
        release = db.get(AppRelease, uuid.UUID(release_id))
        if not app or not release:
            logger.warning(
                f"[WorkflowSchedule] 跳过不存在的任务: app_id={app_id}, release_id={release_id}, trigger_id={trigger_id}"
            )
            return {"status": "skipped", "reason": "app_or_release_not_found"}

        if app.current_release_id != release.id:
            logger.info(
                f"[WorkflowSchedule] 跳过过期发布版本任务: "
                f"app_id={app_id}, queued_release_id={release_id}, current_release_id={app.current_release_id}, "
                f"trigger_id={trigger_id}"
            )
            return {"status": "skipped", "reason": "stale_release"}

        config = service._build_runtime_workflow_config_from_release(
            release,
            real_config_id=(app.workflow_config.id if app.workflow_config else None),
        )
        trigger = service._find_trigger_node(config.nodes, trigger_id=trigger_id, trigger_type="schedule")
        if not trigger:
            logger.warning(f"[WorkflowSchedule] 跳过不存在的 trigger: trigger_id={trigger_id}")
            return {"status": "skipped", "reason": "trigger_not_found"}

        runtime = trigger.get("runtime") or {}
        running_runtime = {
            **runtime,
            "dispatch_status": "running",
            "last_started_at": to_iso_z(utcnow()),
            "last_scheduled_at": to_iso_z(run_at),
            "last_error": None,
        }
        service.update_release_trigger_runtime_state(release.id, trigger_id, running_runtime)
        service.update_trigger_runtime_state(app.id, trigger_id, running_runtime)

        try:
            asyncio.run(
                service.invoke_schedule_trigger(
                    app=app,
                    release=release,
                    config=config,
                    trigger=trigger,
                    now=run_at,
                )
            )
            completed_runtime = {
                **running_runtime,
                "dispatch_status": "completed",
                "last_triggered_at": to_iso_z(run_at),
                "last_completed_at": to_iso_z(utcnow()),
                "last_error": None,
            }
            service.update_release_trigger_runtime_state(release.id, trigger_id, completed_runtime)
            service.update_trigger_runtime_state(app.id, trigger_id, completed_runtime)
            return {"status": "completed", "trigger_id": trigger_id, "scheduled_at": to_iso_z(run_at)}
        except Exception as exc:
            failed_runtime = {
                **running_runtime,
                "dispatch_status": "failed",
                "last_failed_at": to_iso_z(utcnow()),
                "last_error": str(exc),
            }
            service.update_release_trigger_runtime_state(release.id, trigger_id, failed_runtime)
            service.update_trigger_runtime_state(app.id, trigger_id, failed_runtime)
            logger.error(
                f"[WorkflowSchedule] 执行失败: app_id={app_id}, release_id={release_id}, trigger_id={trigger_id}, error={exc}",
                exc_info=True,
            )
            raise


@celery_app.task(name="app.tasks.draft_data_clean", queue="memory_tasks")
def draft_data_clean():
    import asyncio
    from app.repositories.neo4j.neo4j_connector import Neo4jConnector

    with get_db_context() as db:
        stmt = select(EndUser.id).join(
            User,
            cast(User.id, String) == EndUser.other_id
        ).where(
            EndUser.is_active == True
        )
        result = db.execute(stmt)
        end_user_ids = [str(eid) for eid in result.scalars()]

        if not end_user_ids:
            logger.info("draft_data_clean: 没有需要清理的终端用户")
            return {"deleted_count": 0}

        updated = (
            db.query(EndUser)
            .filter(EndUser.id.in_(end_user_ids))
            .update({"is_active": False}, synchronize_session=False)
        )
        db.commit()
        logger.info(f"draft_data_clean: 软删除 {updated} 个终端用户")

    async def _delete_neo4j_groups():
        async with Neo4jConnector() as connector:
            deleted = 0
            for eid in end_user_ids:
                try:
                    await connector.delete_group(eid)
                    deleted += 1
                except Exception as e:
                    logger.error(f"draft_data_clean: Neo4j 删除失败 end_user_id={eid}: {e}")
        return deleted

    neo4j_deleted = asyncio.run(_delete_neo4j_groups())
    logger.info(f"draft_data_clean: Neo4j 删除 {neo4j_deleted} 组节点")

    return {"pg_deleted": updated, "neo4j_deleted": neo4j_deleted}

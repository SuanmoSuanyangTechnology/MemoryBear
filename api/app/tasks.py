import asyncio
import json
import os
import re
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any, Dict, List, Optional

import redis
from redis.exceptions import RedisError
from fastapi.encoders import jsonable_encoder

# Import a unified Celery instance
from app.celery_app import celery_app
from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.rag.crawler.web_crawler import WebCrawler
from app.core.rag.graphrag.general.index import init_graphrag, run_graphrag_for_kb
from app.core.rag.graphrag.utils import get_llm_cache, set_llm_cache
from app.core.rag.integrations.feishu.client import FeishuAPIClient
from app.core.rag.integrations.feishu.models import FileInfo
from app.core.rag.integrations.yuque.client import YuqueAPIClient
from app.core.rag.integrations.yuque.models import YuqueDocInfo
from app.core.rag.llm.chat_model import Base
from app.core.rag.llm.cv_model import QWenCV
from app.core.rag.llm.embedding_model import OpenAIEmbed
from app.core.rag.llm.sequence2txt_model import QWenSeq2txt
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.prompts.generator import question_proposal, qa_proposal
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import (
    ElasticSearchVectorFactory,
)
from app.db import get_db_context
from app.models import Document, File, Knowledge
from app.models.end_user_model import EndUser
from app.schemas import document_schema, file_schema
from app.services.memory_agent_service import MemoryAgentService, get_end_user_connected_config
from app.schemas.memory_agent_schema import WriteMemoryRequest
from app.services.memory_forget_service import MemoryForgetService
from app.utils.config_utils import resolve_config_id
from app.utils.redis_lock import RedisFairLock
from app.core.memory.utils.memory_count_utils import sync_end_user_memory_count_from_neo4j

logger = get_logger(__name__)

# ── 预编译文件类型正则 & 常量 ──────────────────────────────────
AUDIO_PATTERN = re.compile(
    r"\.(da|wave|wav|mp3|aac|flac|ogg|aiff|au|midi|wma|realaudio|vqf|oggvorbis|ape?)$",
    re.IGNORECASE,
)
VIDEO_IMAGE_PATTERN = re.compile(
    r"\.(png|jpeg|jpg|gif|bmp|svg|mp4|mov|avi|flv|mpeg|mpg|webm|wmv|3gp|3gpp|mkv?)$",
    re.IGNORECASE,
)
DEFAULT_PARSE_LANGUAGE = "Chinese"
DEFAULT_PARSE_TO_PAGE = 100_000
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "20"))
# Embedding 并发写入的最大线程数，需根据模型 API rate limit 调整
EMBEDDING_MAX_WORKERS = int(os.getenv("EMBEDDING_MAX_WORKERS", "3"))
# auto_questions LLM 并发调用的最大线程数
AUTO_QUESTIONS_MAX_WORKERS = int(os.getenv("AUTO_QUESTIONS_MAX_WORKERS", "5"))

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
    
    使用连接池提供的客户端，支持自动重连和健康检查。
    如果 Redis 不可用，返回 None，调用方应优雅降级。
    
    Returns:
        redis.StrictRedis: Redis 客户端实例，如果连接失败则返回 None
    """
    try:
        pool = _get_or_create_redis_pool()
        if pool is None:
            return None

        client = redis.StrictRedis(connection_pool=pool)
        # 验证连接可用性
        client.ping()
        return client
    except RedisError as e:
        logger.error(f"Redis connection failed: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting Redis client: {e}", exc_info=True)
        return None


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
    """Gracefully shutdown pending async generators and tasks on the event loop.

    This prevents 'RuntimeError: Event loop is closed' from httpx.AsyncClient.__del__
    by giving pending aclose() coroutines a chance to run before the loop is discarded.

    Note: This only tears down the given loop. Callers that need a fresh event
    loop afterwards should use ``set_asyncio_event_loop()`` explicitly.
    """
    try:
        # Cancel and collect all remaining tasks
        all_tasks = asyncio.all_tasks(loop)
        if all_tasks:
            for task in all_tasks:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*all_tasks, return_exceptions=True))
        # Shutdown async generators (triggers __aclose__ on httpx clients etc.)
        loop.run_until_complete(loop.shutdown_asyncgens())
    except Exception:
        pass
    finally:
        loop.close()


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


def _build_vision_model(file_path: str, db_knowledge):
    """根据文件类型选择合适的视觉/音频模型，避免冗余初始化。"""
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
    if VIDEO_IMAGE_PATTERN.search(file_path):
        omni_key = os.getenv("QWEN3_OMNI_API_KEY", "")
        omni_model = os.getenv("QWEN3_OMNI_MODEL_NAME", "qwen3-omni-flash")
        omni_base = os.getenv("QWEN3_OMNI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        return QWenCV(
            key=omni_key,
            model_name=omni_model,
            lang=DEFAULT_PARSE_LANGUAGE,
            base_url=omni_base,
        )
    # 默认：使用知识库配置的 image2text 模型
    return QWenCV(
        key=db_knowledge.image2text.api_keys[0].api_key,
        model_name=db_knowledge.image2text.api_keys[0].model_name,
        lang=DEFAULT_PARSE_LANGUAGE,
        base_url=db_knowledge.image2text.api_keys[0].api_base,
    )


@celery_app.task(name="app.core.rag.tasks.parse_document")
def parse_document(file_key: str, document_id: uuid.UUID, file_name: str = ""):
    """
    Document parsing, vectorization, and storage.
    
    Args:
        file_key: Storage key for FileStorageService (e.g. "kb/{kb_id}/{file_id}.docx")
        document_id: Document UUID
        file_name: Original file name (used for extension detection in chunk())
    """

    db_document = None
    progress_lines: list[str] = [f"{datetime.now().strftime('%H:%M:%S')} Task has been received."]

    def _progress_msg() -> str:
        return "\n".join(progress_lines) + "\n"

    with get_db_context() as db:
      try:
        if not isinstance(document_id, uuid.UUID):
            document_id = uuid.UUID(str(document_id))

        db_document = db.query(Document).filter(Document.id == document_id).first()
        if db_document is None:
            raise ValueError(f"Document {document_id} not found")
        db_knowledge = db.query(Knowledge).filter(Knowledge.id == db_document.kb_id).first()
        if db_knowledge is None:
            raise ValueError(f"Knowledge {db_document.kb_id} not found")

        # Use file_name from argument or fall back to document record
        if not file_name:
            file_name = db_document.file_name

        # 1. Download file from storage backend
        progress_lines.append(f"{datetime.now().strftime('%H:%M:%S')} Start to parse.")
        start_time = time.time()
        db_document.progress = 0.0
        db_document.progress_msg = _progress_msg()
        db_document.process_begin_at = datetime.now(tz=timezone.utc)
        db_document.process_duration = 0.0
        db_document.run = 1
        db.commit()
        db.refresh(db_document)

        # Read file content from storage backend (no NFS dependency)
        from app.services.file_storage_service import FileStorageService
        import asyncio
        storage_service = FileStorageService()

        async def _download():
            return await storage_service.download_file(file_key)

        try:
            file_binary = asyncio.run(_download())
        except RuntimeError:
            # If there's already a running loop (e.g. in some worker configurations)
            loop = asyncio.new_event_loop()
            try:
                file_binary = loop.run_until_complete(_download())
            finally:
                loop.close()
        if not file_binary:
            raise IOError(f"Downloaded empty file from storage: {file_key}")
        logger.info(f"[ParseDoc] Downloaded {len(file_binary)} bytes from storage key: {file_key}")

        def progress_callback(prog=None, msg=None):
            progress_lines.append(f"{datetime.now().strftime('%H:%M:%S')} parse progress: {prog} msg: {msg}.")

        # Prepare vision_model for parsing
        vision_model = _build_vision_model(file_name, db_knowledge)

        from app.core.rag.app.naive import chunk
        logger.info(f"[ParseDoc] file_binary size={len(file_binary)} bytes, type={type(file_binary).__name__}, bool={bool(file_binary)}")

        parent_child_mode = db_document.is_parent_child_mode
        if parent_child_mode:
            from app.core.rag.app.naive import chunk_parent_child
            child_res, parent_res, parent_id_map = chunk_parent_child(
                filename=file_name,
                binary=file_binary,
                from_page=0,
                to_page=DEFAULT_PARSE_TO_PAGE,
                callback=progress_callback,
                vision_model=vision_model,
                parser_config=db_document.parser_config,
                is_root=False,
            )
        else:
            res = chunk(filename=file_name,
                        binary=file_binary,
                        from_page=0,
                        to_page=DEFAULT_PARSE_TO_PAGE,
                        callback=progress_callback,
                        vision_model=vision_model,
                        parser_config=db_document.parser_config,
                        is_root=False)

        progress_lines.append(f"{datetime.now().strftime('%H:%M:%S')} Finish parsing.")
        db_document.progress = 0.8
        db_document.progress_msg = _progress_msg()
        db.commit()
        db.refresh(db_document)

        # 2. Document vectorization and storage
        total_chunks = (len(child_res) + len(parent_res)) if parent_child_mode else len(res)
        progress_lines.append(f"{datetime.now().strftime('%H:%M:%S')} Generate {total_chunks} chunks.")

        if total_chunks == 0:
            progress_lines.append(f"{datetime.now().strftime('%H:%M:%S')} No chunks generated, skipping vectorization.")
        else:
            total_batches = ceil(total_chunks / EMBEDDING_BATCH_SIZE)
            progress_per_batch = 0.2 / total_batches
            vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
            # 2.1 Delete document vector index
            vector_service.delete_by_metadata_field(key="document_id", value=str(document_id))
            # 2.2 Vectorize and import batch documents
            auto_questions_topn = db_document.parser_config.get("auto_questions", 0)
            qa_prompt = db_document.parser_config.get("qa_prompt", None)
            chat_model = None
            if auto_questions_topn:
                chat_model = Base(
                    key=db_knowledge.llm.api_keys[0].api_key,
                    model_name=db_knowledge.llm.api_keys[0].model_name,
                    base_url=db_knowledge.llm.api_keys[0].api_base,
                )
                logger.info(f"[QA] LLM model: {db_knowledge.llm.api_keys[0].model_name}, base_url: {db_knowledge.llm.api_keys[0].api_base}")
                if qa_prompt:
                    logger.info(f"[QA] Using custom prompt ({len(qa_prompt)} chars)")

            # 预先构建所有 batch 的 chunks，保证 sort_id 全局有序
            all_batch_chunks: list[list[DocumentChunk]] = []

            if parent_child_mode:
                # 父子分块模式：parent chunks + child chunks
                parent_chunks_list = []
                parent_id_to_doc_id = {}

                for idx, item in enumerate(parent_res):
                    parent_doc_id = uuid.uuid4().hex
                    parent_id_to_doc_id[idx] = parent_doc_id
                    meta = {
                        "doc_id": parent_doc_id,
                        "file_id": str(db_document.file_id),
                        "file_name": db_document.file_name,
                        "file_created_at": int(db_document.created_at.timestamp() * 1000),
                        "document_id": str(db_document.id),
                        "knowledge_id": str(db_document.kb_id),
                        "sort_id": idx,
                        "status": 1,
                        "chunk_type": "parent",
                    }
                    parent_chunks_list.append(
                        DocumentChunk(page_content=item["content_with_weight"], metadata=meta))

                child_chunks_list = []
                for idx, item in enumerate(child_res):
                    parent_idx = parent_id_map.get(idx)
                    parent_doc_id = parent_id_to_doc_id.get(parent_idx, "")
                    meta = {
                        "doc_id": uuid.uuid4().hex,
                        "file_id": str(db_document.file_id),
                        "file_name": db_document.file_name,
                        "file_created_at": int(db_document.created_at.timestamp() * 1000),
                        "document_id": str(db_document.id),
                        "knowledge_id": str(db_document.kb_id),
                        "sort_id": idx,
                        "status": 1,
                        "chunk_type": "child",
                        "parent_id": parent_doc_id,
                    }
                    child_chunks_list.append(
                        DocumentChunk(page_content=item["content_with_weight"], metadata=meta))

                all_chunks = parent_chunks_list + child_chunks_list
                for batch_start in range(0, len(all_chunks), EMBEDDING_BATCH_SIZE):
                    batch_end = min(batch_start + EMBEDDING_BATCH_SIZE, len(all_chunks))
                    all_batch_chunks.append(all_chunks[batch_start:batch_end])

                progress_lines.append(
                    f"{datetime.now().strftime('%H:%M:%S')} Parent-child mode: {len(parent_chunks_list)} parent chunks + "
                    f"{len(child_chunks_list)} child chunks prepared.")

            elif auto_questions_topn:
                # QA 模式（FastGPT 方案）：
                # 1. 原 chunk 标记为 source（保留供 GraphRAG 使用，不参与检索）
                # 2. LLM 生成 QA 对，每个 QA 对独立存储为 qa chunk
                indexed_items = list(enumerate(res))

                def _generate_qa(idx_item: tuple[int, dict]) -> tuple[int, list]:
                    """为单个 chunk 生成 QA 对（带缓存），返回 (global_idx, qa_pairs)"""
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
                            logger.error(f"[QA] LLM call failed: model={chat_model.model_name}, base_url={getattr(chat_model, 'base_url', 'N/A')}, error={e}")
                            return global_idx, []
                        logger.info(f"[QA] Chunk {global_idx} generated {len(pairs)} QA pairs")
                        # 缓存存 JSON 字符串
                        set_llm_cache(chat_model.model_name, content, json.dumps(pairs, ensure_ascii=False), "qa",
                                      cache_params)
                        return global_idx, pairs
                    logger.info(f"[QA] Cache hit for chunk {global_idx}, cache_params={cache_params}, cached_type={type(cached).__name__}")
                    # 从缓存读取：可能是 JSON 字符串或旧格式纯文本
                    if isinstance(cached, str):
                        try:
                            parsed = json.loads(cached)
                            if isinstance(parsed, list):
                                logger.info(f"[QA] Chunk {global_idx} loaded {len(parsed)} QA pairs from cache")
                                return global_idx, parsed
                        except (json.JSONDecodeError, TypeError):
                            pass
                        # 旧缓存格式（纯文本问题），尝试解析
                        from app.core.rag.prompts.generator import parse_qa_pairs
                        return global_idx, parse_qa_pairs(cached) if cached else []
                    return global_idx, cached if isinstance(cached, list) else []

                # 并发调用 LLM 生成 QA 对
                qa_map: dict[int, list] = {}
                with ThreadPoolExecutor(max_workers=AUTO_QUESTIONS_MAX_WORKERS) as q_executor:
                    futures = {q_executor.submit(_generate_qa, item): item[0]
                               for item in indexed_items}
                    for future in futures:
                        global_idx, pairs = future.result()
                        qa_map[global_idx] = pairs

                progress_lines.append(
                    f"{datetime.now().strftime('%H:%M:%S')} QA pairs generated for {total_chunks} chunks "
                    f"(workers={AUTO_QUESTIONS_MAX_WORKERS}).")

                # 组装 chunks：source chunks + qa chunks
                source_chunks = []
                qa_chunks = []
                qa_sort_id = 0

                for global_idx in range(total_chunks):
                    item = res[global_idx]
                    source_chunk_id = uuid.uuid4().hex

                    # source chunk：保留原文，供 GraphRAG 使用，不参与向量检索
                    source_meta = {
                        "doc_id": source_chunk_id,
                        "file_id": str(db_document.file_id),
                        "file_name": db_document.file_name,
                        "file_created_at": int(db_document.created_at.timestamp() * 1000),
                        "document_id": str(db_document.id),
                        "knowledge_id": str(db_document.kb_id),
                        "sort_id": global_idx,
                        "status": 1,
                        "chunk_type": "source",
                    }
                    source_chunks.append(
                        DocumentChunk(page_content=item["content_with_weight"], metadata=source_meta))

                    # qa chunks：每个 QA 对独立存储
                    pairs = qa_map.get(global_idx, [])
                    for pair in pairs:
                        qa_meta = {
                            "doc_id": uuid.uuid4().hex,
                            "file_id": str(db_document.file_id),
                            "file_name": db_document.file_name,
                            "file_created_at": int(db_document.created_at.timestamp() * 1000),
                            "document_id": str(db_document.id),
                            "knowledge_id": str(db_document.kb_id),
                            "sort_id": qa_sort_id,
                            "status": 1,
                            "chunk_type": "qa",
                            "question": pair["question"],
                            "answer": pair["answer"],
                            "source_chunk_id": source_chunk_id,
                        }
                        # page_content 存 question，用于向量索引
                        qa_chunks.append(
                            DocumentChunk(page_content=pair["question"], metadata=qa_meta))
                        qa_sort_id += 1

                # 按 batch 分组（source + qa 一起）
                all_chunks = source_chunks + qa_chunks
                for batch_start in range(0, len(all_chunks), EMBEDDING_BATCH_SIZE):
                    batch_end = min(batch_start + EMBEDDING_BATCH_SIZE, len(all_chunks))
                    all_batch_chunks.append(all_chunks[batch_start:batch_end])

                progress_lines.append(
                    f"{datetime.now().strftime('%H:%M:%S')} QA mode: {len(source_chunks)} source chunks + "
                    f"{len(qa_chunks)} QA chunks prepared.")
            else:
                # 无 auto_questions：直接构建 chunks
                for batch_start in range(0, total_chunks, EMBEDDING_BATCH_SIZE):
                    batch_end = min(batch_start + EMBEDDING_BATCH_SIZE, total_chunks)
                    chunks = []
                    for global_idx in range(batch_start, batch_end):
                        item = res[global_idx]
                        metadata = {
                            "doc_id": uuid.uuid4().hex,
                            "file_id": str(db_document.file_id),
                            "file_name": db_document.file_name,
                            "file_created_at": int(db_document.created_at.timestamp() * 1000),
                            "document_id": str(db_document.id),
                            "knowledge_id": str(db_document.kb_id),
                            "sort_id": global_idx,
                            "status": 1,
                        }
                        chunks.append(DocumentChunk(page_content=item["content_with_weight"], metadata=metadata))
                    all_batch_chunks.append(chunks)

            # 并发提交 embedding + ES 写入，max_workers 控制模型 API 并发压力
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

            with ThreadPoolExecutor(max_workers=EMBEDDING_MAX_WORKERS) as executor:
                futures = {
                    executor.submit(_embed_and_store, i, batch_chunks): i
                    for i, batch_chunks in enumerate(all_batch_chunks)
                }
                for future in futures:
                    future.result()

            # 如果有 batch 失败，汇总抛出
            if batch_errors:
                failed_detail = "; ".join(
                    f"batch {i}: {type(err).__name__}: {err}"
                    for i, err in sorted(batch_errors.items())
                )
                raise RuntimeError(f"Embedding failed for {len(batch_errors)}/{total_batches} batch(es). {failed_detail}")

            # 所有 batch 完成后一次性更新进度
            db_document.progress = 0.8 + 0.2  # 直接到 1.0 前的状态
            progress_lines.append(f"{datetime.now().strftime('%H:%M:%S')} All {total_batches} batches embedded (workers={EMBEDDING_MAX_WORKERS}).")
            db_document.progress_msg = _progress_msg()
            db_document.process_duration = time.time() - start_time
            db_document.run = 0
            db.commit()
            db.refresh(db_document)

        # Vectorization and data entry completed
        progress_lines.append(f"{datetime.now().strftime('%H:%M:%S')} Indexing done.")
        db_document.chunk_num = total_chunks
        db_document.progress = 1.0
        db_document.process_duration = time.time() - start_time
        progress_lines.append(f"{datetime.now().strftime('%H:%M:%S')} Task done ({db_document.process_duration}s).")
        db_document.progress_msg = _progress_msg()
        db_document.run = 0
        db.commit()

        # GraphRAG: 异步派发到独立队列，不阻塞文档解析流程
        if db_knowledge.parser_config and db_knowledge.parser_config.get("graphrag", {}).get("use_graphrag", False):
            progress_lines.append(f"{datetime.now().strftime('%H:%M:%S')} GraphRAG enabled, dispatching async task.")
            db_document.progress_msg = _progress_msg()
            db.commit()
            build_graphrag_for_document.delay(str(document_id), str(db_knowledge.id))

        result = f"parse document '{db_document.file_name}' processed successfully."
        logger.info(f"[ParseDoc] document={document_id} file='{db_document.file_name}' done in {db_document.process_duration:.1f}s, chunks={total_chunks}")
        return result
      except Exception as e:
        logger.error(f"[ParseDoc] document={document_id} failed: {e}", exc_info=True)
        if db_document is not None:
            try:
                db.rollback()
                db_document.progress_msg = _progress_msg() + f"Failed to vectorize and import the parsed document:{str(e)}\n"
                db_document.run = 0
                db.commit()
            except Exception:
                logger.warning(f"[ParseDoc] document={document_id} failed to update error status in DB", exc_info=True)
        # db_document 可能处于 detached/expired 状态，用之前缓存的值或 document_id 兜底
        file_name = getattr(db_document, 'file_name', None) if db_document else None
        return f"parse document '{file_name or document_id}' failed."


@celery_app.task(name="app.core.rag.tasks.build_graphrag_for_kb")
def build_graphrag_for_kb(kb_id: uuid.UUID):
    """
    build knowledge graph
    """
    import importlib

    import trio
    importlib.reload(trio)

    with get_db_context() as db:
        try:
            if not isinstance(kb_id, uuid.UUID):
                kb_id = uuid.UUID(str(kb_id))

            db_knowledge = db.query(Knowledge).filter(Knowledge.id == kb_id).first()
            if db_knowledge is None:
                logger.error(f"[GraphRAG-KB] knowledge={kb_id} not found")
                return "build knowledge graph failed: knowledge not found"

            if not (db_knowledge.parser_config and
                    db_knowledge.parser_config.get("graphrag", {}).get("use_graphrag", False)):
                return f"build knowledge graph '{db_knowledge.name}' skipped: graphrag not enabled"

            db_documents = db.query(Document).filter(Document.kb_id == kb_id).all()
            document_ids = [str(doc.id) for doc in db_documents]

            chat_model = Base(
                key=db_knowledge.llm.api_keys[0].api_key,
                model_name=db_knowledge.llm.api_keys[0].model_name,
                base_url=db_knowledge.llm.api_keys[0].api_base,
            )
            embedding_model = OpenAIEmbed(
                key=db_knowledge.embedding.api_keys[0].api_key,
                model_name=db_knowledge.embedding.api_keys[0].model_name,
                base_url=db_knowledge.embedding.api_keys[0].api_base,
            )
            vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

            graphrag_conf = db_knowledge.parser_config.get("graphrag", {})
            with_resolution = graphrag_conf.get("resolution", False)
            with_community = graphrag_conf.get("community", False)

            task = {
                "id": str(db_knowledge.id),
                "workspace_id": str(db_knowledge.workspace_id),
                "kb_id": str(db_knowledge.id),
                "parser_config": db_knowledge.parser_config,
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
                    parser_config=db_knowledge.parser_config,
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

            return f"build knowledge graph '{db_knowledge.name}' processed successfully."
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

    with get_db_context() as db:
        try:
            db_document = db.query(Document).filter(Document.id == uuid.UUID(document_id)).first()
            db_knowledge = db.query(Knowledge).filter(Knowledge.id == uuid.UUID(knowledge_id)).first()
            if db_document is None or db_knowledge is None:
                logger.error(f"[GraphRAG] document={document_id} or knowledge={knowledge_id} not found")
                return "build_graphrag_for_document failed: record not found"

            graphrag_conf = db_knowledge.parser_config.get("graphrag", {})
            with_resolution = graphrag_conf.get("resolution", False)
            with_community = graphrag_conf.get("community", False)

            chat_model = Base(
                key=db_knowledge.llm.api_keys[0].api_key,
                model_name=db_knowledge.llm.api_keys[0].model_name,
                base_url=db_knowledge.llm.api_keys[0].api_base,
            )
            embedding_model = OpenAIEmbed(
                key=db_knowledge.embedding.api_keys[0].api_key,
                model_name=db_knowledge.embedding.api_keys[0].model_name,
                base_url=db_knowledge.embedding.api_keys[0].api_base,
            )
            vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

            task = {
                "id": document_id,
                "workspace_id": str(db_knowledge.workspace_id),
                "kb_id": str(db_knowledge.id),
                "parser_config": db_knowledge.parser_config,
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
                    parser_config=db_knowledge.parser_config,
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

            # 更新文档进度信息
            db_document.progress_msg = (db_document.progress_msg or "") + \
                f"{datetime.now().strftime('%H:%M:%S')} Knowledge Graph done ({duration:.1f}s)\n"
            db.commit()

            return f"build_graphrag_for_document '{document_id}' processed successfully."
        except Exception as e:
            logger.error(f"[GraphRAG] doc={document_id} failed: {e}", exc_info=True)
            return f"build_graphrag_for_document '{document_id}' failed: {e}"


@celery_app.task(name="app.core.rag.tasks.import_qa_chunks", queue="qa_import")
def import_qa_chunks(kb_id: str, document_id: str, filename: str, contents: bytes):
    """
    异步导入 QA 问答对（CSV/Excel）
    
    文件格式：第一行标题（跳过），第一列问题，第二列答案
    """
    import csv as csv_module
    import io

    db = None
    try:
        from app.db import get_db_context
        with get_db_context() as db:
            db_document = db.query(Document).filter(Document.id == uuid.UUID(document_id)).first()
            db_knowledge = db.query(Knowledge).filter(Knowledge.id == uuid.UUID(kb_id)).first()
            if not db_document or not db_knowledge:
                logger.error(f"[ImportQA] document={document_id} or knowledge={kb_id} not found")
                return {"error": "document or knowledge not found", "imported": 0}

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
                    if len(row) >= 2 and row[0].strip() and row[1].strip():
                        qa_pairs.append({"question": row[0].strip(), "answer": row[1].strip()})
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
                            if len(row) >= 2 and row[0] and row[1]:
                                q = str(row[0]).strip()
                                a = str(row[1]).strip()
                                if q and a:
                                    qa_pairs.append({"question": q, "answer": a})
                            elif len(row) >= 1 and row[0]:
                                failed_rows.append(i + 1)
                    wb.close()
                except Exception as e:
                    logger.error(f"[ImportQA] Excel parse failed: {e}")
                    return {"error": f"Excel parse failed: {e}", "imported": 0}

            if not qa_pairs:
                logger.warning(f"[ImportQA] No valid QA pairs found in {filename}")
                return {"error": "No valid QA pairs found", "imported": 0}

            logger.info(f"[ImportQA] Parsed {len(qa_pairs)} QA pairs from {filename}, failed_rows={failed_rows}")

            # 2. 写入 ES
            vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

            sort_id = 0
            total, items = vector_service.search_by_segment(document_id=document_id, pagesize=1, page=1, asc=False)
            if items:
                sort_id = items[0].metadata["sort_id"]

            chunks = []
            for pair in qa_pairs:
                sort_id += 1
                doc_id = uuid.uuid4().hex
                metadata = {
                    "doc_id": doc_id,
                    "file_id": str(db_document.file_id),
                    "file_name": db_document.file_name,
                    "file_created_at": int(db_document.created_at.timestamp() * 1000),
                    "document_id": document_id,
                    "knowledge_id": kb_id,
                    "sort_id": sort_id,
                    "status": 1,
                    "chunk_type": "qa",
                    "question": pair["question"],
                    "answer": pair["answer"],
                }
                chunks.append(DocumentChunk(page_content=pair["question"], metadata=metadata))

            batch_size = 50
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i:i + batch_size]
                vector_service.add_chunks(batch)

            # 3. 更新 chunk_num 和 progress
            db_document.chunk_num += len(chunks)
            db_document.progress = 1.0
            db_document.progress_msg = f"QA 导入完成: {len(chunks)} 条"
            db.commit()

            result = {"imported": len(chunks), "failed_rows": failed_rows}
            logger.info(f"[ImportQA] Done: imported={len(chunks)}, failed={len(failed_rows)}")
            return result

    except Exception as e:
        logger.error(f"[ImportQA] Failed: {e}", exc_info=True)
        # 尝试更新文档状态为失败
        try:
            from app.db import get_db_context
            with get_db_context() as err_db:
                doc = err_db.query(Document).filter(Document.id == uuid.UUID(document_id)).first()
                if doc:
                    doc.progress = -1.0
                    doc.progress_msg = f"QA 导入失败: {str(e)[:200]}"
                    err_db.commit()
        except Exception:
            pass
        return {"error": str(e), "imported": 0}


@celery_app.task(name="app.core.rag.tasks.sync_knowledge_for_kb")
def sync_knowledge_for_kb(kb_id: uuid.UUID):
    """
    sync knowledge document and Document parsing, vectorization, and storage
    """
    with get_db_context() as db:
      try:
        if not isinstance(kb_id, uuid.UUID):
            kb_id = uuid.UUID(str(kb_id))

        db_knowledge = db.query(Knowledge).filter(Knowledge.id == kb_id).first()
        if db_knowledge is None:
            logger.error(f"[SyncKB] knowledge={kb_id} not found")
            return "sync knowledge failed: knowledge not found"

        # 1. get vector_service
        vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

        # 2. sync data
        match db_knowledge.type:
            case "Web":  # Crawl webpages in batches through a web crawler
                entry_url = db_knowledge.parser_config.get("entry_url", "")
                max_pages = db_knowledge.parser_config.get("max_pages", 20)
                delay_seconds = db_knowledge.parser_config.get("delay_seconds", 1.0)
                timeout_seconds = db_knowledge.parser_config.get("timeout_seconds", 10)
                user_agent = db_knowledge.parser_config.get("user_agent", "KnowledgeBaseCrawler/1.0")
                # Create crawler
                crawler = WebCrawler(
                    entry_url=entry_url,
                    max_pages=max_pages,
                    delay_seconds=delay_seconds,
                    timeout_seconds=timeout_seconds,
                    user_agent=user_agent
                )
                try:
                    # 初始化存储已爬取 URLs 的集合
                    file_urls = set()
                    # crawl entry_url by yield
                    for crawled_document in crawler.crawl():
                        file_urls.add(crawled_document.url)
                        db_file = db.query(File).filter(File.kb_id == db_knowledge.id,
                                                        File.file_url == crawled_document.url).first()
                        if db_file:
                            if db_file.file_size == crawled_document.content_length:  # same
                                continue
                            else:  # --update
                                if crawled_document.content_length:
                                    # 1. update file
                                    db_file.file_name = f"{crawled_document.title}.txt"
                                    db_file.file_ext = ".txt"
                                    db_file.file_size = crawled_document.content_length
                                    db.commit()
                                    db.refresh(db_file)
                                    # Construct a save path：/files/{kb_id}/{parent_id}/{file.id}{file_extension}
                                    save_dir = os.path.join(settings.FILE_PATH, str(db_knowledge.id),
                                                            str(db_knowledge.id))
                                    Path(save_dir).mkdir(parents=True,
                                                         exist_ok=True)  # Ensure that the directory exists
                                    save_path = os.path.join(save_dir, f"{db_file.id}{db_file.file_ext}")
                                    # update file
                                    if os.path.exists(save_path):
                                        os.remove(save_path)  # Delete a single file
                                    content_bytes = crawled_document.content.encode('utf-8')
                                    with open(save_path, "wb") as f:
                                        f.write(content_bytes)
                                    # 2. update a document
                                    db_document = db.query(Document).filter(Document.kb_id == db_knowledge.id,
                                                                            Document.file_id == db_file.id).first()
                                    if db_document:
                                        db_document.file_name = db_file.file_name
                                        db_document.file_ext = db_file.file_ext
                                        db_document.file_size = db_file.file_size
                                        db_document.updated_at = datetime.now()
                                        db.commit()
                                        db.refresh(db_document)
                                        # 3. Document parsing, vectorization, and storage
                                        parse_document(file_path=save_path, document_id=db_document.id)
                        else:  # --add
                            if crawled_document.content_length:
                                # 1. upload file
                                upload_file = file_schema.FileCreate(
                                    kb_id=db_knowledge.id,
                                    created_by=db_knowledge.created_by,
                                    parent_id=db_knowledge.id,
                                    file_name=f"{crawled_document.title}.txt",
                                    file_ext=".txt",
                                    file_size=crawled_document.content_length,
                                    file_url=crawled_document.url,
                                )
                                db_file = File(**upload_file.model_dump())
                                db.add(db_file)
                                db.commit()
                                # Construct a save path：/files/{kb_id}/{parent_id}/{file.id}{file_extension}
                                save_dir = os.path.join(settings.FILE_PATH, str(db_knowledge.id), str(db_knowledge.id))
                                Path(save_dir).mkdir(parents=True, exist_ok=True)  # Ensure that the directory exists
                                save_path = os.path.join(save_dir, f"{db_file.id}{db_file.file_ext}")
                                # Save file
                                content_bytes = crawled_document.content.encode('utf-8')
                                with open(save_path, "wb") as f:
                                    f.write(content_bytes)
                                # 2. Create a document
                                create_document_data = document_schema.DocumentCreate(
                                    kb_id=db_knowledge.id,
                                    created_by=db_knowledge.created_by,
                                    file_id=db_file.id,
                                    file_name=db_file.file_name,
                                    file_ext=db_file.file_ext,
                                    file_size=db_file.file_size,
                                    file_meta={},
                                    parser_id="naive",
                                    parser_config={
                                        "layout_recognize": "DeepDOC",
                                        "chunk_token_num": 130,
                                        "delimiter": "\n",
                                        "auto_keywords": 0,
                                        "auto_questions": 0,
                                        "html4excel": "false"
                                    }
                                )
                                db_document = Document(**create_document_data.model_dump())
                                db.add(db_document)
                                db.commit()
                                # 3. Document parsing, vectorization, and storage
                                parse_document(file_path=save_path, document_id=db_document.id)
                    db_files = db.query(File).filter(File.kb_id == db_knowledge.id,
                                                     File.file_url.notin_(file_urls)).all()
                    if db_files:  # --delete
                        for db_file in db_files:
                            db_document = db.query(Document).filter(Document.kb_id == db_knowledge.id,
                                                                    Document.file_id == db_file.id).first()
                            if db_document:
                                # 1. Delete vector index
                                vector_service.delete_by_metadata_field(key="document_id", value=str(db_document.id))
                                # 2. Delete document
                                db.delete(db_document)
                            # 3. Delete file
                            file_path = Path(
                                settings.FILE_PATH,
                                str(db_file.kb_id),
                                str(db_file.parent_id),
                                f"{db_file.id}{db_file.file_ext}"
                            )
                            if file_path.exists():
                                file_path.unlink()  # Delete a single file
                            db.delete(db_file)
                        # commit transaction
                        db.commit()

                except Exception as e:
                    logger.error(f"[SyncKB] Error during crawl: {e}", exc_info=True)
            case "Third-party":  # Integration of knowledge bases from three parties
                yuque_user_id = db_knowledge.parser_config.get("yuque_user_id", "")
                feishu_app_id = db_knowledge.parser_config.get("feishu_app_id", "")
                if yuque_user_id:  # Yuque Knowledge Base
                    yuque_token = db_knowledge.parser_config.get("yuque_token", "")
                    # Create yuqueAPIClient
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
                            db_file = db.query(File).filter(File.kb_id == db_knowledge.id,
                                                            File.file_url == doc.slug).first()
                            if db_file:
                                if db_file.created_at == doc.updated_at:  # same
                                    continue
                                else:  # --update
                                    # 1. update file
                                    # Construct a save path：/files/{kb_id}/{parent_id}/{file.id}{file_extension}
                                    save_dir = os.path.join(settings.FILE_PATH, str(db_knowledge.id),
                                                            str(db_knowledge.id))
                                    Path(save_dir).mkdir(parents=True,
                                                         exist_ok=True)  # Ensure that the directory exists

                                    # download document from Feishu FileInfo
                                    async def async_download_document(api_client: YuqueAPIClient, doc: YuqueDocInfo,
                                                                      save_dir: str):
                                        async with api_client as client:
                                            file_path = await client.download_document(doc, save_dir)
                                            return file_path

                                    file_path = asyncio.run(async_download_document(api_client, doc, save_dir))

                                    save_path = os.path.join(save_dir, f"{db_file.id}{db_file.file_ext}")
                                    # update file
                                    if os.path.exists(save_path):
                                        os.remove(save_path)  # Delete a single file
                                    shutil.copyfile(file_path, save_path)
                                    # update db_file
                                    file_name = os.path.basename(file_path)
                                    _, file_extension = os.path.splitext(file_name)
                                    file_size = os.path.getsize(file_path)
                                    db_file.file_name = file_name
                                    db_file.file_ext = file_extension.lower()
                                    db_file.file_size = file_size
                                    db_file.created_at = doc.updated_at
                                    db.commit()
                                    db.refresh(db_file)
                                    # 2. update a document
                                    db_document = db.query(Document).filter(Document.kb_id == db_knowledge.id,
                                                                            Document.file_id == db_file.id).first()
                                    if db_document:
                                        db_document.file_name = db_file.file_name
                                        db_document.file_ext = db_file.file_ext
                                        db_document.file_size = db_file.file_size
                                        db_document.created_at = db_file.created_at
                                        db_document.updated_at = datetime.now()
                                        db.commit()
                                        db.refresh(db_document)
                                        # 3. Document parsing, vectorization, and storage
                                        parse_document(file_path=save_path, document_id=db_document.id)
                            else:  # --add
                                # 1. update file
                                # Construct a save path：/files/{kb_id}/{parent_id}/{file.id}{file_extension}
                                save_dir = os.path.join(settings.FILE_PATH, str(db_knowledge.id),
                                                        str(db_knowledge.id))
                                Path(save_dir).mkdir(parents=True, exist_ok=True)  # Ensure that the directory exists

                                # download document from Feishu FileInfo
                                async def async_download_document(api_client: YuqueAPIClient, doc: YuqueDocInfo,
                                                                  save_dir: str):
                                    async with api_client as client:
                                        file_path = await client.download_document(doc, save_dir)
                                        return file_path

                                file_path = asyncio.run(async_download_document(api_client, doc, save_dir))
                                # add db_file
                                file_name = os.path.basename(file_path)
                                _, file_extension = os.path.splitext(file_name)
                                file_size = os.path.getsize(file_path)
                                upload_file = file_schema.FileCreate(
                                    kb_id=db_knowledge.id,
                                    created_by=db_knowledge.created_by,
                                    parent_id=db_knowledge.id,
                                    file_name=file_name,
                                    file_ext=file_extension.lower(),
                                    file_size=file_size,
                                    file_url=doc.slug,
                                    created_at=doc.updated_at
                                )
                                db_file = File(**upload_file.model_dump())
                                db.add(db_file)
                                db.commit()
                                # Save file
                                save_path = os.path.join(save_dir, f"{db_file.id}{db_file.file_ext}")
                                # update file
                                if os.path.exists(save_path):
                                    os.remove(save_path)  # Delete a single file
                                shutil.copyfile(file_path, save_path)
                                # 2. Create a document
                                create_document_data = document_schema.DocumentCreate(
                                    kb_id=db_knowledge.id,
                                    created_by=db_knowledge.created_by,
                                    file_id=db_file.id,
                                    file_name=db_file.file_name,
                                    file_ext=db_file.file_ext,
                                    file_size=db_file.file_size,
                                    file_meta={},
                                    parser_id="naive",
                                    parser_config={
                                        "layout_recognize": "DeepDOC",
                                        "chunk_token_num": 130,
                                        "delimiter": "\n",
                                        "auto_keywords": 0,
                                        "auto_questions": 0,
                                        "html4excel": "false"
                                    }
                                )
                                db_document = Document(**create_document_data.model_dump())
                                db.add(db_document)
                                db.commit()
                                # 3. Document parsing, vectorization, and storage
                                parse_document(file_path=save_path, document_id=db_document.id)
                        db_files = db.query(File).filter(File.kb_id == db_knowledge.id,
                                                         File.file_url.notin_(file_urls)).all()
                        if db_files:  # --delete
                            for db_file in db_files:
                                db_document = db.query(Document).filter(Document.kb_id == db_knowledge.id,
                                                                        Document.file_id == db_file.id).first()
                                if db_document:
                                    # 1. Delete vector index
                                    vector_service.delete_by_metadata_field(key="document_id",
                                                                            value=str(db_document.id))
                                    # 2. Delete document
                                    db.delete(db_document)
                                # 3. Delete file
                                file_path = Path(
                                    settings.FILE_PATH,
                                    str(db_file.kb_id),
                                    str(db_file.parent_id),
                                    f"{db_file.id}{db_file.file_ext}"
                                )
                                if file_path.exists():
                                    file_path.unlink()  # Delete a single file
                                db.delete(db_file)
                            # commit transaction
                            db.commit()

                    except Exception as e:
                        logger.error(f"[SyncKB] Error during fetch yuque: {e}", exc_info=True)
                if feishu_app_id:  # Feishu Knowledge Base
                    feishu_app_secret = db_knowledge.parser_config.get("feishu_app_secret", "")
                    feishu_folder_token = db_knowledge.parser_config.get("feishu_folder_token", "")
                    # Create feishuAPIClient
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
                            db_file = db.query(File).filter(File.kb_id == db_knowledge.id,
                                                            File.file_url == doc.url).first()
                            if db_file:
                                if db_file.created_at == doc.modified_time:  # same
                                    continue
                                else:  # --update
                                    # 1. update file
                                    # Construct a save path：/files/{kb_id}/{parent_id}/{file.id}{file_extension}
                                    save_dir = os.path.join(settings.FILE_PATH, str(db_knowledge.id),
                                                            str(db_knowledge.id))
                                    Path(save_dir).mkdir(parents=True,
                                                         exist_ok=True)  # Ensure that the directory exists

                                    # download document from Feishu FileInfo
                                    async def async_download_document(api_client: FeishuAPIClient, doc: FileInfo,
                                                                      save_dir: str):
                                        async with api_client as client:
                                            file_path = await client.download_document(document=doc, save_dir=save_dir)
                                            return file_path

                                    file_path = asyncio.run(async_download_document(api_client, doc, save_dir))

                                    save_path = os.path.join(save_dir, f"{db_file.id}{db_file.file_ext}")
                                    # update file
                                    if os.path.exists(save_path):
                                        os.remove(save_path)  # Delete a single file
                                    shutil.copyfile(file_path, save_path)
                                    # update db_file
                                    file_name = os.path.basename(file_path)
                                    _, file_extension = os.path.splitext(file_name)
                                    file_size = os.path.getsize(file_path)
                                    db_file.file_name = file_name
                                    db_file.file_ext = file_extension.lower()
                                    db_file.file_size = file_size
                                    db_file.created_at = doc.modified_time
                                    db.commit()
                                    db.refresh(db_file)
                                    # 2. update a document
                                    db_document = db.query(Document).filter(Document.kb_id == db_knowledge.id,
                                                                            Document.file_id == db_file.id).first()
                                    if db_document:
                                        db_document.file_name = db_file.file_name
                                        db_document.file_ext = db_file.file_ext
                                        db_document.file_size = db_file.file_size
                                        db_document.created_at = db_file.created_at
                                        db_document.updated_at = datetime.now()
                                        db.commit()
                                        db.refresh(db_document)
                                        # 3. Document parsing, vectorization, and storage
                                        parse_document(file_path=save_path, document_id=db_document.id)
                            else:  # --add
                                # 1. update file
                                # Construct a save path：/files/{kb_id}/{parent_id}/{file.id}{file_extension}
                                save_dir = os.path.join(settings.FILE_PATH, str(db_knowledge.id),
                                                        str(db_knowledge.id))
                                Path(save_dir).mkdir(parents=True, exist_ok=True)  # Ensure that the directory exists

                                # download document from Feishu FileInfo
                                async def async_download_document(api_client: FeishuAPIClient, doc: FileInfo,
                                                                  save_dir: str):
                                    async with api_client as client:
                                        file_path = await client.download_document(document=doc, save_dir=save_dir)
                                        return file_path

                                file_path = asyncio.run(async_download_document(api_client, doc, save_dir))
                                # add db_file
                                file_name = os.path.basename(file_path)
                                _, file_extension = os.path.splitext(file_name)
                                file_size = os.path.getsize(file_path)
                                upload_file = file_schema.FileCreate(
                                    kb_id=db_knowledge.id,
                                    created_by=db_knowledge.created_by,
                                    parent_id=db_knowledge.id,
                                    file_name=file_name,
                                    file_ext=file_extension.lower(),
                                    file_size=file_size,
                                    file_url=doc.url,
                                    created_at=doc.modified_time
                                )
                                db_file = File(**upload_file.model_dump())
                                db.add(db_file)
                                db.commit()
                                # Save file
                                save_path = os.path.join(save_dir, f"{db_file.id}{db_file.file_ext}")
                                # update file
                                if os.path.exists(save_path):
                                    os.remove(save_path)  # Delete a single file
                                shutil.copyfile(file_path, save_path)
                                # 2. Create a document
                                create_document_data = document_schema.DocumentCreate(
                                    kb_id=db_knowledge.id,
                                    created_by=db_knowledge.created_by,
                                    file_id=db_file.id,
                                    file_name=db_file.file_name,
                                    file_ext=db_file.file_ext,
                                    file_size=db_file.file_size,
                                    file_meta={},
                                    parser_id="naive",
                                    parser_config={
                                        "layout_recognize": "DeepDOC",
                                        "chunk_token_num": 130,
                                        "delimiter": "\n",
                                        "auto_keywords": 0,
                                        "auto_questions": 0,
                                        "html4excel": "false"
                                    }
                                )
                                db_document = Document(**create_document_data.model_dump())
                                db.add(db_document)
                                db.commit()
                                # 3. Document parsing, vectorization, and storage
                                parse_document(file_path=save_path, document_id=db_document.id)
                        db_files = db.query(File).filter(File.kb_id == db_knowledge.id,
                                                         File.file_url.notin_(file_urls)).all()
                        if db_files:  # --delete
                            for db_file in db_files:
                                db_document = db.query(Document).filter(Document.kb_id == db_knowledge.id,
                                                                        Document.file_id == db_file.id).first()
                                if db_document:
                                    # 1. Delete vector index
                                    vector_service.delete_by_metadata_field(key="document_id",
                                                                            value=str(db_document.id))
                                    # 2. Delete document
                                    db.delete(db_document)
                                # 3. Delete file
                                file_path = Path(
                                    settings.FILE_PATH,
                                    str(db_file.kb_id),
                                    str(db_file.parent_id),
                                    f"{db_file.id}{db_file.file_ext}"
                                )
                                if file_path.exists():
                                    file_path.unlink()  # Delete a single file
                                db.delete(db_file)
                            # commit transaction
                            db.commit()

                    except Exception as e:
                        logger.error(f"[SyncKB] Error during fetch feishu: {e}", exc_info=True)
            case _:  # General
                logger.info(f"[SyncKB] kb={kb_id} type={db_knowledge.type}: no synchronization needed")

        result = f"sync knowledge '{db_knowledge.name}' processed successfully."
        return result
      except Exception as e:
        logger.error(f"[SyncKB] kb={kb_id} failed: {e}", exc_info=True)
        kb_name = db_knowledge.name if db_knowledge else kb_id
        return f"sync knowledge '{kb_name}' failed: {e}"


@celery_app.task(name="app.core.memory.agent.read_message", bind=True)
def read_message_task(self, end_user_id: str, message: str, history: List[Dict[str, Any]], search_switch: str,
                      config_id: str, storage_type: str, user_rag_memory_id: str) -> Dict[str, Any]:
    """Celery task to process a read message via MemoryAgentService.

    Args:
        end_user_id: Group ID for the memory agent (also used as end_user_id)
        message: User message to process
        history: Conversation history
        search_switch: Search switch parameter
        config_id: Configuration ID as string (will be converted to UUID)

    Returns:
        Dict containing the result and metadata

    Raises:
        Exception on failure
    """
    start_time = time.time()

    # Convert config_id string to UUID
    actual_config_id = None
    if config_id:
        try:
            with get_db_context() as db:
                actual_config_id = resolve_config_id(config_id, db)
        except (ValueError, AttributeError):
            # If conversion fails, leave as None and try to resolve
            pass

    # Resolve config_id if None
    if actual_config_id is None:
        try:
            from app.services.memory_agent_service import get_end_user_connected_config
            with get_db_context() as db:
                connected_config = get_end_user_connected_config(end_user_id, db)
                actual_config_id = connected_config.get("memory_config_id")
        except Exception:
            # Log but continue - will fail later with proper error
            pass

    async def _run() -> dict:
        with get_db_context() as db:
            service = MemoryAgentService()
            return await service.read_memory(
                end_user_id,
                message,
                history,
                search_switch,
                actual_config_id, db,
                storage_type, user_rag_memory_id
            )

    try:
        # 尝试获取现有事件循环，如果不存在则创建新的
        loop = set_asyncio_event_loop()

        result = loop.run_until_complete(_run())
        elapsed_time = time.time() - start_time

        return {
            "status": "SUCCESS",
            "result": result,
            "end_user_id": end_user_id,
            "config_id": config_id,
            "elapsed_time": elapsed_time,
            "task_id": self.request.id
        }
    except BaseException as e:
        elapsed_time = time.time() - start_time
        # Handle ExceptionGroup from TaskGroup
        if hasattr(e, 'exceptions'):
            error_messages = [f"{type(sub_e).__name__}: {str(sub_e)}" for sub_e in e.exceptions]
            detailed_error = "; ".join(error_messages)
        else:
            detailed_error = str(e)
        return {
            "status": "FAILURE",
            "error": detailed_error,
            "end_user_id": end_user_id,
            "config_id": config_id,
            "elapsed_time": elapsed_time,
            "task_id": self.request.id
        }


@celery_app.task(name="app.core.memory.agent.write_message", bind=True, acks_late=False)
def write_message_task(
        self,
        end_user_id: str,
        message: list[dict],
        config_id: str | int,
        storage_type: str,
        user_rag_memory_id: str,
        language: str = "zh",
        conversation_id: str = "",
        workspace_id: str = "",
) -> Dict[str, Any]:
    """Celery task to process a write message via MemoryAgentService.
    Args:
        end_user_id: Group ID for the memory agent (also used as end_user_id)
        message: Message to write
        config_id: Configuration ID (can be UUID string, integer, or config_id_old)
        storage_type: Storage type (neo4j or rag)
        user_rag_memory_id: User RAG memory ID
        language: 语言类型 ("zh" 中文, "en" 英文)
        conversation_id: 对话 ID（用于候选池消费模式）
        workspace_id: 工作空间 ID（候选池消费模式加载 memory_config 时使用）

    Returns:
        Dict containing the result and metadata

    Raises:
        Exception on failure
    """
    logger.info(
        f"[CELERY WRITE] Starting write task - end_user_id={end_user_id}, "
        f"config_id={config_id} (type: {type(config_id).__name__}), "
        f"storage_type={storage_type}, language={language}, "
        f"conversation_id={conversation_id or '-'}, "
        f"workspace_id={workspace_id or '-'}")
    start_time = time.time()

    # Convert config_id to UUID
    actual_config_id = None

    if config_id:
        try:
            with get_db_context() as db:
                actual_config_id = resolve_config_id(config_id, db)
            logger.info(f"[CELERY WRITE] Converted config_id to UUID: {actual_config_id} "
                        f"(type: {type(actual_config_id).__name__})")
        except (ValueError, AttributeError) as e:
            logger.error(f"[CELERY WRITE] Invalid config_id format: {config_id} "
                         f"(type: {type(config_id).__name__}), error: {e}")
            return {
                "status": "FAILURE",
                "error": f"Invalid config_id format: {config_id} - {str(e)}",
                "end_user_id": end_user_id,
                "config_id": str(config_id),
                "elapsed_time": 0.0,
                "task_id": self.request.id
            }

    # Resolve config_id if None
    if actual_config_id is None:
        try:
            from app.services.memory_agent_service import get_end_user_connected_config
            with get_db_context() as db:
                connected_config = get_end_user_connected_config(end_user_id, db)
                actual_config_id = connected_config.get("memory_config_id")
        except Exception:
            # Log but continue - will fail later with proper error
            pass

    async def _run() -> str | dict:
        """两种模式：
        - 候选池消费模式：message 为空且 conversation_id 非空 → 直接执行 Layer 2
        - 完整写入模式：走 MemoryAgentService.write_memory（API write 路径专用）
        """
        # 候选池消费模式（Agent 对话 / 工作流 MemoryWriteNode 路径）
        if (not message) and conversation_id:
            from app.core.memory.sliding_window.window_utils import execute_pending_from_pool

            logger.info(
                f"[CELERY WRITE] 候选池消费模式: "
                f"conv={conversation_id}, end_user_id={end_user_id}, "
                f"workspace_id={workspace_id}"
            )
            processed = await execute_pending_from_pool(
                conversation_id=conversation_id,
                end_user_id=end_user_id,
                config_id=str(actual_config_id) if actual_config_id else "",
                workspace_id=workspace_id,
                language=language,
            )
            return {"status": "success", "processed": processed}

        # 完整写入模式（API write 路径，带 messages）
        with get_db_context() as db:
            logger.info(
                f"[CELERY WRITE] Executing MemoryAgentService.write_memory "
                f"with config_id = {actual_config_id} (type: {type(actual_config_id).__name__}), language={language}")

            from datetime import datetime, timezone
            _default_dialog_at = datetime.now(timezone.utc).isoformat()
            for msg in message:
                if isinstance(msg, dict) and not msg.get("dialog_at"):
                    msg["dialog_at"] = _default_dialog_at

            service = MemoryAgentService()
            result = await service.write_memory(
                WriteMemoryRequest(
                    end_user_id=end_user_id,
                    messages=message,
                    config_id=actual_config_id,
                    storage_type=storage_type,
                    user_rag_memory_id=user_rag_memory_id,
                    language=language,
                    conversation_id=conversation_id,
                ),
                db,
            )
            logger.info(f"[CELERY WRITE] Write completed successfully: {result}")
            return result

    redis_client = get_sync_redis_client()
    lock = None
    loop = None
    lock_token = None
    if redis_client is not None:
        lock = RedisFairLock(
            key=f"memory_write:{end_user_id}",
            redis_client=redis_client,
            expire=600,
            timeout=3600,
            auto_renewal=True,
        )
        if not lock.acquire():
            logger.warning(f"[CELERY WRITE] 获取锁超时，跳过本次写入: end_user_id={end_user_id}")
            return {
                "status": "SKIPPED",
                "error": "acquire lock timeout",
                "end_user_id": end_user_id,
                "config_id": str(config_id),
                "elapsed_time": time.time() - start_time,
                "task_id": self.request.id,
            }

        # 标记当前上下文已持有锁，防止下游 MemoryAgentService.write_memory 重复加锁
        from app.services.memory_agent_service import _set_write_lock_holder
        lock_token = _set_write_lock_holder(end_user_id)

    try:
        task_start_time = int(time.time())
        loop = set_asyncio_event_loop()

        result = loop.run_until_complete(_run())
        elapsed_time = time.time() - start_time

        logger.info(f"[CELERY WRITE] Task completed successfully "
                    f"- elapsed_time={elapsed_time:.2f}s, task_id={self.request.id}")

        try:
            _r = redis_client
            if _r is not None:
                from datetime import timezone as _tz
                _now_utc = datetime.now(_tz.utc).isoformat()
                _r.set(
                    f"write_message:last_done:{end_user_id}",
                    _now_utc,
                    ex=86400 * 30,
                )
        except Exception as _e:
            logger.warning(f"[CELERY WRITE] 写入 last_done 时间戳失败（不影响主流程）: {_e}")
        # 将 result 转为 JSON 安全结构，避免 Celery JSON 序列化 pydantic BaseModel / UUID 失败
        try:
            safe_result = jsonable_encoder(result)
        except Exception as _enc_e:
            logger.warning(f"[CELERY WRITE] jsonable_encoder 失败，回退为字符串: {_enc_e}")
            safe_result = str(result)
        return {
            "status": "SUCCESS",
            "result": safe_result,
            "start_at": task_start_time,
            "end_user_id": end_user_id,
            "config_id": str(config_id) if config_id is not None else None,
            "elapsed_time": elapsed_time,
            "task_id": self.request.id
        }
    except BaseException as e:
        elapsed_time = time.time() - start_time
        # Handle ExceptionGroup from TaskGroup
        if hasattr(e, 'exceptions'):
            error_messages = [f"{type(sub_e).__name__}: {str(sub_e)}" for sub_e in e.exceptions]
            detailed_error = "; ".join(error_messages)
        else:
            detailed_error = str(e)

        logger.error(f"[CELERY WRITE] Task failed - elapsed_time={elapsed_time:.2f}s, error={detailed_error}",
                     exc_info=True)

        return {
            "status": "FAILURE",
            "error": detailed_error,
            "end_user_id": end_user_id,
            "config_id": config_id,
            "elapsed_time": elapsed_time,
            "task_id": self.request.id
        }
    finally:
        if lock_token is not None:
            try:
                from app.services.memory_agent_service import _reset_write_lock_holder
                _reset_write_lock_holder(lock_token)
            except Exception as e:
                logger.warning(f"[CELERY WRITE] 重置锁标记失败: {e}")
        if lock is not None:
            try:
                lock.release()
            except Exception as e:
                logger.warning(f"[CELERY WRITE] 释放锁失败: {e}")
        # Gracefully shutdown the event loop to prevent
        # 'RuntimeError: Event loop is closed' from httpx.AsyncClient.__del__
        if loop:
            _shutdown_loop_gracefully(loop)


@celery_app.task(
    bind=True,
    name="app.tasks.extract_emotion_batch",
    max_retries=2,
    default_retry_delay=30,
)
def extract_emotion_batch_task(
    self,
    statements: List[Dict[str, str]],
    llm_model_id: str,
    language: str = "zh",
    emotion_config: Optional[Dict[str, Any]] = None,
    snapshot_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Celery task: batch emotion extraction + Neo4j backfill.

    Runs asynchronously after the main write pipeline completes.
    Each statement is processed independently; individual failures
    degrade gracefully without affecting other statements.

    Args:
        statements: List of dicts with keys: statement_id, statement_text, speaker.
        llm_model_id: UUID string of the LLM model to use.
        language: Language code ("zh" / "en").
        emotion_config: Optional dict with emotion step config overrides
                        (emotion_extract_keywords, emotion_enable_subject).
        snapshot_dir: Optional absolute path of the current run's snapshot directory.
                      When provided (only in debug mode), emotion outputs will be
                      dumped to <snapshot_dir>/4_emotion_outputs.json for offline
                      comparison between the legacy / new pipelines.
    """
    task_id = self.request.id
    total = len(statements)
    logger.info(
        f"[Emotion] 开始批量情绪提取: "
        f"statements={total}, llm_model_id={llm_model_id}, "
        f"language={language}, task_id={task_id}"
    )
    start_time = time.time()

    if not statements:
        return {"status": "SUCCESS", "total": 0, "extracted": 0, "failed": 0, "task_id": task_id}

    async def _run() -> Dict[str, Any]:
        from app.core.memory.models.variate_config import ExtractionPipelineConfig
        from app.core.memory.storage_services.extraction_engine.steps.base import StepContext
        from app.core.memory.storage_services.extraction_engine.steps.emotion_step import EmotionExtractionStep
        from app.core.memory.storage_services.extraction_engine.steps.schema import (
            EmotionStepInput,
            EmotionStepOutput,
        )
        from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
        from app.db import get_db_context
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.repositories.neo4j.cypher_queries import STATEMENT_EMOTION_UPDATE

        # Build LLM client
        with get_db_context() as db:
            factory = MemoryClientFactory(db)
            llm_client = factory.get_llm_client(llm_model_id)

        # Build minimal pipeline config with emotion enabled
        pipeline_config = ExtractionPipelineConfig(emotion_enabled=True)
        # Apply optional config overrides
        emo_cfg = emotion_config or {}
        for key in ("emotion_extract_keywords", "emotion_enable_subject"):
            if key in emo_cfg:
                setattr(pipeline_config, key, emo_cfg[key])

        context = StepContext(
            llm_client=llm_client,
            language=language,
            config=pipeline_config,
        )
        step = EmotionExtractionStep(context)

        # Concurrent extraction for all statements
        extracted = 0
        failed = 0
        update_items = []
        # 快照用：收集每条 statement 的 EmotionStepOutput（仅当 snapshot_dir 非空时使用）
        snapshot_outputs: Dict[str, Any] = {} if snapshot_dir else None  # type: ignore[assignment]

        async def _extract_one(stmt_dict: Dict[str, str]):
            nonlocal extracted, failed
            inp = EmotionStepInput(
                statement_id=stmt_dict["statement_id"],
                statement_text=stmt_dict["statement_text"],
                speaker=stmt_dict.get("speaker", "user"),
            )
            try:
                result: EmotionStepOutput = await step.run(inp)
                update_items.append({
                    "statement_id": stmt_dict["statement_id"],
                    "emotion_type": result.emotion_type,
                    "emotion_intensity": result.emotion_intensity,
                    "emotion_keywords": result.emotion_keywords,
                })
                if snapshot_outputs is not None:
                    snapshot_outputs[stmt_dict["statement_id"]] = result.model_dump()
                extracted += 1
                logger.debug(
                    f"[Emotion] 单条提取完成: stmt={stmt_dict['statement_id']}, "
                    f"type={result.emotion_type}, intensity={result.emotion_intensity}"
                )
            except Exception as e:
                failed += 1
                if snapshot_outputs is not None:
                    snapshot_outputs[stmt_dict["statement_id"]] = {"error": str(e)}
                logger.warning(
                    f"[Emotion] 单条提取失败 stmt={stmt_dict['statement_id']}: {e}"
                )

        await asyncio.gather(*[_extract_one(s) for s in statements])

        # 快照落盘（worker 端）：不影响 Neo4j 写入流程，失败只打日志
        if snapshot_outputs is not None:
            try:
                from pathlib import Path as _Path
                import json as _json

                _dir = _Path(snapshot_dir)
                _dir.mkdir(parents=True, exist_ok=True)
                _path = _dir / "4_emotion_outputs.json"
                with open(_path, "w", encoding="utf-8") as _f:
                    _json.dump(snapshot_outputs, _f, ensure_ascii=False, indent=2, default=str)
                logger.info(
                    f"[Emotion][Snapshot] 已落盘 {len(snapshot_outputs)} 条情绪结果 → {_path}"
                )
            except Exception as _e:
                logger.warning(
                    f"[Emotion][Snapshot] 快照落盘失败（不影响主流程）: {_e}"
                )

        # Batch update Neo4j via write transaction
        if update_items:
            connector = Neo4jConnector()
            try:
                async def _write_emotions(tx):
                    result = await tx.run(STATEMENT_EMOTION_UPDATE, items=update_items)
                    records = [record async for record in result]
                    return records

                records = await connector.execute_write_transaction(_write_emotions)
                logger.info(
                    f"[Emotion] Neo4j 回写完成: "
                    f"更新 {len(records)}/{len(update_items)} 条 Statement 节点"
                )
            except Exception as e:
                logger.error(f"[Emotion] Neo4j 回写失败: {e}")
                raise
            finally:
                await connector.close()

        return {"extracted": extracted, "failed": failed}

    loop = None
    try:
        loop = set_asyncio_event_loop()
        result = loop.run_until_complete(_run())
        elapsed = time.time() - start_time
        logger.info(
            f"[Emotion] 任务完成: 提取={result['extracted']}, "
            f"失败={result['failed']}, 耗时={elapsed:.2f}s, task_id={task_id}"
        )
        return {
            "status": "SUCCESS",
            "total": total,
            **result,
            "elapsed_time": elapsed,
            "task_id": task_id,
        }
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(
            f"[Emotion] 任务失败: {e}, 耗时={elapsed:.2f}s",
            exc_info=True,
        )
        raise self.retry(exc=e)
    finally:
        if loop:
            _shutdown_loop_gracefully(loop)


@celery_app.task(
    bind=True,
    name="app.tasks.post_store_dedup_and_alias_merge",
    max_retries=1,
    default_retry_delay=30,
)
def post_store_dedup_and_alias_merge_task(
    self,
    end_user_id: str,
    entity_ids: List[str],
    llm_model_id: Optional[str] = None,
    snapshot_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Celery task: 写入后异步执行 Neo4j 别名归并 + 第二层去重。

    在主写入流水线将第一层去重结果写入 Neo4j 之后执行：
    1. Neo4j 别名归并：将 "别名属于" 边的 source.name 合并到 target.aliases
    2. Neo4j 边重定向：将指向别名节点的边重定向到目标节点
    3. 第二层去重：与 Neo4j 中已有的同组实体做联合去重

    Args:
        end_user_id: 终端用户 ID
        entity_ids: 本轮写入的实体 ID 列表（用于第二层去重的候选检索）
        llm_model_id: LLM 模型 UUID（用于第二层去重的 LLM 兜底判定）
    """
    task_id = self.request.id
    logger.info(
        f"[PostStore] 开始异步别名归并+第二层去重: "
        f"end_user_id={end_user_id}, entity_count={len(entity_ids)}, "
        f"task_id={task_id}"
    )
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.repositories.neo4j.cypher_queries import (
            MERGE_ALIAS_BELONGS_TO,
            REDIRECT_ALIAS_EDGES,
            DELETE_ALIAS_NODES,
        )

        connector = Neo4jConnector()
        result_info: Dict[str, Any] = {}

        try:
            # ── 1. Neo4j 别名归并（追加新别名） ──
            try:
                records = await connector.execute_query(
                    MERGE_ALIAS_BELONGS_TO,
                    end_user_id=end_user_id,
                )
                merged_count = len(records) if records else 0
                result_info["alias_merged"] = merged_count
                logger.info(f"[PostStore] Neo4j 别名归并完成，影响 {merged_count} 条记录")
            except Exception as e:
                logger.warning(f"[PostStore] Neo4j 别名归并失败: {e}")
                result_info["alias_merge_error"] = str(e)

            # ── 2. Neo4j 边重定向 ──
            try:
                redirect_records = await connector.execute_query(
                    REDIRECT_ALIAS_EDGES,
                    end_user_id=end_user_id,
                )
                redirect_count = len(redirect_records) if redirect_records else 0
                result_info["edges_redirected"] = redirect_count
                logger.info(f"[PostStore] Neo4j 边重定向完成，影响 {redirect_count} 条记录")
            except Exception as e:
                logger.warning(f"[PostStore] Neo4j 边重定向失败: {e}")
                result_info["redirect_error"] = str(e)

            # ── 3. 删除别名节点及"别名属于"关系 ──
            try:
                delete_records = await connector.execute_query(
                    DELETE_ALIAS_NODES,
                    end_user_id=end_user_id,
                )
                deleted_count = delete_records[0].get("deleted_count", 0) if delete_records else 0
                result_info["alias_nodes_deleted"] = deleted_count
                logger.info(f"[PostStore] 别名节点删除完成，删除 {deleted_count} 个节点")
            except Exception as e:
                logger.warning(f"[PostStore] 别名节点删除失败: {e}")
                result_info["alias_delete_error"] = str(e)

            # ── 3.5 Snapshot: 别名归并+删除后的实体状态 ──
            if snapshot_dir:
                try:
                    snapshot_query = """
                    UNWIND $entity_ids AS eid
                    MATCH (e:ExtractedEntity {id: eid})
                    RETURN e.id AS id, e.name AS name,
                           e.entity_type AS entity_type,
                           e.description AS description,
                           coalesce(e.aliases, []) AS aliases
                    """
                    snap_records = await connector.execute_query(
                        snapshot_query, entity_ids=entity_ids
                    )
                    entity_rows = [dict(r) for r in snap_records] if snap_records else []
                    from app.core.memory.utils.debug.write_snapshot_recorder import WriteSnapshotRecorder
                    WriteSnapshotRecorder.save_alias_merge_result(snapshot_dir, entity_rows)
                    logger.info(f"[PostStore] Snapshot 8_after_alias_merge 已写入，实体数={len(entity_rows)}")
                except Exception as e:
                    logger.warning(f"[PostStore] Snapshot 写入失败（不影响主流程）: {e}")

            # ── 4. 第二层去重（与 Neo4j 已有实体联合去重） ──
            try:
                from app.core.memory.storage_services.extraction_engine.deduplication.second_layer_dedup import (
                    second_layer_dedup_and_merge_with_neo4j,
                )
                from app.core.memory.storage_services.extraction_engine.deduplication.deduped_and_disamb import (
                    clean_cross_role_aliases,
                )
                from app.repositories.neo4j.cypher_queries import EXTRACTED_ENTITY_NODE_SAVE

                # 从 Neo4j 加载本轮写入的实体（第一层去重后的结果）
                load_query = """
                UNWIND $entity_ids AS eid
                MATCH (e:ExtractedEntity {id: eid})
                RETURN e {.*} AS entity
                """
                entity_records = await connector.execute_query(
                    load_query, entity_ids=entity_ids
                )

                if entity_records:
                    from app.core.memory.storage_services.extraction_engine.deduplication.second_layer_dedup import (
                        _row_to_entity,
                    )

                    current_entities = []
                    for rec in entity_records:
                        try:
                            entity_data = rec.get("entity") or rec
                            current_entities.append(_row_to_entity(entity_data))
                        except Exception:
                            pass

                    if current_entities:
                        # 构建 LLM client（如果有 llm_model_id）
                        llm_client = None
                        if llm_model_id:
                            try:
                                from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
                                from app.db import get_db_context
                                with get_db_context() as db:
                                    factory = MemoryClientFactory(db)
                                    llm_client = factory.get_llm_client(llm_model_id)
                            except Exception as e:
                                logger.warning(f"[PostStore] 构建 LLM client 失败，跳过 LLM 兜底: {e}")

                        fused_entities, _, _ = await second_layer_dedup_and_merge_with_neo4j(
                            connector=connector,
                            end_user_id=end_user_id,
                            entity_nodes=current_entities,
                            statement_entity_edges=[],
                            entity_entity_edges=[],
                            llm_client=llm_client,
                        )

                        # 清洗跨角色别名污染
                        clean_cross_role_aliases(fused_entities)

                        # 将融合后的实体回写 Neo4j
                        if fused_entities:
                            entity_data = [e.model_dump() for e in fused_entities]
                            await connector.execute_query(
                                EXTRACTED_ENTITY_NODE_SAVE, entities=entity_data
                            )

                        result_info["layer2_input"] = len(current_entities)
                        result_info["layer2_output"] = len(fused_entities)
                        logger.info(
                            f"[PostStore] 第二层去重完成: "
                            f"{len(current_entities)} → {len(fused_entities)} 个实体"
                        )
                    else:
                        result_info["layer2_skipped"] = "no entities loaded"
                else:
                    result_info["layer2_skipped"] = "no entity records found"

            except Exception as e:
                logger.warning(f"[PostStore] 第二层去重失败（不影响主流程）: {e}", exc_info=True)
                result_info["layer2_error"] = str(e)

        finally:
            await connector.close()

        # ── 第二层去重可能合并/删除节点，重新同步 memory_count ──
        _count_connector = Neo4jConnector()
        try:
            await sync_end_user_memory_count_from_neo4j(end_user_id, _count_connector)
        except Exception as _sync_e:
            logger.warning(
                f"[MEMORY_COUNT_SYNC] 同步失败（不影响主流程）: end_user_id={end_user_id}, error={_sync_e}"
            )
        finally:
            await _count_connector.close()

        return result_info

    loop = None
    try:
        loop = set_asyncio_event_loop()
        result = loop.run_until_complete(_run())
        elapsed = time.time() - start_time
        logger.info(
            f"[PostStore] 任务完成: {result}, 耗时={elapsed:.2f}s, task_id={task_id}"
        )

        return {
            "status": "SUCCESS",
            **result,
            "elapsed_time": elapsed,
            "task_id": task_id,
        }
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(
            f"[PostStore] 任务失败: {e}, 耗时={elapsed:.2f}s",
            exc_info=True,
        )
        raise self.retry(exc=e)
    finally:
        if loop:
            _shutdown_loop_gracefully(loop)

@celery_app.task(
    name="app.tasks.layer2_reflection_task",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=600,
    soft_time_limit=540,
)
def layer2_reflection_task(self) -> Dict[str, Any]:
    """Layer 2 离线巡检（描述合并等）— 每 10 分钟

    遍历所有 workspace → app → end_user，对每个启用了 enable_self_reflexion 的配置
    调用 MemoryService.run_reflection_layer2()。
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.models.workspace_model import Workspace
        from app.services.memory_reflection_service import WorkspaceAppService
        from app.core.memory.memory_service import MemoryService

        with get_db_context() as db:
            try:
                workspaces = db.query(Workspace).all()
                if not workspaces:
                    return {"status": "SUCCESS", "message": "无工作空间"}

                logger.info(f"反思引擎Layer2 巡检开始，共 {len(workspaces)} 个工作空间")
                processed_users = 0
                skipped_configs = 0
                total_dedup_merged = 0
                total_desc_merged = 0

                for workspace in workspaces:
                    service = WorkspaceAppService(db)
                    result = service.get_workspace_apps_detailed(str(workspace.id))

                    for data in result['apps_detailed_info']:
                        if not data['memory_configs']:
                            continue

                        for config in data['memory_configs']:
                            if not config.get('enable_self_reflexion'):
                                skipped_configs += 1
                                continue

                            config_id = config['config_id']
                            baseline = config.get('baseline', 'HYBRID')
                            end_users = data['end_users']

                            for user in end_users:
                                try:
                                    memory_service = MemoryService(
                                        db=db,
                                        config_id=config_id,
                                        end_user_id=str(user['id']),
                                        workspace_id=str(workspace.id),
                                    )
                                    r = await memory_service.run_reflection_layer2(
                                        baseline=baseline,
                                    )
                                    processed_users += 1
                                    # 增量统计
                                    dedup_info = r.get("entity_dedup", {})
                                    merge_info = r.get("description_merge", {})
                                    total_dedup_merged += dedup_info.get("merged_count", 0)
                                    total_desc_merged += merge_info.get("merged_count", 0)
                                    # 只在有实际合并时输出详细日志
                                    if merge_info.get("merged_count", 0) > 0:
                                        logger.info(
                                            f"反思引擎Layer2 用户 {user['id']} 描述合并: "
                                            f"候选 {merge_info['candidate_count']}, "
                                            f"合并 {merge_info['merged_count']}"
                                        )
                                    if dedup_info.get("merged_count", 0) > 0:
                                        logger.info(
                                            f"反思引擎Layer2 用户 {user['id']} 去重合并: "
                                            f"合并 {dedup_info['merged_count']}"
                                        )
                                except Exception as e:
                                    logger.error(f"反思引擎Layer2 巡检失败 user={user['id']}: {e}")

                logger.info(
                    f"反思引擎Layer2 巡检遍历完成: 处理 {processed_users} 个用户, "
                    f"跳过 {skipped_configs} 个未启用反思的配置"
                )

                return {
                    "status": "SUCCESS",
                    "processed_users": processed_users,
                    "skipped_configs": skipped_configs,
                    "total_dedup_merged": total_dedup_merged,
                    "total_desc_merged": total_desc_merged,
                }

            except Exception as e:
                logger.error(f"反思引擎Layer2 定时任务失败: {e}", exc_info=True)
                return {"status": "FAILED", "error": str(e)}

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
    except Exception as e:
        result = {"status": "FAILED", "error": str(e)}
    finally:
        _shutdown_loop_gracefully(loop)

    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    logger.info(f"反思引擎Layer2 任务完成，耗时 {result['elapsed_time']:.1f}s")
    return result
    
@celery_app.task(
    name="app.tasks.layer2_dedup_full_scan_task",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=1800,
    soft_time_limit=1740,
)
def layer2_dedup_full_scan_task(self) -> Dict[str, Any]:
    """方案B：低频全量扫描去重（每天一次）

    复用 layer2_reflection_task 的调度模式：
    遍历所有 workspace → app → end_user，检查 enable_self_reflexion 配置。
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.models.workspace_model import Workspace
        from app.services.memory_reflection_service import WorkspaceAppService
        from app.core.memory.memory_service import MemoryService

        with get_db_context() as db:
            workspaces = db.query(Workspace).all()
            if not workspaces:
                return {"status": "SUCCESS", "message": "无工作空间"}

            processed_users = 0
            skipped_configs = 0
            total_merged = 0

            for workspace in workspaces:
                service = WorkspaceAppService(db)
                result = service.get_workspace_apps_detailed(str(workspace.id))

                for data in result['apps_detailed_info']:
                    if not data['memory_configs']:
                        continue

                    for config in data['memory_configs']:
                        # 检查反思引擎是否开启
                        if not config.get('enable_self_reflexion'):
                            skipped_configs += 1
                            continue

                        config_id = config['config_id']
                        end_users = data['end_users']

                        for user in end_users:
                            try:
                                memory_service = MemoryService(
                                    db=db,
                                    config_id=config_id,
                                    end_user_id=str(user['id']),
                                    workspace_id=str(workspace.id),
                                )
                                r = await memory_service.run_dedup_full_scan()
                                processed_users += 1
                                merged = r.get("merged_count", 0)
                                total_merged += merged
                                if merged > 0:
                                    logger.info(
                                        f"方案B全量扫描 用户 {user['id']} "
                                        f"扫描类型 {r.get('scanned_types', 0)}, "
                                        f"合并 {merged} 对"
                                    )
                            except Exception as e:
                                logger.error(f"方案B全量扫描失败 user={user['id']}: {e}")

            logger.info(
                f"方案B全量扫描完成: 处理 {processed_users} 用户, "
                f"跳过 {skipped_configs} 个未启用配置, "
                f"总合并 {total_merged} 对"
            )
            return {
                "status": "SUCCESS",
                "processed_users": processed_users,
                "skipped_configs": skipped_configs,
                "total_merged": total_merged,
            }

    loop = set_asyncio_event_loop()
    try:
        result = loop.run_until_complete(_run())
    except Exception as e:
        result = {"status": "FAILED", "error": str(e)}
    finally:
        _shutdown_loop_gracefully(loop)

    result["elapsed_time"] = time.time() - start_time
    result["task_id"] = self.request.id
    logger.info(f"反思引擎去重消岐全量扫描任务完成，耗时 {result['elapsed_time']:.1f}s")
    return result

def _sync_end_user_info_pg(
    end_user_id: str,
    aliases: List[str],
    extracted_metadata: Optional[Dict[str, Any]],
) -> None:
    """将别名和元数据增量同步到 PostgreSQL end_user_info 表。

    - aliases 合并到 end_user_info.aliases（去重）
    - end_user_info.other_name 若为空则取 aliases[0]
    - end_user.other_name 与 end_user_info.other_name 保持同步
    - extracted_metadata 各字段列表合并到 end_user_info.meta_data（去重）

    失败只记日志，不抛异常，不影响主流程。
    """
    try:
        import uuid as _uuid
        from app.db import get_db_context
        from app.repositories.end_user_info_repository import EndUserInfoRepository
        from app.repositories.end_user_repository import EndUserRepository

        eu_uuid = _uuid.UUID(end_user_id)

        with get_db_context() as db:
            info_repo = EndUserInfoRepository(db)
            info = info_repo.update_aliases_and_metadata(
                end_user_id=eu_uuid,
                new_aliases=aliases or [],
                new_metadata=extracted_metadata,
            )
            if info is None:
                logger.warning(
                    f"[Metadata][PG] end_user_info 记录不存在，跳过同步: end_user_id={end_user_id}"
                )
                return

            # 同步 end_user.other_name（与 end_user_info.other_name 保持一致）
            new_other_name = (info.other_name or "").strip()
            if new_other_name:
                eu_repo = EndUserRepository(db)
                end_user = eu_repo.get_end_user_by_id(eu_uuid)
                if end_user and not (end_user.other_name or "").strip():
                    end_user.other_name = new_other_name
                    db.commit()
                    logger.info(
                        f"[Metadata][PG] 同步 end_user.other_name={new_other_name}: "
                        f"end_user_id={end_user_id}"
                    )

        logger.info(
            f"[Metadata][PG] end_user_info 同步完成: end_user_id={end_user_id}, "
            f"aliases_count={len(aliases or [])}"
        )
    except Exception as e:
        logger.warning(
            f"[Metadata][PG] 同步 end_user_info 失败（不影响主流程）: "
            f"end_user_id={end_user_id}, error={e}",
            exc_info=True,
        )


@celery_app.task(
    bind=True,
    name="app.tasks.extract_metadata_batch",
    max_retries=2,
    default_retry_delay=30,
)
def extract_metadata_batch_task(
    self,
    user_entities: List[Dict[str, Any]],
    llm_model_id: str,
    language: str = "zh",
    snapshot_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Celery task: 用户实体元数据提取 + Neo4j 回写 + PostgreSQL 同步。

    在主写入流水线完成后异步执行。从用户实体的 description 中提取
    结构化元数据（core_facts、traits、relations 等），增量回写到 Neo4j，
    同时将 aliases 和 extracted_metadata 同步到 PostgreSQL end_user_info 表。

    Args:
        user_entities: 用户实体列表，每项包含:
            - entity_id: 实体 ID
            - entity_name: 实体名称
            - descriptions: description 文本列表
            - aliases: 实体别名列表（来自 "别名属于" 关系归并后的结果）
            - end_user_id: 终端用户 ID（用于写入 PostgreSQL）
        llm_model_id: LLM 模型 UUID 字符串
        language: 语言 ("zh" / "en")
        snapshot_dir: 可选的快照目录路径（调试模式下使用）
    """
    task_id = self.request.id
    total = len(user_entities)
    logger.info(
        f"[Metadata] 开始用户元数据提取: "
        f"entities={total}, llm_model_id={llm_model_id}, "
        f"language={language}, task_id={task_id}"
    )
    start_time = time.time()

    if not user_entities:
        return {"status": "SUCCESS", "total": 0, "extracted": 0, "failed": 0, "task_id": task_id}

    async def _run() -> Dict[str, Any]:
        from app.core.memory.models.variate_config import ExtractionPipelineConfig
        from app.core.memory.storage_services.extraction_engine.steps.base import StepContext
        from app.core.memory.storage_services.extraction_engine.steps.metadata_step import MetadataExtractionStep
        from app.core.memory.storage_services.extraction_engine.steps.schema import (
            MetadataStepInput,
        )
        from app.core.memory.utils.llm.llm_utils import MemoryClientFactory
        from app.db import get_db_context
        from app.repositories.neo4j.neo4j_connector import Neo4jConnector
        from app.repositories.neo4j.cypher_queries import ENTITY_METADATA_UPDATE, ENTITY_METADATA_QUERY

        # Build LLM client
        with get_db_context() as db:
            factory = MemoryClientFactory(db)
            llm_client = factory.get_llm_client(llm_model_id)

        pipeline_config = ExtractionPipelineConfig()
        context = StepContext(
            llm_client=llm_client,
            language=language,
            config=pipeline_config,
        )
        step = MetadataExtractionStep(context)

        extracted = 0
        failed = 0
        snapshot_outputs: Dict[str, Any] = {} if snapshot_dir else None  # type: ignore[assignment]

        connector = Neo4jConnector()
        try:
            for entity_dict in user_entities:
                entity_id = entity_dict["entity_id"]
                entity_name = entity_dict.get("entity_name", "")
                descriptions = entity_dict.get("descriptions", [])
                aliases = entity_dict.get("aliases", [])
                end_user_id = entity_dict.get("end_user_id", "")

                if not descriptions:
                    logger.debug(f"[Metadata] 跳过无 description 的实体: {entity_id}")
                    continue

                try:
                    # 查询已有元数据用于增量去重
                    existing_metadata = {}
                    try:
                        records = await connector.execute_query(
                            ENTITY_METADATA_QUERY, entity_id=entity_id
                        )
                        if records:
                            rec = records[0]
                            for field in (
                                "core_facts", "traits", "relations", "goals",
                                "interests", "beliefs_or_stances", "anchors", "events",
                            ):
                                val = rec.get(field)
                                existing_metadata[field] = val if val else []
                    except Exception as e:
                        logger.warning(f"[Metadata] 查询已有元数据失败: {e}")

                    inp = MetadataStepInput(
                        entity_id=entity_id,
                        entity_name=entity_name,
                        descriptions=descriptions,
                        existing_metadata=existing_metadata,
                    )
                    result = await step.run(inp)

                    if result.has_any():
                        # 回写 Neo4j
                        await connector.execute_query(
                            ENTITY_METADATA_UPDATE,
                            entity_id=entity_id,
                            core_facts=result.core_facts,
                            traits=result.traits,
                            relations=result.relations,
                            goals=result.goals,
                            interests=result.interests,
                            beliefs_or_stances=result.beliefs_or_stances,
                            anchors=result.anchors,
                            events=result.events,
                        )
                        extracted += 1
                        logger.info(
                            f"[Metadata] 实体 {entity_name}({entity_id}) 元数据提取并回写成功"
                        )

                        # 同步写入 PostgreSQL end_user_info
                        if end_user_id:
                            _sync_end_user_info_pg(
                                end_user_id=end_user_id,
                                aliases=aliases,
                                extracted_metadata=result.model_dump(),
                            )
                    else:
                        # 即使无新增元数据，也同步 aliases 到 PostgreSQL
                        if end_user_id and aliases:
                            _sync_end_user_info_pg(
                                end_user_id=end_user_id,
                                aliases=aliases,
                                extracted_metadata=None,
                            )
                        logger.debug(
                            f"[Metadata] 实体 {entity_name}({entity_id}) 无新增元数据"
                        )

                    if snapshot_outputs is not None:
                        snapshot_outputs[entity_id] = {
                            "entity_name": entity_name,
                            "descriptions": descriptions,
                            "extracted_metadata": result.model_dump(),
                        }

                except Exception as e:
                    failed += 1
                    if snapshot_outputs is not None:
                        snapshot_outputs[entity_id] = {"error": str(e)}
                    logger.warning(
                        f"[Metadata] 实体 {entity_id} 元数据提取失败: {e}"
                    )
        finally:
            await connector.close()

        # 快照落盘
        if snapshot_outputs is not None and snapshot_dir:
            try:
                from pathlib import Path as _Path
                import json as _json

                _dir = _Path(snapshot_dir)
                _dir.mkdir(parents=True, exist_ok=True)
                _path = _dir / "9_metadata_outputs.json"
                with open(_path, "w", encoding="utf-8") as _f:
                    _json.dump(snapshot_outputs, _f, ensure_ascii=False, indent=2, default=str)
                logger.info(
                    f"[Metadata][Snapshot] 已落盘 {len(snapshot_outputs)} 条元数据结果 → {_path}"
                )
            except Exception as _e:
                logger.warning(
                    f"[Metadata][Snapshot] 快照落盘失败（不影响主流程）: {_e}"
                )

        return {"extracted": extracted, "failed": failed}

    loop = None
    try:
        loop = set_asyncio_event_loop()
        result = loop.run_until_complete(_run())
        elapsed = time.time() - start_time
        logger.info(
            f"[Metadata] 任务完成: 提取={result['extracted']}, "
            f"失败={result['failed']}, 耗时={elapsed:.2f}s, task_id={task_id}"
        )
        return {
            "status": "SUCCESS",
            "total": total,
            **result,
            "elapsed_time": elapsed,
            "task_id": task_id,
        }
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(
            f"[Metadata] 任务失败: {e}, 耗时={elapsed:.2f}s",
            exc_info=True,
        )
        raise self.retry(exc=e)
    finally:
        if loop:
            _shutdown_loop_gracefully(loop)


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


@celery_app.task(name="app.controllers.memory_storage_controller.search_all")
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
        from app.models.end_user_model import EndUser
        from app.repositories.memory_increment_repository import write_memory_increment
        from app.services.memory_storage_service import search_all_batch

        with get_db_context() as db:
            try:
                workspace_uuid = uuid.UUID(workspace_id)

                # 1. 查询当前workspace下的所有app（仅未删除的）
                apps = db.query(App).filter(
                    App.workspace_id == workspace_uuid,
                    App.is_active.is_(True)
                ).all()

                if not apps:
                    # 如果没有app，总量为0
                    memory_increment = write_memory_increment(
                        db=db,
                        workspace_id=workspace_uuid,
                        total_num=0
                    )
                    return {
                        "status": "SUCCESS",
                        "workspace_id": workspace_id,
                        "total_num": 0,
                        "end_user_count": 0,
                        "memory_increment_id": str(memory_increment.id),
                        "created_at": memory_increment.created_at.isoformat(),
                    }

                # 2. 查询所有app下的end_user_id（去重）
                # app_ids = [app.id for app in apps]
                end_users = db.query(EndUser.id).filter(
                    EndUser.workspace_id == workspace_id
                ).distinct().all()

                # 3. 批量查询所有宿主的记忆总量
                end_user_id_list = [str(eid) for (eid,) in end_users]
                batch_result = await search_all_batch(end_user_id_list)

                total_num = sum(batch_result.values())
                end_user_details = [
                    {"end_user_id": uid, "total": batch_result.get(uid, 0)}
                    for uid in end_user_id_list
                ]

                # 4. 写入数据库
                memory_increment = write_memory_increment(
                    db=db,
                    workspace_id=workspace_uuid,
                    total_num=total_num
                )

                return {
                    "status": "SUCCESS",
                    "workspace_id": workspace_id,
                    "total_num": total_num,
                    "end_user_count": len(end_users),
                    "end_user_details": end_user_details,
                    "memory_increment_id": str(memory_increment.id),
                    "created_at": memory_increment.created_at.isoformat(),
                }
            except Exception as e:
                raise e

    try:
        result = asyncio.run(_run())
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

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.models.app_model import App
        from app.models.end_user_model import EndUser
        from app.models.workspace_model import Workspace
        from app.repositories.memory_increment_repository import write_memory_increment
        from app.services.memory_storage_service import search_all_batch

        with get_db_context() as db:
            try:
                # 获取所有活跃的工作空间
                workspaces = db.query(Workspace).filter(
                    Workspace.is_active.is_(True)
                ).all()

                if not workspaces:
                    logger.warning("没有找到活跃的工作空间")
                    return {
                        "status": "SUCCESS",
                        "message": "没有找到活跃的工作空间",
                        "workspace_count": 0,
                        "workspace_results": []
                    }

                logger.info(f"开始统计 {len(workspaces)} 个工作空间的记忆增量")
                all_workspace_results = []

                # 遍历每个工作空间
                for workspace in workspaces:
                    workspace_id = workspace.id
                    logger.info(f"开始处理工作空间: {workspace.name} (ID: {workspace_id})")

                    try:
                        # 1. 查询当前workspace下的所有app（仅未删除的）
                        apps = db.query(App).filter(
                            App.workspace_id == workspace_id,
                            App.is_active.is_(True)
                        ).all()

                        if not apps:
                            # 如果没有app，总量为0
                            memory_increment = write_memory_increment(
                                db=db,
                                workspace_id=workspace_id,
                                total_num=0
                            )
                            all_workspace_results.append({
                                "workspace_id": str(workspace_id),
                                "workspace_name": workspace.name,
                                "status": "SUCCESS",
                                "total_num": 0,
                                "end_user_count": 0,
                                "memory_increment_id": str(memory_increment.id),
                                "created_at": memory_increment.created_at.isoformat(),
                            })
                            logger.info(f"工作空间 {workspace.name} 没有应用，记录总量为0")
                            continue

                        # 2. 查询所有app下的end_user_id（去重）
                        # app_ids = [app.id for app in apps]
                        end_users = db.query(EndUser.id).filter(
                            EndUser.workspace_id == workspace_id
                        ).distinct().all()

                        # 3. 批量查询所有宿主的记忆总量
                        end_user_id_list = [str(eid) for (eid,) in end_users]
                        batch_result = await search_all_batch(end_user_id_list)

                        total_num = sum(batch_result.values())
                        end_user_details = [
                            {"end_user_id": uid, "total": batch_result.get(uid, 0)}
                            for uid in end_user_id_list
                        ]

                        # 4. 写入数据库
                        memory_increment = write_memory_increment(
                            db=db,
                            workspace_id=workspace_id,
                            total_num=total_num
                        )

                        all_workspace_results.append({
                            "workspace_id": str(workspace_id),
                            "workspace_name": workspace.name,
                            "status": "SUCCESS",
                            "total_num": total_num,
                            "end_user_count": len(end_users),
                            "end_user_details": end_user_details,
                            "memory_increment_id": str(memory_increment.id),
                            "created_at": memory_increment.created_at.isoformat(),
                        })

                        logger.info(
                            f"工作空间 {workspace.name} 统计完成: 总量={total_num}, 用户数={len(end_users)}"
                        )

                    except Exception as e:
                        db.rollback()  # 回滚失败的事务，允许继续处理下一个工作空间
                        logger.error(f"处理工作空间 {workspace.name} (ID: {workspace_id}) 失败: {str(e)}")
                        all_workspace_results.append({
                            "workspace_id": str(workspace_id),
                            "workspace_name": workspace.name,
                            "status": "FAILURE",
                            "error": str(e),
                            "total_num": 0,
                            "end_user_count": 0,
                        })

                total_memory = sum(r.get("total_num", 0) for r in all_workspace_results)
                success_count = sum(1 for r in all_workspace_results if r.get("status") == "SUCCESS")

                return {
                    "status": "SUCCESS",
                    "message": f"成功处理 {success_count}/{len(workspaces)} 个工作空间，总记忆量: {total_memory}",
                    "workspace_count": len(workspaces),
                    "success_count": success_count,
                    "total_memory": total_memory,
                    "workspace_results": all_workspace_results
                }

            except Exception as e:
                logger.error(f"记忆增量统计任务执行失败: {str(e)}")
                return {
                    "status": "FAILURE",
                    "error": str(e),
                    "workspace_count": 0,
                    "workspace_results": []
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


@celery_app.task(
    name="app.tasks.regenerate_memory_cache",
    bind=True,
    ignore_result=True,
    max_retries=0,
    acks_late=False,
    time_limit=3600,
    soft_time_limit=3300,
)
def regenerate_memory_cache(self) -> Dict[str, Any]:
    """定时任务：为所有用户重新生成记忆洞察和用户摘要缓存

    遍历所有活动工作空间的所有终端用户，为每个用户重新生成记忆洞察和用户摘要。
    实现错误隔离，单个用户失败不影响其他用户的处理。

    Returns:
        包含任务执行结果的字典，包括：
        - status: 任务状态 (SUCCESS/FAILURE)
        - message: 执行消息
        - workspace_count: 处理的工作空间数量
        - total_users: 总用户数
        - successful: 成功生成的用户数
        - failed: 失败的用户数
        - workspace_results: 每个工作空间的详细结果
        - elapsed_time: 执行耗时（秒）
        - task_id: 任务ID
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.repositories.end_user_repository import EndUserRepository
        from app.services.user_memory_service import UserMemoryService

        logger.info("开始执行记忆缓存重新生成定时任务")

        service = UserMemoryService()

        total_users = 0
        successful = 0
        failed = 0
        workspace_results = []

        with get_db_context() as db:
            try:
                # 获取所有活动工作空间
                repo = EndUserRepository(db)
                workspaces = repo.get_all_active_workspaces()
                logger.info(f"找到 {len(workspaces)} 个活动工作空间")

                # 遍历每个工作空间
                for workspace_id in workspaces:
                    logger.info(f"开始处理工作空间: {workspace_id}")
                    workspace_start_time = time.time()

                    try:
                        # 获取工作空间的所有终端用户
                        end_users = repo.get_all_by_workspace(workspace_id)
                        workspace_user_count = len(end_users)
                        total_users += workspace_user_count

                        logger.info(f"工作空间 {workspace_id} 有 {workspace_user_count} 个终端用户")

                        workspace_successful = 0
                        workspace_failed = 0
                        workspace_errors = []

                        # 遍历每个用户并生成缓存
                        for end_user in end_users:
                            end_user_id = str(end_user.id)

                            try:
                                # 生成记忆洞察
                                insight_result = await service.generate_and_cache_insight(db, end_user_id)

                                # 生成用户摘要
                                summary_result = await service.generate_and_cache_summary(db, end_user_id)

                                # 检查是否都成功
                                if insight_result["success"] and summary_result["success"]:
                                    workspace_successful += 1
                                    successful += 1
                                    logger.info(f"成功为终端用户 {end_user_id} 重新生成缓存")
                                else:
                                    workspace_failed += 1
                                    failed += 1
                                    error_info = {
                                        "end_user_id": end_user_id,
                                        "insight_error": insight_result.get("error"),
                                        "summary_error": summary_result.get("error")
                                    }
                                    workspace_errors.append(error_info)
                                    logger.warning(f"终端用户 {end_user_id} 的缓存重新生成部分失败: {error_info}")

                            except Exception as e:
                                # 单个用户失败不影响其他用户（错误隔离）
                                workspace_failed += 1
                                failed += 1
                                error_info = {
                                    "end_user_id": end_user_id,
                                    "error": str(e)
                                }
                                workspace_errors.append(error_info)
                                logger.error(f"为终端用户 {end_user_id} 重新生成缓存时出错: {str(e)}")

                        workspace_elapsed = time.time() - workspace_start_time

                        # 记录工作空间处理结果
                        workspace_result = {
                            "workspace_id": str(workspace_id),
                            "total_users": workspace_user_count,
                            "successful": workspace_successful,
                            "failed": workspace_failed,
                            "errors": workspace_errors[:10],  # 只保留前10个错误
                            "elapsed_time": workspace_elapsed
                        }
                        workspace_results.append(workspace_result)

                        logger.info(
                            f"工作空间 {workspace_id} 处理完成: "
                            f"总数={workspace_user_count}, 成功={workspace_successful}, "
                            f"失败={workspace_failed}, 耗时={workspace_elapsed:.2f}秒"
                        )

                    except Exception as e:
                        # 工作空间处理失败，记录错误并继续处理下一个
                        logger.error(f"处理工作空间 {workspace_id} 时出错: {str(e)}")
                        workspace_results.append({
                            "workspace_id": str(workspace_id),
                            "error": str(e),
                            "total_users": 0,
                            "successful": 0,
                            "failed": 0,
                            "errors": []
                        })

                # 记录总体统计信息
                logger.info(
                    f"记忆缓存重新生成定时任务完成: "
                    f"工作空间数={len(workspaces)}, 总用户数={total_users}, "
                    f"成功={successful}, 失败={failed}"
                )

                return {
                    "status": "SUCCESS",
                    "message": f"成功处理 {len(workspaces)} 个工作空间，总共 {successful}/{total_users} 个用户缓存重新生成成功",
                    "workspace_count": len(workspaces),
                    "total_users": total_users,
                    "successful": successful,
                    "failed": failed,
                    "workspace_results": workspace_results
                }

            except Exception as e:
                logger.error(f"记忆缓存重新生成定时任务执行失败: {str(e)}")
                return {
                    "status": "FAILURE",
                    "error": str(e),
                    "workspace_count": len(workspace_results),
                    "total_users": total_users,
                    "successful": successful,
                    "failed": failed,
                    "workspace_results": workspace_results
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


@celery_app.task(
    name="app.tasks.workspace_reflection_task",
    bind=True,
    ignore_result=True,
    max_retries=0,
    acks_late=False,
    time_limit=300,
    soft_time_limit=240,
)
def workspace_reflection_task(self) -> Dict[str, Any]:
    """定时任务：每30秒运行工作空间反思功能

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.models.workspace_model import Workspace
        from app.services.memory_reflection_service import (
            MemoryReflectionService,
            WorkspaceAppService,
        )

        with get_db_context() as db:
            try:
                # 获取所有工作空间
                workspaces = db.query(Workspace).all()

                if not workspaces:
                    return {
                        "status": "SUCCESS",
                        "message": "没有找到工作空间",
                        "workspace_count": 0,
                        "reflection_results": []
                    }

                all_reflection_results = []

                # 遍历每个工作空间
                for workspace in workspaces:
                    workspace_id = workspace.id
                    logger.info(f"开始处理工作空间反思，workspace_id: {workspace_id}")

                    try:
                        reflection_service = MemoryReflectionService(db)

                        # 使用服务类处理复杂查询逻辑
                        service = WorkspaceAppService(db)
                        result = service.get_workspace_apps_detailed(str(workspace_id))

                        workspace_reflection_results = []

                        for data in result['apps_detailed_info']:
                            if not data['memory_configs']:
                                continue

                            releases = data['releases']
                            memory_configs = data['memory_configs']
                            end_users = data['end_users']

                            for base, config, user in zip(releases, memory_configs, end_users):
                                if str(base['config']) == str(config['config_id']) and str(base['app_id']) == str(
                                        user['app_id']):
                                    # 调用反思服务
                                    logger.info(f"为用户 {user['id']} 启动反思，config_id: {config['config_id']}")

                                    reflection_result = await reflection_service.start_reflection_from_data(
                                        config_data=config,
                                        end_user_id=user['id']
                                    )

                                    workspace_reflection_results.append({
                                        "app_id": base['app_id'],
                                        "config_id": config['config_id'],
                                        "end_user_id": user['id'],
                                        "reflection_result": reflection_result
                                    })

                        all_reflection_results.append({
                            "workspace_id": str(workspace_id),
                            "reflection_count": len(workspace_reflection_results),
                            "reflection_results": workspace_reflection_results
                        })

                        logger.info(
                            f"工作空间 {workspace_id} 反思处理完成，处理了 {len(workspace_reflection_results)} 个任务")

                    except Exception as e:
                        db.rollback()  # Rollback failed transaction to allow next query
                        logger.error(f"处理工作空间 {workspace_id} 反思失败: {str(e)}")
                        all_reflection_results.append({
                            "workspace_id": str(workspace_id),
                            "error": str(e),
                            "reflection_count": 0,
                            "reflection_results": []
                        })

                total_reflections = sum(r.get("reflection_count", 0) for r in all_reflection_results)

                return {
                    "status": "SUCCESS",
                    "message": f"成功处理 {len(workspaces)} 个工作空间，总共 {total_reflections} 个反思任务",
                    "workspace_count": len(workspaces),
                    "total_reflections": total_reflections,
                    "workspace_results": all_reflection_results
                }

            except Exception as e:
                logger.error(f"工作空间反思任务执行失败: {str(e)}")
                return {
                    "status": "FAILURE",
                    "error": str(e),
                    "workspace_count": 0,
                    "reflection_results": []
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


@celery_app.task(
    name="app.tasks.run_forgetting_cycle_task",
    bind=True,
    ignore_result=False,  # 改为 False 以便在 Flower 中查看结果
    max_retries=0,
    acks_late=False,
    time_limit=7200,
    soft_time_limit=7000,
)
def run_forgetting_cycle_task(self, config_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    """定时任务：运行遗忘周期
    
    遍历所有终端用户，执行遗忘周期。
    """
    start_time = time.time()

    async def _process_users() -> Dict[str, Any]:
        with get_db_context() as db:
            end_users = db.query(EndUser).all()
            if not end_users:
                logger.info("没有终端用户，跳过遗忘周期")
                return {"status": "SUCCESS", "message": "没有终端用户",
                        "report": {"merged_count": 0, "failed_count": 0, "processed_users": 0},
                        "duration_seconds": time.time() - start_time}

            logger.info(f"开始处理 {len(end_users)} 个终端用户的遗忘周期")
            forget_service = MemoryForgetService()
            total_merged = total_failed = processed_users = 0
            failed_users = []

            for end_user in end_users:
                try:
                    # 获取用户配置（自动回退到工作空间默认配置）
                    connected_config = get_end_user_connected_config(str(end_user.id), db)
                    user_config_id = resolve_config_id(connected_config.get("memory_config_id"), db)

                    if not user_config_id:
                        failed_users.append({"end_user_id": str(end_user.id), "error": "无法获取配置"})
                        continue

                    # 执行遗忘周期
                    report = await forget_service.trigger_forgetting_cycle(
                        db=db, end_user_id=str(end_user.id), config_id=user_config_id
                    )

                    total_merged += report.get('merged_count', 0)
                    total_failed += report.get('failed_count', 0)
                    processed_users += 1

                    logger.info(f"用户 {end_user.id}: 融合 {report.get('merged_count', 0)} 对节点")

                except Exception as e:
                    logger.error(f"处理用户 {end_user.id} 失败: {e}", exc_info=True)
                    failed_users.append({"end_user_id": str(end_user.id), "error": str(e)})

            duration = time.time() - start_time
            logger.info(f"遗忘周期完成: {processed_users}/{len(end_users)} 用户, "
                       f"融合 {total_merged} 对, 耗时 {duration:.2f}s")

            return {
                "status": "SUCCESS",
                "message": f"处理 {processed_users} 个用户",
                "report": {
                    "merged_count": total_merged,
                    "failed_count": total_failed,
                    "processed_users": processed_users,
                    "total_users": len(end_users),
                    "failed_users": failed_users
                },
                "duration_seconds": duration
            }

    # 运行异步函数
    try:
        return asyncio.run(_process_users())
    except Exception as e:
        logger.error(f"遗忘周期任务失败: {e}", exc_info=True)
        return {
            "status": "FAILED",
            "message": f"任务失败: {str(e)}",
            "duration_seconds": time.time() - start_time
        }


# =============================================================================
# 隐性记忆和情绪数据更新定时任务
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
    """定时任务：更新所有用户的隐性记忆画像和情绪建议数据

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
            try:
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

            except Exception as e:
                logger.error(f"隐性记忆和情绪数据更新定时任务执行失败: {str(e)}")
                return {
                    "status": "FAILURE",
                    "error": str(e),
                    "total_users": total_users,
                    "successful_implicit": successful_implicit,
                    "successful_emotion": successful_emotion,
                    "failed": failed,
                    "new_users_initialized": 0,
                    "new_users_failed": 0,
                    "user_results": user_results[:50]
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
    存量用户的数据刷新由定时任务 update_implicit_emotions_storage 负责。

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

        initialized = 0
        failed = 0
        skipped = 0

        with get_db_context() as db:
            repo = ImplicitEmotionsStorageRepository(db)

            for end_user_id in end_user_ids:
                existing = repo.get_by_end_user_id(end_user_id)
                if existing is not None:
                    skipped += 1
                    continue

                logger.info(f"用户 {end_user_id} 无记录，开始初始化")
                implicit_ok = False
                emotion_ok = False
                try:
                    try:
                        implicit_service = ImplicitMemoryService(db=db, end_user_id=end_user_id)
                        profile_data = await implicit_service.generate_complete_profile(user_id=end_user_id)
                        await implicit_service.save_profile_cache(
                            end_user_id=end_user_id, profile_data=profile_data, db=db
                        )
                        implicit_ok = True
                    except Exception as e:
                        logger.error(f"用户 {end_user_id} 隐性记忆初始化失败: {e}")

                    try:
                        emotion_service = EmotionAnalyticsService()
                        suggestions_data = await emotion_service.generate_emotion_suggestions(
                            end_user_id=end_user_id, db=db, language="zh"
                        )
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

        logger.info(f"按需初始化完成: 初始化={initialized}, 跳过={skipped}, 失败={failed}")
        return {
            "status": "SUCCESS",
            "initialized": initialized,
            "skipped": skipped,
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
        language = "zh"

        service = MemoryAgentService()

        for end_user_id in end_user_ids:
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
                result = await service.get_interest_distribution_by_user(
                    end_user_id=end_user_id,
                    limit=5,
                    language=language,
                )
                await InterestMemoryCache.set_interest_distribution(
                    end_user_id=end_user_id,
                    language=language,
                    data=result,
                    expire=INTEREST_CACHE_EXPIRE,
                )
                initialized += 1
                logger.info(f"用户 {end_user_id} 兴趣分布缓存生成成功")
            except Exception as e:
                failed += 1
                logger.error(f"用户 {end_user_id} 兴趣分布缓存生成失败: {e}")

        logger.info(f"兴趣分布按需初始化完成: 初始化={initialized}, 跳过={skipped}, 失败={failed}")
        return {
            "status": "SUCCESS",
            "initialized": initialized,
            "skipped": skipped,
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
    max_retries=2,
    acks_late=True,
    time_limit=1800,  # 30分钟硬超时
    soft_time_limit=1700,
)
def run_incremental_clustering(
    self,
    end_user_id: str,
    new_entity_ids: List[str],
    llm_model_id: Optional[str] = None,
    embedding_model_id: Optional[str] = None,
    language: str = "zh",
) -> Dict[str, Any]:
    """增量聚类任务：处理新增实体的社区分配和元数据生成。
    
    此任务在后台异步执行，不阻塞 write_message 主流程。
    
    Args:
        end_user_id: 用户 ID
        new_entity_ids: 新增实体 ID 列表
        llm_model_id: LLM 模型 ID（可选）
        embedding_model_id: Embedding 模型 ID（可选）
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
            f"实体数={len(new_entity_ids)}, llm_model_id={llm_model_id}"
        )

        connector = Neo4jConnector()
        try:
            engine = LabelPropagationEngine(
                connector=connector,
                llm_model_id=llm_model_id,
                embedding_model_id=embedding_model_id,
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

    try:
        loop = set_asyncio_event_loop()
        result = loop.run_until_complete(_run())
        result["elapsed_time"] = time.time() - start_time
        result["task_id"] = self.request.id

        logger.info(
            f"[IncrementalClustering] 任务完成 - task_id={self.request.id}, "
            f"elapsed_time={result['elapsed_time']:.2f}s"
        )

        return result
    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.error(
            f"[IncrementalClustering] 任务失败 - task_id={self.request.id}, "
            f"elapsed_time={elapsed_time:.2f}s, error={str(e)}",
            exc_info=True
        )
        return {
            "status": "FAILURE",
            "error": str(e),
            "end_user_id": end_user_id,
            "elapsed_time": elapsed_time,
            "task_id": self.request.id,
        }


@celery_app.task(
    name="app.tasks.init_community_clustering_for_users",
    bind=True,
    ignore_result=False,
    max_retries=0,
    acks_late=False,
    time_limit=7200,  # 2小时硬超时
    soft_time_limit=6900,
)
def init_community_clustering_for_users(self, end_user_ids: List[str], workspace_id: Optional[str] = None) -> Dict[str, Any]:
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

            # 批量预取所有用户的配置（内置兜底：用户配置不可用时自动回退到工作空间默认配置）
            user_llm_map: Dict[str, Optional[str]] = {}
            user_embedding_map: Dict[str, Optional[str]] = {}
            try:
                with get_db_context() as db:
                    from app.services.memory_agent_service import get_end_users_connected_configs_batch
                    from app.services.memory_config_service import MemoryConfigService
                    batch_configs = get_end_users_connected_configs_batch(end_user_ids, db)
                    for uid, cfg_info in batch_configs.items():
                        config_id = cfg_info.get("memory_config_id")
                        if config_id:
                            try:
                                cfg = MemoryConfigService(db).load_memory_config(config_id=config_id)
                                user_llm_map[uid] = str(cfg.llm_model_id) if cfg.llm_model_id else None
                                user_embedding_map[uid] = str(cfg.embedding_model_id) if cfg.embedding_model_id else None
                            except Exception as e:
                                logger.warning(f"[CommunityCluster] 用户 {uid} 加载配置失败，将使用 None: {e}")
                                user_llm_map[uid] = None
                                user_embedding_map[uid] = None
                        else:
                            user_llm_map[uid] = None
                            user_embedding_map[uid] = None
            except Exception as e:
                logger.warning(f"[CommunityCluster] 批量获取配置失败，所有用户将使用 None: {e}")

            for end_user_id in end_user_ids:
                try:
                    # 已有社区节点时，检查是否存在属性不完整的节点
                    has_communities = await repo.has_communities(end_user_id)
                    if has_communities:
                        llm_model_id = user_llm_map.get(end_user_id)
                        embedding_model_id = user_embedding_map.get(end_user_id)
                        incomplete_ids = await repo.get_incomplete_communities(
                            end_user_id, check_embedding=bool(embedding_model_id)
                        )
                        if not incomplete_ids:
                            skipped += 1
                            logger.debug(f"[CommunityCluster] 用户 {end_user_id} 社区节点均完整，跳过")
                            continue

                        # 对不完整的社区节点逐一补全元数据
                        engine = LabelPropagationEngine(
                            connector=connector,
                            llm_model_id=llm_model_id,
                            embedding_model_id=embedding_model_id,
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

                    # 每个用户使用自己的 llm_model_id / embedding_model_id
                    llm_model_id = user_llm_map.get(end_user_id)
                    embedding_model_id = user_embedding_map.get(end_user_id)
                    engine = LabelPropagationEngine(
                        connector=connector,
                        llm_model_id=llm_model_id,
                        embedding_model_id=embedding_model_id,
                    )

                    logger.info(
                        f"[CommunityCluster] 用户 {end_user_id} 有 {len(entities)} 个实体，开始全量聚类，llm_model_id={llm_model_id}")
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


# ─── User Metadata Extraction Task ───────────────────────────────────────────


# unused task


# =============================================================================
# Sliding Window Write Tasks
# =============================================================================


@celery_app.task(
    bind=True,
    name="app.tasks.sliding_window_write",
    queue="memory_tasks",
    max_retries=0,
    acks_late=True,
)
def sliding_window_write_task(
    self,
    conversation_id: str,
    message_seq: int,
    context_before: List[dict],
    context_after: List[dict],
    target_message: dict,
    config_id: str,
    end_user_id: str,
    workspace_id: str,
    language: str,
    dispatch_at: str,
) -> None:
    """滑动窗口写入任务。

    1. 从数据库加载 memory_config
    2. 初始化 WritePipeline
    3. 调用 WritePipeline.run_with_window()
    4. 任务完成后（无论成功或失败）删除幂等锁 write_task:{conversation_id}:{message_seq}

    Fire-and-forget：异常时记录日志，不重试。
    """
    async def _run() -> None:
        from app.core.memory.pipelines.write_pipeline import WritePipeline
        from app.services.memory_config_service import MemoryConfigService
        from app.models.conversation_model import Conversation
        from sqlalchemy import select

        with get_db_context() as db:
            config_service = MemoryConfigService(db)

            # config_id 为空时，通过 conversation_id 查出 workspace_id 作为 fallback
            _config_id = config_id if config_id else None
            _workspace_id = workspace_id if workspace_id else None

            if not _workspace_id and conversation_id:
                row = db.execute(
                    select(Conversation.workspace_id).where(
                        Conversation.id == conversation_id
                    )
                ).scalar_one_or_none()
                if row:
                    _workspace_id = str(row)

            memory_config = config_service.load_memory_config(
                config_id=_config_id,
                workspace_id=_workspace_id,
                service_name="SlidingWindowWriteTask",
            )

        pipeline = WritePipeline(
            memory_config=memory_config,
            end_user_id=end_user_id,
            language=language,
        )
        await pipeline.run_with_window(
            target_message=target_message,
            context_before=context_before,
            context_after=context_after,
            conversation_id=conversation_id,
            message_seq=message_seq,
            dispatch_at=dispatch_at,
        )

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error(
            f"[SlidingWindowWrite] 失败: conv={conversation_id}, seq={message_seq}, err={e}",
            exc_info=True,
        )
    finally:
        redis_client = get_sync_redis_client()
        if redis_client:
            try:
                redis_client.delete(f"write_task:{conversation_id}:{message_seq}")
            except Exception as e:
                logger.warning(
                    f"[SlidingWindowWrite] 删除幂等锁失败: conv={conversation_id}, seq={message_seq}, err={e}"
                )


# ──────────────────────────────────────────────
# 滑动窗口写入相关常量
# ──────────────────────────────────────────────

# Redis key 前缀
CONV_ACTIVE_KEY_PREFIX = "conv_active:"
FLUSH_LOCK_KEY_PREFIX = "flush_lock:"

# Flush 任务幂等锁 TTL（秒）：派发 flush_conversation_task 时 SETNX 这把锁
# 防止同一对话被并发兜底，FlushTask 完成（成功或失败）会主动 DELETE 释放
FLUSH_LOCK_TTL_SECONDS = 600


@celery_app.task(
    bind=True,
    name="app.tasks.flush_conversation",
    queue="memory_tasks",
    max_retries=0,
    acks_late=True,
)
def flush_conversation_task(self, conversation_id: str) -> None:
    """兜底写入任务：逐条处理 write_cursor 后的所有未写入消息。

    使用 memory_write:{end_user_id} 锁与其他写入路径互斥，保证同一 user 串行。
    完成后（无论成功或失败）删除 flush_lock:{conversation_id}。
    Fire-and-forget：异常时记录日志，不重试。
    """
    # 提前查 end_user_id 用于加锁
    end_user_id_for_lock: Optional[str] = None
    try:
        from sqlalchemy import select

        from app.models.conversation_model import Conversation

        with get_db_context() as db:
            row = db.execute(
                select(Conversation.user_id).where(Conversation.id == conversation_id)
            ).scalar_one_or_none()
            if row:
                end_user_id_for_lock = str(row)
    except Exception as e:
        logger.warning(
            f"[FlushTask] 查询 end_user_id 失败，将以无锁模式执行: conv={conversation_id}, err={e}"
        )

    async def _run() -> None:
        from app.core.memory.sliding_window.flush_task import FlushTask

        await FlushTask().run(conversation_id)

    redis_client = get_sync_redis_client()
    write_lock = None
    write_lock_token = None
    if redis_client is not None and end_user_id_for_lock:
        write_lock = RedisFairLock(
            key=f"memory_write:{end_user_id_for_lock}",
            redis_client=redis_client,
            expire=600,
            timeout=3600,
            auto_renewal=True,
        )
        if not write_lock.acquire():
            logger.warning(
                f"[FlushTask] 获取锁超时，跳过本次 flush: "
                f"conv={conversation_id}, end_user_id={end_user_id_for_lock}"
            )
            # 释放幂等锁，后续 Beat 会重新派发
            try:
                redis_client.delete(f"{FLUSH_LOCK_KEY_PREFIX}{conversation_id}")
            except Exception:
                pass
            return

        from app.services.memory_agent_service import _set_write_lock_holder
        write_lock_token = _set_write_lock_holder(end_user_id_for_lock)

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error(
            f"[FlushTask] 失败: conv={conversation_id}, err={e}",
            exc_info=True,
        )
    finally:
        if write_lock_token is not None:
            try:
                from app.services.memory_agent_service import _reset_write_lock_holder
                _reset_write_lock_holder(write_lock_token)
            except Exception as e:
                logger.warning(f"[FlushTask] 重置锁标记失败: {e}")
        if write_lock is not None:
            try:
                write_lock.release()
            except Exception as e:
                logger.warning(f"[FlushTask] 释放写入锁失败: {e}")
        if redis_client:
            try:
                redis_client.delete(f"{FLUSH_LOCK_KEY_PREFIX}{conversation_id}")
            except Exception as e:
                logger.warning(
                    f"[FlushTask] 删除 flush_lock 失败: conv={conversation_id}, err={e}"
                )


@celery_app.task(
    name="app.tasks.scan_idle_conversations",
    queue="periodic_tasks",
    max_retries=0,
    acks_late=False,
)
def scan_idle_conversations_task() -> None:
    """Celery Beat 定时任务（每 60 秒）：扫描空闲对话并派发兜底写入任务。

    优先从 Redis Set (pending_conversations) 获取候选对话 ID，避免全表 JOIN 扫描。
    若 Set 不可用则回退到数据库查询。

    扫描条件（三者同时满足才派发 flush_conversation_task）：
    1. 对话存在未写入消息（来自 Redis Set 或 DB 查询）
    2. Redis 中 conv_active:{conversation_id} 已过期或不存在（对话空闲 >5 分钟）
    3. Redis 中 flush_lock:{conversation_id} 不存在（无正在执行的 Flush_Task）

    满足条件时：原子写入 flush_lock（TTL=600s），再派发 flush_conversation_task。

    注意：conv_active key 由 MemoryService._refresh_active_key 写在
    settings.REDIS_DB（DB 13），而 flush_lock 与其他 Celery 共享数据写在
    settings.REDIS_DB_CELERY_BACKEND（DB 15）——两者 DB 不同，扫描时需要
    分别从对应 DB 读取。
    """
    from sqlalchemy import func, select, text

    from app.models.conversation_model import Conversation

    redis_client = get_sync_redis_client()
    if redis_client is None:
        logger.error("[ScanIdle] Redis 不可用，跳过本次扫描")
        return

    # 单独构造一个连接到 settings.REDIS_DB 的客户端，用于读取 conv_active key 和 pending_conversations Set
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
            f"[ScanIdle] 无法连接 conv_active 所在 Redis DB（settings.REDIS_DB），"
            f"将跳过空闲检查（所有对话视为活跃）: err={e}"
        )
        active_redis_client = None

    dispatched = 0
    skipped_active = 0
    skipped_locked = 0

    try:
        # 优先从 Redis Set 获取候选对话 ID（O(N) SMEMBERS，避免全表 JOIN）
        candidate_conv_ids: list[str] | None = None
        if active_redis_client is not None:
            try:
                from app.core.memory.sliding_window.window_utils import PENDING_CONVERSATIONS_SET_KEY
                candidates = active_redis_client.smembers(PENDING_CONVERSATIONS_SET_KEY)
                if candidates:
                    candidate_conv_ids = list(candidates)
                    logger.info(f"[ScanIdle] 从 Redis Set 获取 {len(candidate_conv_ids)} 个候选对话")
            except Exception as e:
                logger.warning(f"[ScanIdle] 读取 pending_conversations Set 失败，回退到 DB 查询: {e}")

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

        logger.info(f"[ScanIdle] 发现 {len(candidate_conv_ids)} 个对话存在未写入消息")

        for conv_id_str in candidate_conv_ids:
            # 检查 conv_active key 是否存在（存在则对话仍活跃，跳过）
            # conv_active 写在 settings.REDIS_DB（DB 13），需要用专属 client 读取
            if active_redis_client is not None:
                try:
                    active_key = f"{CONV_ACTIVE_KEY_PREFIX}{conv_id_str}"
                    if active_redis_client.exists(active_key):
                        skipped_active += 1
                        continue
                except Exception as e:
                    logger.warning(f"[ScanIdle] 检查 conv_active 失败: conv={conv_id_str}, err={e}")
                    # 检查失败时保守起见跳过——避免误派发兜底
                    skipped_active += 1
                    continue
            else:
                # 拿不到 active client：保守起见全部视为活跃，跳过派发
                skipped_active += 1
                continue

            # 原子写入 flush_lock（nx=True 保证只有一个 worker 能成功）
            try:
                flush_lock_key = f"{FLUSH_LOCK_KEY_PREFIX}{conv_id_str}"
                acquired = redis_client.set(
                    flush_lock_key, "1",
                    ex=FLUSH_LOCK_TTL_SECONDS, nx=True,
                )
                if not acquired:
                    # 锁已存在，说明已有 Flush_Task 在处理
                    skipped_locked += 1
                    continue
            except Exception as e:
                logger.warning(f"[ScanIdle] 写入 flush_lock 失败: conv={conv_id_str}, err={e}")
                continue

            # 派发 flush_conversation_task
            try:
                flush_conversation_task.apply_async(
                    kwargs={"conversation_id": conv_id_str},
                    queue="memory_tasks",
                )
                dispatched += 1
                logger.info(f"[ScanIdle] 派发 FlushTask: conv={conv_id_str}")
            except Exception as e:
                # 派发失败时释放锁，避免死锁
                logger.error(f"[ScanIdle] 派发 FlushTask 失败: conv={conv_id_str}, err={e}")
                try:
                    redis_client.delete(flush_lock_key)
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"[ScanIdle] 扫描任务失败: err={e}", exc_info=True)
    finally:
        # 释放专属于 DB 13 的 Redis client，避免长跑 Beat 进程慢慢累积 socket fd
        if active_redis_client is not None:
            try:
                active_redis_client.close()
            except Exception:
                pass

    logger.info(
        f"[ScanIdle] 扫描完成: 派发={dispatched}, 跳过(活跃)={skipped_active}, 跳过(已锁)={skipped_locked}"
    )

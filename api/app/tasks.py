import asyncio
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
from app.core.rag.prompts.generator import question_proposal
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import (
    ElasticSearchVectorFactory,
)
from app.db import get_db, get_db_context
from app.models import Document, File, Knowledge
from app.models.end_user_model import EndUser
from app.schemas import document_schema, file_schema
from app.services.memory_agent_service import MemoryAgentService, get_end_user_connected_config
from app.services.memory_forget_service import MemoryForgetService
from app.utils.config_utils import resolve_config_id
from app.utils.redis_lock import RedisFairLock

logger = get_logger(__name__)

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
                max_connections=10,
                socket_connect_timeout=5,
                socket_timeout=5,
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
    """Set the asyncio event loop for the current thread.

    Always creates a fresh event loop to avoid 'Event loop is closed' errors
    caused by stale httpx.AsyncClient objects from previous task runs.
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
        # Set a new event loop so subsequent tasks get a fresh one
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)


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


@celery_app.task(name="app.core.rag.tasks.parse_document")
def parse_document(file_path: str, document_id: uuid.UUID):
    """
    Document parsing, vectorization, and storage
    """
    # Force re-importing Trio in child processes (to avoid inheriting the state of the parent process)
    import importlib

    import trio
    importlib.reload(trio)
    db = next(get_db())  # Manually call the generator
    db_document = None
    db_knowledge = None
    progress_msg = f"{datetime.now().strftime('%H:%M:%S')} Task has been received.\n"
    try:
        db_document = db.query(Document).filter(Document.id == document_id).first()
        db_knowledge = db.query(Knowledge).filter(Knowledge.id == db_document.kb_id).first()
        # 1. Document parsing & segmentation
        progress_msg += f"{datetime.now().strftime('%H:%M:%S')} Start to parse.\n"
        start_time = time.time()
        db_document.progress = 0.0
        db_document.progress_msg = progress_msg
        db_document.process_begin_at = datetime.now(tz=timezone.utc)
        db_document.process_duration = 0.0
        db_document.run = 1
        db.commit()
        db.refresh(db_document)

        def progress_callback(prog=None, msg=None):
            nonlocal progress_msg  # Declare the use of an external progress_msg variable
            progress_msg += f"{datetime.now().strftime('%H:%M:%S')} parse progress: {prog} msg: {msg}.\n"

        # Prepare to configure chat_mdl、embedding_model、vision_model information
        chat_model = Base(
            key=db_knowledge.llm.api_keys[0].api_key,
            model_name=db_knowledge.llm.api_keys[0].model_name,
            base_url=db_knowledge.llm.api_keys[0].api_base
        )
        embedding_model = OpenAIEmbed(
            key=db_knowledge.embedding.api_keys[0].api_key,
            model_name=db_knowledge.embedding.api_keys[0].model_name,
            base_url=db_knowledge.embedding.api_keys[0].api_base
        )
        vision_model = QWenCV(
            key=db_knowledge.image2text.api_keys[0].api_key,
            model_name=db_knowledge.image2text.api_keys[0].model_name,
            lang="Chinese",
            base_url=db_knowledge.image2text.api_keys[0].api_base
        )
        if re.search(r"\.(da|wave|wav|mp3|aac|flac|ogg|aiff|au|midi|wma|realaudio|vqf|oggvorbis|ape?)$", file_path,
                     re.IGNORECASE):
            vision_model = QWenSeq2txt(
                key=os.getenv("QWEN3_OMNI_API_KEY", ""),
                model_name=os.getenv("QWEN3_OMNI_MODEL_NAME", "qwen3-omni-flash"),
                lang="Chinese",
                base_url=os.getenv("QWEN3_OMNI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            )
        elif re.search(r"\.(png|jpeg|jpg|gif|bmp|svg|mp4|mov|avi|flv|mpeg|mpg|webm|wmv|3gp|3gpp|mkv?)$", file_path,
                       re.IGNORECASE):
            vision_model = QWenCV(
                key=os.getenv("QWEN3_OMNI_API_KEY", ""),
                model_name=os.getenv("QWEN3_OMNI_MODEL_NAME", "qwen3-omni-flash"),
                lang="Chinese",
                base_url=os.getenv("QWEN3_OMNI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            )
        else:
            print(file_path)

        from app.core.rag.app.naive import chunk
        res = chunk(filename=file_path,
                    from_page=0,
                    to_page=100000,
                    callback=progress_callback,
                    vision_model=vision_model,
                    parser_config=db_document.parser_config,
                    is_root=False)

        progress_msg += f"{datetime.now().strftime('%H:%M:%S')} Finish parsing.\n"
        db_document.progress = 0.8
        db_document.progress_msg = progress_msg
        db.commit()
        db.refresh(db_document)

        # 2. Document vectorization and storage
        total_chunks = len(res)
        progress_msg += f"{datetime.now().strftime('%H:%M:%S')} Generate {total_chunks} chunks.\n"
        batch_size = 100
        total_batches = ceil(total_chunks / batch_size)
        progress_per_batch = 0.2 / total_batches  # Progress of each batch
        vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
        # 2.1 Delete document vector index
        vector_service.delete_by_metadata_field(key="document_id", value=str(document_id))
        # 2.2 Vectorize and import batch documents
        for batch_start in range(0, total_chunks, batch_size):
            batch_end = min(batch_start + batch_size, total_chunks)  # prevent out-of-bounds
            batch = res[batch_start: batch_end]  # Retrieve the current batch
            chunks = []

            # Process the current batch
            for idx_in_batch, item in enumerate(batch):
                global_idx = batch_start + idx_in_batch  # Calculate global index
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
                if db_document.parser_config.get("auto_questions", 0):
                    topn = db_document.parser_config["auto_questions"]
                    cached = get_llm_cache(chat_model.model_name, item["content_with_weight"], "question",
                                           {"topn": topn})
                    if not cached:
                        cached = question_proposal(chat_model, item["content_with_weight"], topn)
                        set_llm_cache(chat_model.model_name, item["content_with_weight"], cached, "question",
                                      {"topn": topn})
                    chunks.append(
                        DocumentChunk(page_content=f"question: {cached} answer: {item['content_with_weight']}",
                                      metadata=metadata))
                else:
                    chunks.append(DocumentChunk(page_content=item["content_with_weight"], metadata=metadata))

            # Bulk segmented vector import
            vector_service.add_chunks(chunks)

            # Update progress
            db_document.progress += progress_per_batch
            progress_msg += f"{datetime.now().strftime('%H:%M:%S')} Embedding progress  ({db_document.progress}).\n"
            db_document.progress_msg = progress_msg
            db_document.process_duration = time.time() - start_time
            db_document.run = 0
            db.commit()
            db.refresh(db_document)

        # Vectorization and data entry completed
        progress_msg += f"{datetime.now().strftime('%H:%M:%S')} Indexing done.\n"
        db_document.chunk_num = total_chunks
        db_document.progress = 1.0
        db_document.process_duration = time.time() - start_time
        progress_msg += f"{datetime.now().strftime('%H:%M:%S')} Task done ({db_document.process_duration}s).\n"
        db_document.progress_msg = progress_msg
        db_document.run = 0
        db.commit()

        # using graphrag
        if db_knowledge.parser_config and db_knowledge.parser_config.get("graphrag", {}).get("use_graphrag", False):
            graphrag_conf = db_knowledge.parser_config.get("graphrag", {})
            with_resolution = graphrag_conf.get("resolution", False)
            with_community = graphrag_conf.get("community", False)

            def callback(*args, msg=None, **kwargs):
                nonlocal progress_msg
                message = msg or (args[0] if args else "No message")
                progress_msg += f"{datetime.now().strftime('%H:%M:%S')} run graphrag msg: {message}.\n"

            progress_msg += f"{datetime.now().strftime('%H:%M:%S')} Start to run graphrag.\n"
            start_time = time.time()
            db_document.progress_msg = progress_msg
            db.commit()
            db.refresh(db_document)

            task = {
                "id": str(db_document.id),
                "workspace_id": str(db_knowledge.workspace_id),
                "kb_id": str(db_knowledge.id),
                "parser_config": db_knowledge.parser_config,
            }

            # init_graphrag
            vts, _ = embedding_model.encode(["ok"])
            vector_size = len(vts[0])
            init_graphrag(task, vector_size)

            async def _run(
                    row: dict,
                    document_ids: list[str],
                    language: str,
                    parser_config: dict,
                    vector_service,
                    chat_model,
                    embedding_model,
                    callback,
                    with_resolution: bool = True,
                    with_community: bool = True
            ) -> dict:
                await trio.sleep(5)  # Delay for 10 seconds
                nonlocal progress_msg  # Declare the use of an external progress_msg variable
                result = await run_graphrag_for_kb(
                    row=row,
                    document_ids=document_ids,
                    language=language,
                    parser_config=parser_config,
                    vector_service=vector_service,
                    chat_model=chat_model,
                    embedding_model=embedding_model,
                    callback=callback,
                    with_resolution=with_resolution,
                    with_community=with_community,
                )
                progress_msg += f"{datetime.now().strftime('%H:%M:%S')} GraphRAG task result for task {task}:\n{result}\n"
                return result

            def sync_task():
                trio.run(
                    lambda: _run(
                        row=task,
                        document_ids=[str(db_document.id)],
                        language="Chinese",
                        parser_config=db_knowledge.parser_config,
                        vector_service=vector_service,
                        chat_model=chat_model,
                        embedding_model=embedding_model,
                        callback=callback,
                        with_resolution=with_resolution,
                        with_community=with_community,
                    )
                )

            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(sync_task)
                    future.result()  # Blocks until the task completes
            except Exception as e:
                progress_msg += f"{datetime.now().strftime('%H:%M:%S')} GraphRAG task failed for task {task}:\n{str(e)}\n"
            progress_msg += f"{datetime.now().strftime('%H:%M:%S')} Knowledge Graph done ({time.time() - start_time}s)"
            db_document.progress_msg = progress_msg
            db.commit()
            db.refresh(db_document)

        result = f"parse document '{db_document.file_name}' processed successfully."
        return result
    except Exception as e:
        if 'db_document' in locals():
            db_document.progress_msg += f"Failed to vectorize and import the parsed document:{str(e)}\n"
            db_document.run = 0
            db.commit()
        result = f"parse document '{db_document.file_name}' failed."
        return result
    finally:
        db.close()


@celery_app.task(name="app.core.rag.tasks.build_graphrag_for_kb")
def build_graphrag_for_kb(kb_id: uuid.UUID):
    """
    build knowledge graph
    """
    # Force re-importing Trio in child processes (to avoid inheriting the state of the parent process)
    import importlib

    import trio
    importlib.reload(trio)
    db = next(get_db())  # Manually call the generator
    db_documents = None
    db_knowledge = None
    try:
        db_documents = db.query(Document).filter(Document.kb_id == kb_id).all()
        db_knowledge = db.query(Knowledge).filter(Knowledge.id == kb_id).first()
        # 1. Prepare to configure chat_mdl、embedding_model、vision_model information
        chat_model = Base(
            key=db_knowledge.llm.api_keys[0].api_key,
            model_name=db_knowledge.llm.api_keys[0].model_name,
            base_url=db_knowledge.llm.api_keys[0].api_base
        )
        embedding_model = OpenAIEmbed(
            key=db_knowledge.embedding.api_keys[0].api_key,
            model_name=db_knowledge.embedding.api_keys[0].model_name,
            base_url=db_knowledge.embedding.api_keys[0].api_base
        )
        vision_model = QWenCV(
            key=db_knowledge.image2text.api_keys[0].api_key,
            model_name=db_knowledge.image2text.api_keys[0].model_name,
            lang="Chinese",
            base_url=db_knowledge.image2text.api_keys[0].api_base
        )

        # 2. get all document_ids from knowledge base
        vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
        total, items = vector_service.search_by_segment(document_id=None, query=None, pagesize=9999, page=1, asc=True)
        document_ids = [str(item.id) for item in db_documents]

        # 2. using graphrag
        if db_knowledge.parser_config and db_knowledge.parser_config.get("graphrag", {}).get("use_graphrag", False):
            graphrag_conf = db_knowledge.parser_config.get("graphrag", {})
            with_resolution = graphrag_conf.get("resolution", False)
            with_community = graphrag_conf.get("community", False)

            def callback(*args, msg=None, **kwargs):
                message = msg or (args[0] if args else "No message")
                print(f"{datetime.now().strftime('%H:%M:%S')} run graphrag msg: {message}.\n")

            start_time = time.time()
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

            async def _run(row: dict, document_ids: list[str], language: str, parser_config: dict, vector_service,
                           chat_model, embedding_model, callback, with_resolution: bool = True,
                           with_community: bool = True, ) -> dict:
                result = await run_graphrag_for_kb(
                    row=row,
                    document_ids=document_ids,
                    language=language,
                    parser_config=parser_config,
                    vector_service=vector_service,
                    chat_model=chat_model,
                    embedding_model=embedding_model,
                    callback=callback,
                    with_resolution=with_resolution,
                    with_community=with_community,
                )
                print(f"{datetime.now().strftime('%H:%M:%S')} GraphRAG task result for task {task}:\n{result}\n")
                return result

            def sync_task():
                trio.run(
                    lambda: _run(
                        row=task,
                        document_ids=document_ids,
                        language="Chinese",
                        parser_config=db_knowledge.parser_config,
                        vector_service=vector_service,
                        chat_model=chat_model,
                        embedding_model=embedding_model,
                        callback=callback,
                        with_resolution=with_resolution,
                        with_community=with_community,
                    )
                )

            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(sync_task)
                    future.result()  # Blocks until the task completes
            except Exception as e:
                print(f"{datetime.now().strftime('%H:%M:%S')} GraphRAG task failed for task {task}:\n{str(e)}\n")
            finally:
                if db:
                    db.close()
            print(f"{datetime.now().strftime('%H:%M:%S')} Knowledge Graph done ({time.time() - start_time}s)")

        result = f"build knowledge graph '{db_knowledge.name}' processed successfully."
        return result
    except Exception as e:
        if 'db_knowledge' in locals():
            print(f"Failed to build knowledge grap:{str(e)}\n")
        result = f"build knowledge grap '{db_knowledge.name}' failed."
        return result
    finally:
        if db:
            db.close()


@celery_app.task(name="app.core.rag.tasks.sync_knowledge_for_kb")
def sync_knowledge_for_kb(kb_id: uuid.UUID):
    """
    sync knowledge document and Document parsing, vectorization, and storage
    """
    db = next(get_db())  # Manually call the generator
    db_knowledge = None
    try:
        db_knowledge = db.query(Knowledge).filter(Knowledge.id == kb_id).first()
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
                    print(f"\n\nError during crawl: {e}")
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
                                print("\n=== Fetching repositories ===")
                                repos = await client.get_user_repos()
                                print(f"Found {len(repos)} repositories:")
                                all_files = []
                                for repo in repos:
                                    # Get documents from repository
                                    print(f"\n=== Fetching documents from '{repo.name}' ===")
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
                        print(f"\n\nError during fetch feishu: {e}")
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
                        print(f"\n\nError during fetch feishu: {e}")
            case _:  # General
                print(f"General: No synchronization needed\n")

        result = f"sync knowledge '{db_knowledge.name}' processed successfully."
        return result
    except Exception as e:
        if 'db_knowledge' in locals():
            print(f"Failed to sync knowledge:{str(e)}\n")
        result = f"sync knowledge '{db_knowledge.name}' failed."
        return result
    finally:
        db.close()


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


@celery_app.task(name="app.core.memory.agent.write_message", bind=True)
def write_message_task(
        self,
        end_user_id: str,
        message: list[dict],
        config_id: str | int,
        storage_type: str,
        user_rag_memory_id: str,
        language: str = "zh"
) -> Dict[str, Any]:
    """Celery task to process a write message via MemoryAgentService.
    Args:
        end_user_id: Group ID for the memory agent (also used as end_user_id)
        message: Message to write
        config_id: Configuration ID (can be UUID string, integer, or config_id_old)
        storage_type: Storage type (neo4j or rag)
        user_rag_memory_id: User RAG memory ID
        language: 语言类型 ("zh" 中文, "en" 英文)

    Returns:
        Dict containing the result and metadata

    Raises:
        Exception on failure
    """
    logger.info(
        f"[CELERY WRITE] Starting write task - end_user_id={end_user_id}, "
        f"config_id={config_id} (type: {type(config_id).__name__}), "
        f"storage_type={storage_type}, language={language}")
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

    async def _run() -> str:
        with get_db_context() as db:
            logger.info(
                f"[CELERY WRITE] Executing MemoryAgentService.write_memory "
                f"with config_id={actual_config_id} (type: {type(actual_config_id).__name__}), language={language}")
            service = MemoryAgentService()
            result = await service.write_memory(end_user_id, message, actual_config_id, db, storage_type,
                                                user_rag_memory_id, language)
            logger.info(f"[CELERY WRITE] Write completed successfully: {result}")
            return result

    redis_client = get_sync_redis_client()
    lock = None
    if redis_client is not None:
        lock = RedisFairLock(
            key=f"memory_write:{end_user_id}",
            redis_client=redis_client,
            expire=120,
            timeout=300,
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

    try:
        loop = set_asyncio_event_loop()

        result = loop.run_until_complete(_run())
        elapsed_time = time.time() - start_time

        logger.info(f"[CELERY WRITE] Task completed successfully "
                    f"- elapsed_time={elapsed_time:.2f}s, task_id={self.request.id}")

        try:
            _r = get_sync_redis_client()
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
        if lock is not None:
            try:
                lock.release()
            except Exception as e:
                logger.warning(f"[CELERY WRITE] 释放锁失败: {e}")
        # Gracefully shutdown the event loop to prevent
        # 'RuntimeError: Event loop is closed' from httpx.AsyncClient.__del__
        _shutdown_loop_gracefully(loop)


# unused task
# @celery_app.task(name="app.core.memory.agent.health.check_read_service")
# def check_read_service_task() -> Dict[str, str]:
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
        from app.services.memory_storage_service import search_all

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

                # 3. 遍历所有end_user，查询每个宿主的记忆总量并累加
                total_num = 0
                end_user_details = []

                for (end_user_id,) in end_users:
                    try:
                        # 调用 search_all 接口查询该宿主的总量
                        result = await search_all(str(end_user_id))
                        user_total = result.get("total", 0)
                        total_num += user_total
                        end_user_details.append({
                            "end_user_id": str(end_user_id),
                            "total": user_total
                        })
                    except Exception as e:
                        # 记录单个用户查询失败，但继续处理其他用户
                        end_user_details.append({
                            "end_user_id": str(end_user_id),
                            "total": 0,
                            "error": str(e)
                        })

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
        from app.services.memory_storage_service import search_all

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

                        # 3. 遍历所有end_user，查询每个宿主的记忆总量并累加
                        total_num = 0
                        end_user_details = []

                        for (end_user_id,) in end_users:
                            try:
                                # 调用 search_all 接口查询该宿主的总量
                                result = await search_all(str(end_user_id))
                                user_total = result.get("total", 0)
                                total_num += user_total
                                end_user_details.append({
                                    "end_user_id": str(end_user_id),
                                    "total": user_total
                                })
                            except Exception as e:
                                # 记录单个用户查询失败，但继续处理其他用户
                                logger.warning(f"查询用户 {end_user_id} 记忆失败: {str(e)}")
                                end_user_details.append({
                                    "end_user_id": str(end_user_id),
                                    "total": 0,
                                    "error": str(e)
                                })

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
# Long-term Memory Storage Tasks (Batched Write Strategies)
# =============================================================================

# @celery_app.task(name="app.core.memory.agent.long_term_storage.time", bind=True)
# def long_term_storage_time_task(
#     self,
#     end_user_id: str,
#     config_id: str,
#     time_window: int = 5
# ) -> Dict[str, Any]:
#     """Celery task for time-based long-term memory storage.

#     Retrieves recent sessions from Redis within time window and writes to Neo4j.

#     Args:
#         end_user_id: End user identifier
#         config_id: Memory configuration ID
#         time_window: Time window in minutes for retrieving recent sessions

#     Returns:
#         Dict containing task status and metadata
#     """
#     from app.core.logging_config import get_logger
#     logger = get_logger(__name__)

#     logger.info(f"[LONG_TERM_TIME] Starting task - end_user_id={end_user_id}, time_window={time_window}")
#     start_time = time.time()

#     async def _run() -> Dict[str, Any]:
#         from app.core.memory.agent.langgraph_graph.routing.write_router import memory_long_term_storage
#         from app.services.memory_config_service import MemoryConfigService

#         db = next(get_db())
#         try:
#             # Load memory config
#             config_service = MemoryConfigService(db)
#             memory_config = config_service.load_memory_config(
#                 config_id=config_id,
#                 service_name="LongTermStorageTask"
#             )

#             # Execute time-based storage
#             await memory_long_term_storage(end_user_id, memory_config, time_window)

#             return {"status": "SUCCESS", "strategy": "time", "time_window": time_window}
#         finally:
#             db.close()

#     try:
#         import nest_asyncio
#         nest_asyncio.apply()
#     except ImportError:
#         pass

#     try:
#         loop = asyncio.get_event_loop()
#         if loop.is_closed():
#             loop = asyncio.new_event_loop()
#             asyncio.set_event_loop(loop)
#     except RuntimeError:
#         loop = asyncio.new_event_loop()
#         asyncio.set_event_loop(loop)

#     try:
#         result = loop.run_until_complete(_run())
#         elapsed_time = time.time() - start_time

#         logger.info(f"[LONG_TERM_TIME] Task completed - elapsed_time={elapsed_time:.2f}s")

#         return {
#             **result,
#             "end_user_id": end_user_id,
#             "config_id": config_id,
#             "elapsed_time": elapsed_time,
#             "task_id": self.request.id
#         }
#     except Exception as e:
#         elapsed_time = time.time() - start_time
#         logger.error(f"[LONG_TERM_TIME] Task failed - error={str(e)}", exc_info=True)

#         return {
#             "status": "FAILURE",
#             "strategy": "time",
#             "error": str(e),
#             "end_user_id": end_user_id,
#             "config_id": config_id,
#             "elapsed_time": elapsed_time,
#             "task_id": self.request.id
#         }


# @celery_app.task(name="app.core.memory.agent.long_term_storage.aggregate", bind=True)
# def long_term_storage_aggregate_task(
#     self,
#     end_user_id: str,
#     langchain_messages: List[Dict[str, Any]],
#     config_id: str
# ) -> Dict[str, Any]:
#     """Celery task for aggregate-based long-term memory storage.

#     Uses LLM to determine if new messages describe the same event as history.
#     Only writes to Neo4j if messages represent new information (not duplicates).

#     Args:
#         end_user_id: End user identifier
#         langchain_messages: List of messages [{"role": "user/assistant", "content": "..."}]
#         config_id: Memory configuration ID

#     Returns:
#         Dict containing task status, is_same_event flag, and metadata
#     """
#     from app.core.logging_config import get_logger
#     logger = get_logger(__name__)

#     logger.info(f"[LONG_TERM_AGGREGATE] Starting task - end_user_id={end_user_id}")
#     start_time = time.time()

#     async def _run() -> Dict[str, Any]:
#         from app.core.memory.agent.langgraph_graph.routing.write_router import aggregate_judgment
#         from app.core.memory.agent.langgraph_graph.tools.write_tool import chat_data_format
#         from app.core.memory.agent.utils.redis_tool import write_store
#         from app.services.memory_config_service import MemoryConfigService

#         db = next(get_db())
#         try:
#             # Save to Redis buffer first
#             write_store.save_session_write(end_user_id, await chat_data_format(langchain_messages))

#             # Load memory config
#             config_service = MemoryConfigService(db)
#             memory_config = config_service.load_memory_config(
#                 config_id=config_id,
#                 service_name="LongTermStorageTask"
#             )

#             # Execute aggregate judgment
#             result = await aggregate_judgment(end_user_id, langchain_messages, memory_config)

#             return {
#                 "status": "SUCCESS",
#                 "strategy": "aggregate",
#                 "is_same_event": result.get("is_same_event", False),
#                 "wrote_to_neo4j": not result.get("is_same_event", False)
#             }
#         finally:
#             db.close()

#     try:
#         import nest_asyncio
#         nest_asyncio.apply()
#     except ImportError:
#         pass

#     try:
#         loop = asyncio.get_event_loop()
#         if loop.is_closed():
#             loop = asyncio.new_event_loop()
#             asyncio.set_event_loop(loop)
#     except RuntimeError:
#         loop = asyncio.new_event_loop()
#         asyncio.set_event_loop(loop)

#     try:
#         result = loop.run_until_complete(_run())
#         elapsed_time = time.time() - start_time

#         logger.info(f"[LONG_TERM_AGGREGATE] Task completed - is_same_event={result.get('is_same_event')}, elapsed_time={elapsed_time:.2f}s")

#         return {
#             **result,
#             "end_user_id": end_user_id,
#             "config_id": config_id,
#             "elapsed_time": elapsed_time,
#             "task_id": self.request.id
#         }
#     except Exception as e:
#         elapsed_time = time.time() - start_time
#         logger.error(f"[LONG_TERM_AGGREGATE] Task failed - error={str(e)}", exc_info=True)

#         return {
#             "status": "FAILURE",
#             "strategy": "aggregate",
#             "error": str(e),
#             "end_user_id": end_user_id,
#             "config_id": config_id,
#             "elapsed_time": elapsed_time,
#             "task_id": self.request.id
#         }


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

        with get_db_context() as db:
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
) -> Dict[str, Any]:
    """增量聚类任务：处理新增实体的社区分配和元数据生成。
    
    此任务在后台异步执行，不阻塞 write_message 主流程。
    
    Args:
        end_user_id: 用户 ID
        new_entity_ids: 新增实体 ID 列表
        llm_model_id: LLM 模型 ID（可选）
        embedding_model_id: Embedding 模型 ID（可选）
    
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


# unused task
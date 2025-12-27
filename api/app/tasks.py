import asyncio
import trio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from math import ceil
from typing import Any, Dict, List, Optional
import re

import redis
import requests

# Import a unified Celery instance
from app.celery_app import celery_app
from app.core.config import settings
from app.core.rag.graphrag.utils import get_llm_cache, set_llm_cache
from app.core.rag.llm.chat_model import Base
from app.core.rag.llm.cv_model import QWenCV
from app.core.rag.llm.embedding_model import OpenAIEmbed
from app.core.rag.llm.sequence2txt_model import QWenSeq2txt
from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.graphrag.general.index import init_graphrag, run_graphrag_for_kb
from app.core.rag.prompts.generator import question_proposal
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import (
    ElasticSearchVectorFactory,
)
from app.db import get_db, get_db_context
from app.models.document_model import Document
from app.models.knowledge_model import Knowledge
from app.services.memory_agent_service import MemoryAgentService


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
        if db_knowledge.parser_config.get("graphrag", {}).get("use_graphrag", False):
            graphrag_conf = db_knowledge.parser_config.get("graphrag", {})
            with_resolution = graphrag_conf.get("resolution", False)
            with_community = graphrag_conf.get("community", False)

            def callback(msg=None):
                nonlocal progress_msg
                progress_msg += f"{datetime.now().strftime('%H:%M:%S')} run graphrag msg: {msg}.\n"

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

            async def _run(row: dict, document_ids: list[str], language: str, parser_config: dict, vector_service,
                           chat_model, embedding_model, callback, with_resolution: bool = True,
                           with_community: bool = True, ) -> dict:
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

            try:
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
    db = next(get_db())  # Manually call the generator
    db_document = None
    db_knowledge = None
    try:
        db_document = db.query(Document).filter(Document.kb_id == kb_id).all()
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
        document_ids = [item.id for item in db_document]

        # 2. using graphrag
        if db_knowledge.parser_config.get("graphrag", {}).get("use_graphrag", False):
            graphrag_conf = db_knowledge.parser_config.get("graphrag", {})
            with_resolution = graphrag_conf.get("resolution", False)
            with_community = graphrag_conf.get("community", False)

            def callback(msg=None):
                print(f"{datetime.now().strftime('%H:%M:%S')} run graphrag msg: {msg}.\n")

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

            try:
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
            except Exception as e:
                print(f"{datetime.now().strftime('%H:%M:%S')} GraphRAG task failed for task {task}:\n{str(e)}\n")
            print(f"{datetime.now().strftime('%H:%M:%S')} Knowledge Graph done ({time.time() - start_time}s)")

        result = f"build knowledge graph '{db_knowledge.name}' processed successfully."
        return result
    except Exception as e:
        if 'db_knowledge' in locals():
            print(f"Failed to build knowledge grap:{str(e)}\n")
        result = f"build knowledge grap '{db_knowledge.name}' failed."
        return result
    finally:
        db.close()


@celery_app.task(name="app.core.memory.agent.read_message", bind=True)
def read_message_task(self, group_id: str, message: str, history: List[Dict[str, Any]], search_switch: str, config_id: str,storage_type:str,user_rag_memory_id:str) -> Dict[str, Any]:

    """Celery task to process a read message via MemoryAgentService.

    Args:
        group_id: Group ID for the memory agent (also used as end_user_id)
        message: User message to process
        history: Conversation history
        search_switch: Search switch parameter
        config_id: Optional configuration ID
        
    Returns:
        Dict containing the result and metadata
        
    Raises:
        Exception on failure
    """
    start_time = time.time()
    
    # Resolve config_id if None
    actual_config_id = config_id
    if actual_config_id is None:
        try:
            from app.services.memory_agent_service import get_end_user_connected_config
            db = next(get_db())
            try:
                connected_config = get_end_user_connected_config(group_id, db)
                actual_config_id = connected_config.get("memory_config_id")
            finally:
                db.close()
        except Exception as e:
            # Log but continue - will fail later with proper error
            pass
    
    async def _run() -> str:
        db = next(get_db())
        try:
            service = MemoryAgentService()
            return await service.read_memory(group_id, message, history, search_switch, actual_config_id, db, storage_type, user_rag_memory_id)
        finally:
            db.close()

    try:
        # 使用 nest_asyncio 来避免事件循环冲突
        try:
            import nest_asyncio
            nest_asyncio.apply()
        except ImportError:
            pass
        
        # 尝试获取现有事件循环，如果不存在则创建新的
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        result = loop.run_until_complete(_run())
        elapsed_time = time.time() - start_time
        
        return {
            "status": "SUCCESS",
            "result": result,
            "group_id": group_id,
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
            "group_id": group_id,
            "config_id": config_id,
            "elapsed_time": elapsed_time,
            "task_id": self.request.id
        }


@celery_app.task(name="app.core.memory.agent.write_message", bind=True)
def write_message_task(self, group_id: str, message: str, config_id: str,storage_type:str,user_rag_memory_id:str) -> Dict[str, Any]:
    """Celery task to process a write message via MemoryAgentService.
    
    Args:
        group_id: Group ID for the memory agent (also used as end_user_id)
        message: Message to write
        config_id: Optional configuration ID
        
    Returns:
        Dict containing the result and metadata
        
    Raises:
        Exception on failure
    """
    start_time = time.time()
    
    # Resolve config_id if None
    actual_config_id = config_id
    if actual_config_id is None:
        try:
            from app.services.memory_agent_service import get_end_user_connected_config
            db = next(get_db())
            try:
                connected_config = get_end_user_connected_config(group_id, db)
                actual_config_id = connected_config.get("memory_config_id")
            finally:
                db.close()
        except Exception as e:
            # Log but continue - will fail later with proper error
            pass
    
    async def _run() -> str:
        db = next(get_db())
        try:
            service = MemoryAgentService()
            return await service.write_memory(group_id, message, actual_config_id, db, storage_type, user_rag_memory_id)
        finally:
            db.close()

    try:
        # 使用 nest_asyncio 来避免事件循环冲突
        try:
            import nest_asyncio
            nest_asyncio.apply()
        except ImportError:
            pass
        
        # 尝试获取现有事件循环，如果不存在则创建新的
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        result = loop.run_until_complete(_run())
        elapsed_time = time.time() - start_time
        
        return {
            "status": "SUCCESS",
            "result": result,
            "group_id": group_id,
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
            "group_id": group_id,
            "config_id": config_id,
            "elapsed_time": elapsed_time,
            "task_id": self.request.id
        }


def reflection_engine() -> None:
    """Empty function placeholder for timed background reflection.

    Intentionally left blank; replace with real reflection logic later.
    """
    import asyncio

    from app.core.memory.utils.self_reflexion_utils.self_reflexion import self_reflexion

    host_id = uuid.UUID("2f6ff1eb-50c7-4765-8e89-e4566be19122")
    asyncio.run(self_reflexion(host_id))


@celery_app.task(name="app.core.memory.agent.reflection.timer")
def reflection_timer_task() -> None:
    """Periodic Celery task that invokes reflection_engine.
    
    Raises an exception on failure.
    """
    reflection_engine()


@celery_app.task(name="app.core.memory.agent.health.check_read_service")
def check_read_service_task() -> Dict[str, str]:
    """Call read_service and write latest status to Redis.
    
    Returns status data dict that gets written to Redis.
    """
    client = redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD if settings.REDIS_PASSWORD else None
    )
    try:
        api_url = f"http://{settings.SERVER_IP}:8000/api/memory/read_service"
        payload = {
            "user_id": "健康检查",
            "apply_id": "健康检查",
            "group_id": "健康检查",
            "message": "你好",
            "history": [],
            "search_switch": "2",
        }
        resp = requests.post(api_url, json=payload, timeout=15)
        ok = resp.status_code == 200
        status = "Success" if ok else "Fail"
        msg = "接口请求成功" if ok else f"接口请求失败: {resp.status_code}"
        error = "" if ok else resp.text
        code = 0 if ok else 500
    except Exception as e:
        status = "Fail"
        msg = "接口请求失败"
        error = str(e)
        code = 500

    data = {
        "status": status,
        "msg": msg,
        "error": error,
        "code": str(code),
        "time": str(int(time.time())),
    }

    client.hset("memsci:health:read_service", mapping=data)
    client.expire("memsci:health:read_service", int(settings.HEALTH_CHECK_SECONDS))

    return data


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
                
                # 1. 查询当前workspace下的所有app
                apps = db.query(App).filter(App.workspace_id == workspace_uuid).all()
                
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
                app_ids = [app.id for app in apps]
                end_users = db.query(EndUser.id).filter(
                    EndUser.app_id.in_(app_ids)
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


@celery_app.task(name="app.tasks.regenerate_memory_cache", bind=True)
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
        from app.core.logging_config import get_logger
        from app.repositories.end_user_repository import EndUserRepository
        from app.services.user_memory_service import UserMemoryService
        
        logger = get_logger(__name__)
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
        # 使用 nest_asyncio 来避免事件循环冲突
        try:
            import nest_asyncio
            nest_asyncio.apply()
        except ImportError:
            pass
        
        # 尝试获取现有事件循环，如果不存在则创建新的
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
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


@celery_app.task(name="app.tasks.workspace_reflection_task", bind=True)
def workspace_reflection_task(self) -> Dict[str, Any]:
    """定时任务：每30秒运行工作空间反思功能

    Returns:
        包含任务执行结果的字典
    """
    start_time = time.time()

    async def _run() -> Dict[str, Any]:
        from app.core.logging_config import get_api_logger
        from app.models.workspace_model import Workspace
        from app.services.memory_reflection_service import (
            MemoryReflectionService,
            WorkspaceAppService,
        )

        api_logger = get_api_logger()
        
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
                    api_logger.info(f"开始处理工作空间反思，workspace_id: {workspace_id}")

                    try:
                        reflection_service = MemoryReflectionService(db)

                        # 使用服务类处理复杂查询逻辑
                        service = WorkspaceAppService(db)
                        result = service.get_workspace_apps_detailed(str(workspace_id))

                        workspace_reflection_results = []

                        for data in result['apps_detailed_info']:
                            if data['data_configs'] == []:
                                continue

                            releases = data['releases']
                            data_configs = data['data_configs']
                            end_users = data['end_users']

                            for base, config, user in zip(releases, data_configs, end_users):
                                if int(base['config']) == int(config['config_id']) and base['app_id'] == user['app_id']:
                                    # 调用反思服务
                                    api_logger.info(f"为用户 {user['id']} 启动反思，config_id: {config['config_id']}")

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

                        api_logger.info(
                            f"工作空间 {workspace_id} 反思处理完成，处理了 {len(workspace_reflection_results)} 个任务")

                    except Exception as e:
                        api_logger.error(f"处理工作空间 {workspace_id} 反思失败: {str(e)}")
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
                api_logger.error(f"工作空间反思任务执行失败: {str(e)}")
                return {
                    "status": "FAILURE",
                    "error": str(e),
                    "workspace_count": 0,
                    "reflection_results": []
                }

    try:
        # 使用 nest_asyncio 来避免事件循环冲突
        try:
            import nest_asyncio
            nest_asyncio.apply()
        except ImportError:
            pass

        # 尝试获取现有事件循环，如果不存在则创建新的
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

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
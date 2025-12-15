import os
import asyncio
from typing import Any, Dict, List, Optional
import requests
from datetime import datetime, timezone
import time
import uuid
from math import ceil
import redis
import json

from app.db import get_db
from app.models.document_model import Document
from app.models.knowledge_model import Knowledge
from app.core.rag.llm.cv_model import QWenCV
from app.core.rag.llm.chat_model import Base
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
from app.core.rag.models.chunk import DocumentChunk
from app.services.memory_agent_service import MemoryAgentService
from app.core.config import settings
from app.core.rag.graphrag.utils import get_llm_cache, set_llm_cache
from app.core.rag.prompts.generator import question_proposal

# Import a unified Celery instance
from app.celery_app import celery_app


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
        # Prepare to configure chat_mdl、vision_model information
        chat_model = Base(
            key=db_knowledge.llm.api_keys[0].api_key,
            model_name=db_knowledge.llm.api_keys[0].model_name,
            base_url=db_knowledge.llm.api_keys[0].api_base
        )
        vision_model = QWenCV(
            key=db_knowledge.image2text.api_keys[0].api_key,
            model_name=db_knowledge.image2text.api_keys[0].model_name,
            lang="Chinese",
            base_url=db_knowledge.image2text.api_keys[0].api_base
        )
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
                    cached = get_llm_cache(chat_model.model_name, item["content_with_weight"], "question", {"topn": topn})
                    if not cached:
                        cached = question_proposal(chat_model, item["content_with_weight"], topn)
                        set_llm_cache(chat_model.model_name, item["content_with_weight"], cached, "question", {"topn": topn})
                    chunks.append(DocumentChunk(page_content=f"question: {cached} answer: {item['content_with_weight']}", metadata=metadata))
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


@celery_app.task(name="app.core.memory.agent.read_message", bind=True)
def read_message_task(self, group_id: str, message: str, history: List[Dict[str, Any]], search_switch: str, config_id: str,storage_type:str,user_rag_memory_id:str) -> Dict[str, Any]:

    """Celery task to process a read message via MemoryAgentService.

    Args:
        group_id: Group ID for the memory agent
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
    
    async def _run() -> str:
        service = MemoryAgentService()
        return await service.read_memory(group_id, message, history, search_switch, config_id,storage_type,user_rag_memory_id)

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
    except Exception as e:
        elapsed_time = time.time() - start_time
        return {
            "status": "FAILURE",
            "error": str(e),
            "group_id": group_id,
            "config_id": config_id,
            "elapsed_time": elapsed_time,
            "task_id": self.request.id
        }


@celery_app.task(name="app.core.memory.agent.write_message", bind=True)
def write_message_task(self, group_id: str, message: str, config_id: str,storage_type:str,user_rag_memory_id:str) -> Dict[str, Any]:
    """Celery task to process a write message via MemoryAgentService.
    
    Args:
        group_id: Group ID for the memory agent
        message: Message to write
        config_id: Optional configuration ID
        
    Returns:
        Dict containing the result and metadata
        
    Raises:
        Exception on failure
    """
    start_time = time.time()
    
    async def _run() -> str:
        service = MemoryAgentService()
        return await service.write_memory(group_id, message, config_id,storage_type,user_rag_memory_id)

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
    except Exception as e:
        elapsed_time = time.time() - start_time
        return {
            "status": "FAILURE",
            "error": str(e),
            "group_id": group_id,
            "config_id": config_id,
            "elapsed_time": elapsed_time,
            "task_id": self.request.id
        }


def reflection_engine() -> None:
    """Empty function placeholder for timed background reflection.

    Intentionally left blank; replace with real reflection logic later.
    """
    from app.core.memory.utils.self_reflexion_utils.self_reflexion import self_reflexion
    import asyncio

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
        from app.services.memory_storage_service import search_all
        from app.repositories.memory_increment_repository import write_memory_increment
        from app.models.end_user_model import EndUser
        from app.models.app_model import App
        
        db = next(get_db())
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
        finally:
            db.close()
    
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
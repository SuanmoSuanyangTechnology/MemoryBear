# 修改 memory_konwledges_server.py 文件

import asyncio
import os
import re
import uuid
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from app.core.rag.models.chunk import DocumentChunk
from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
from app.core.response_utils import success
from app.db import get_db
from app.schemas import file_schema, document_schema
from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Query
from app.models.document_model import Document
import uuid
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.core.config import settings
from app.models.user_model import User
from app.schemas.file_schema import CustomTextFileCreate
from app.services import document_service, file_service, knowledge_service
from app.celery_app import celery_app
from app.core.logging_config import get_api_logger
from app.schemas.file_schema import CustomTextFileCreate
from app.db import get_db
# 创建一个简单的用户类用于测试
api_logger = get_api_logger()

class ChunkCreate(BaseModel):
    content: str
class SimpleUser:
    def __init__(self, user_id: str):
        # 确保ID是UUID类型
        self.id = user_id
        self.username = user_id

'''解析'''
async def parse_document_by_id(document_id: uuid.UUID, db: Session, current_user: User):
    """
    解析指定文档

    Args:
        document_id: 文档ID
        db: 数据库会话
        current_user: 当前用户

    Returns:
        dict: 包含任务ID的结果字典

    Raises:
        HTTPException: 当文档、文件或知识库不存在时抛出异常
    """

    try:
        # 1. 检查文档是否存在
        api_logger.debug(f"检查文档是否存在: {document_id}")
        db_document = document_service.get_document_by_id(db, document_id=document_id, current_user=current_user)

        if not db_document:
            api_logger.warning(f"文档不存在或无访问权限: document_id={document_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="文档不存在或无访问权限"
            )

        # 2. 检查文件是否存在
        api_logger.debug(f"检查文件是否存在: {db_document.file_id}")
        db_file = file_service.get_file_by_id(db, file_id=db_document.file_id)

        if not db_file:
            api_logger.warning(f"文件不存在或无访问权限: file_id={db_document.file_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="文件不存在或无访问权限"
            )

        # 3. 构建文件路径：/files/{kb_id}/{parent_id}/{file.id}{file.file_ext}
        file_path = os.path.join(
            settings.FILE_PATH,
            str(db_file.kb_id),
            str(db_file.parent_id),
            f"{db_file.id}{db_file.file_ext}"
        )

        # 4. 检查文件是否存在于磁盘上
        if not os.path.exists(file_path):
            api_logger.warning(f"文件未找到（可能已被删除）: file_path={file_path}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="文件未找到（可能已被删除）"
            )

        # 5. 获取知识库信息
        api_logger.info(f"获取知识库详情: knowledge_id={db_document.kb_id}")
        db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=db_document.kb_id,
                                                             current_user=current_user)
        if not db_knowledge:
            api_logger.warning(f"知识库不存在或访问被拒绝: knowledge_id={db_document.kb_id}")
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="知识库不存在或访问被拒绝"
            )

        # 6. 发送解析任务到Celery后台队列
        task = celery_app.send_task("app.core.rag.tasks.parse_document", args=[file_path, document_id])

        result = {
            "task_id": task.id
        }

        api_logger.info(f"文档解析任务已接受: document_id={document_id}, task_id={task.id}")
        return result

    except Exception as e:
        api_logger.error(f"文档解析失败: document_id={document_id} - {str(e)}")
        raise

'''获取块ID'''
async def get_document_chunks(
        kb_id: uuid.UUID,
        document_id: uuid.UUID,
        page: int = 1,
        pagesize: int = 20,
        keywords: Optional[str] = None,
        db: Session = None,
        current_user: User = None
):
    """
    分页查询文档块列表

    Args:
        kb_id: 知识库ID
        document_id: 文档ID
        page: 页码，默认为1
        pagesize: 每页大小，默认为20
        keywords: 用于匹配块内容的关键字
        db: 数据库会话
        current_user: 当前用户

    Returns:
        dict: 包含分页数据的响应结果

    Raises:
        HTTPException: 当知识库不存在或查询失败时抛出异常
    """
    api_logger.info(
        f"分页查询文档块列表: kb_id={kb_id}, document_id={document_id}, page={page}, pagesize={pagesize}, keywords={keywords}, username: {current_user.username}")

    # 参数验证
    if page < 1 or pagesize < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="分页参数必须大于0"
        )

    # 获取知识库信息
    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在或访问被拒绝"
        )

    # 执行分页查询
    try:
        api_logger.debug("开始执行文档块查询")
        vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)
        total, items = vector_service.search_by_segment(
            document_id=str(document_id),
            query=keywords,
            pagesize=pagesize,
            page=page,
            asc=True
        )
        api_logger.info(f"文档块查询成功: total={total}, returned={len(items)} records")
    except Exception as e:
        api_logger.error(f"文档块查询失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(e)}"
        )

    # 构造响应结果
    result = {
        "items": items,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "has_next": True if page * pagesize < total else False
        }
    }

    return success(data=result, msg="文档块列表查询成功")

'''查找文档ID'''
def find_document_id_by_kb_and_filename(
        db: Session,
        kb_id: str,
        file_name: str
) -> str | None:
    """
    通过 kb_id 和 file_name 在 documents 表中查找对应的 ID

    Args:
        db: 数据库会话
        kb_id: 知识库ID
        file_name: 文件名

    Returns:
        str | None: 找到的 document ID，未找到返回 None
    """
    try:
        # 查询 documents 表
        document = db.query(Document).filter(
            Document.kb_id == kb_id,
            Document.file_name == file_name
        ).first()

        if document:
            print(f"找到文档: ID={document.id}, kb_id={kb_id}, file_name={file_name}")
            return str(document.id)
        else:
            return None

    except Exception as e:
        return None

'''获取知识库ID'''
def find_documents_by_kb_id(
        db: Session,
        kb_id: str,
        limit: int = 10
) -> list[dict]:
    """
    通过 kb_id 查找所有相关文档

    Args:
        db: 数据库会话
        kb_id: 知识库ID
        limit: 返回结果数量限制

    Returns:
        list[dict]: 文档列表，包含 id, file_name, created_at 等信息
    """
    try:
        documents = db.query(Document).filter(
            Document.kb_id == kb_id
        ).limit(limit).all()

        result = []
        for doc in documents:
            result.append({
                "id": str(doc.id),
                "file_name": doc.file_name,
                "file_ext": doc.file_ext,
                "file_size": doc.file_size,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
                "status": getattr(doc, 'status', None)
            })
        return result

    except Exception as e:
        return []

''''上传文件'''
async def memory_konwledges_up(
        kb_id: str,
        parent_id: str,
        create_data: file_schema.CustomTextFileCreate,
        db: Session = Depends(get_db),
        current_user: SimpleUser = None,  # 修改为SimpleUser
):
    # 如果没有提供current_user，则创建一个默认的
    if current_user is None:
        current_user = SimpleUser("5d27df0b-7eec-4fa6-9f8b-0f9b7e852f60")

    content_bytes = create_data.content.encode('utf-8')
    file_size = len(content_bytes)
    print(f"file size: {file_size} byte")

    if file_size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The content is empty."
        )

    # If the file size exceeds 50MB (50 * 1024 * 1024 bytes)
    if file_size > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"The content size exceeds the {settings.MAX_FILE_SIZE}byte limit"
        )

    upload_file = file_schema.FileCreate(
        kb_id=kb_id,
        created_by=current_user.id,  # 现在是UUID类型
        parent_id=parent_id,
        file_name=f"{create_data.title}.txt",
        file_ext=".txt",
        file_size=file_size,
    )
    db_file = file_service.create_file(db=db, file=upload_file, current_user=current_user)

    # Construct a save path：/files/{kb_id}/{parent_id}/{file.id}{file_extension}
    # 使用 settings.FILE_PATH 确保与 parse_document_by_id 一致
    save_dir = os.path.join(settings.FILE_PATH, str(kb_id), str(parent_id))

    # 确保目录存在
    Path(save_dir).mkdir(parents=True, exist_ok=True)

    save_path = os.path.join(save_dir, f"{db_file.id}.txt")

    # Save file
    with open(save_path, "wb") as f:
        f.write(content_bytes)

    # Verify whether the file has been saved successfully
    if not os.path.exists(save_path):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File save failed"
        )

    # Create a document
    create_document_data = document_schema.DocumentCreate(
        kb_id=kb_id,
        created_by=current_user.id,
        file_id=db_file.id,
        file_name=db_file.file_name,
        file_ext=db_file.file_ext,
        file_size=db_file.file_size,
        file_meta={},
        parser_id="naive",
        parser_config={
            "layout_recognize": "DeepDOC",
            "chunk_token_num": 128,
            "delimiter": "\n",
            "auto_keywords": 0,
            "auto_questions": 0,
            "html4excel": "false"
        }
    )
    db_document = document_service.create_document(db=db, document=create_document_data, current_user=current_user)

    return success(data=document_schema.Document.model_validate(db_document), msg="custom text upload successful")

'''添加新块'''


async def create_document_chunk(
        kb_id: uuid.UUID,
        document_id: uuid.UUID,
        create_data: ChunkCreate,
        db: Session,
        current_user: User
):
    """
    创建文档块

    Args:
        kb_id: 知识库ID
        document_id: 文档ID
        create_data: 创建数据
        db: 数据库会话
        current_user: 当前用户

    Returns:
        dict: 包含创建的文档块信息的成功响应

    Raises:
        HTTPException: 当知识库或文档不存在时抛出相应异常
    """
    api_logger.info(
        f"创建文档块请求: kb_id={kb_id}, document_id={document_id}, content={create_data.content}, username: {current_user.username}")

    # 1. 获取知识库信息
    db_knowledge = knowledge_service.get_knowledge_by_id(db, knowledge_id=kb_id, current_user=current_user)
    if not db_knowledge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在或访问被拒绝"
        )

    # 2. 获取文档信息
    db_document = db.query(Document).filter(Document.id == document_id).first()
    if not db_document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在或您无访问权限"
        )

    # 3. 初始化向量服务
    vector_service = ElasticSearchVectorFactory().init_vector(knowledge=db_knowledge)

    # 4. 获取排序ID（处理索引不存在的情况）
    sort_id = 0
    try:
        total, items = vector_service.search_by_segment(document_id=str(document_id), pagesize=1, page=1, asc=False)
        if items:
            sort_id = items[0].metadata["sort_id"]
    except Exception as e:
        # 如果索引不存在，从 0 开始
        error_msg = str(e)
        if "index_not_found_exception" in error_msg or "no such index" in error_msg:
            api_logger.warning(f"索引不存在，将从 sort_id=0 开始: {error_msg}")
            sort_id = 0
        else:
            # 其他错误则抛出
            api_logger.error(f"查询文档块失败: {error_msg}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"查询文档块失败: {error_msg}"
            )
    
    sort_id = sort_id + 1

    # 5. 创建文档块
    doc_id = uuid.uuid4().hex
    metadata = {
        "doc_id": doc_id,
        "file_id": str(db_document.file_id),
        "file_name": db_document.file_name,
        "file_created_at": int(db_document.created_at.timestamp() * 1000),
        "document_id": str(document_id),
        "knowledge_id": str(kb_id),
        "sort_id": sort_id,
        "status": 1,
    }
    chunk = DocumentChunk(page_content=create_data.content, metadata=metadata)

    # 6. 存储向量化的文档块（这会自动创建索引如果不存在）
    try:
        vector_service.add_chunks([chunk])
    except Exception as e:
        api_logger.error(f"添加文档块到向量库失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"添加文档块到向量库失败: {str(e)}"
        )

    # 7. 更新 chunk_num
    db_document.chunk_num += 1
    db.commit()

    return success(data=chunk, msg="文档块创建成功")

async def write_rag(group_id, message, user_rag_memory_id):
    """
    将消息写入 RAG 知识库

    Args:
        group_id: 组ID，用作文件标题
        message: 消息内容
        user_rag_memory_id: 知识库ID（必须是有效的UUID）

    Returns:
        写入结果

    Raises:
        HTTPException: 当参数无效或操作失败时
    """
    # 验证 user_rag_memory_id 是否为有效的 UUID
    if not user_rag_memory_id:
        api_logger.error("user_rag_memory_id 为空，无法执行 RAG 写入操作")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="知识库ID不能为空"
        )

    try:
        # 尝试将字符串转换为 UUID 以验证格式
        kb_uuid = uuid.UUID(user_rag_memory_id)
    except (ValueError, AttributeError) as e:
        api_logger.error(f"user_rag_memory_id 不是有效的UUID: {user_rag_memory_id}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"知识库ID格式无效: {user_rag_memory_id}"
        )

    db_gen = get_db()
    db = next(db_gen)

    try:
        create_data = CustomTextFileCreate(title=group_id, content=message)
        current_user = SimpleUser(user_rag_memory_id)
        # 检查文档是否已存在
        document = find_document_id_by_kb_and_filename(db=db, kb_id=user_rag_memory_id, file_name=f"{group_id}.txt")
        print('======',document)
        api_logger.info(f"查找文档结果: document_id={document}")
        if document is not None:
            # 文档已存在，直接添加新块
            api_logger.info(f"文档已存在，添加新块: document_id={document}")

            create_chunks = ChunkCreate(content=message)
            result = await create_document_chunk(
                kb_id=kb_uuid,
                document_id=uuid.UUID(document),
                create_data=create_chunks,
                db=db,
                current_user=current_user
            )
            return result
        else:
            # 文档不存在，创建新文档
            api_logger.info(f"文档不存在，创建新文档: group_id={group_id}")
            result = await memory_konwledges_up(
                kb_id=user_rag_memory_id,
                parent_id=user_rag_memory_id,
                create_data=create_data,
                db=db,
                current_user=current_user
            )
            # 重新查询刚创建的文档ID
            new_document_id = find_document_id_by_kb_and_filename(
                db=db,
                kb_id=user_rag_memory_id,
                file_name=f"{group_id}.txt"
            )

            if new_document_id:
                await parse_document_by_id(new_document_id, db=db, current_user=current_user)
            else:
                api_logger.error(f"创建文档后无法找到文档ID: group_id={group_id}")
            return result
    finally:
        # 确保数据库会话被关闭
        db.close()
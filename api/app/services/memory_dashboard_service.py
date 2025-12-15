from sqlalchemy.orm import Session
from typing import List, Optional
import uuid
from fastapi import HTTPException

from app.models.user_model import User
from app.models.app_model import App
from app.models.end_user_model import EndUser
from app.models.memory_increment_model import MemoryIncrement

from app.repositories import (
    app_repository,
    end_user_repository,
    memory_increment_repository,
    knowledge_repository
)
from app.schemas.end_user_schema import EndUser as EndUserSchema
from app.schemas.memory_increment_schema import MemoryIncrement as MemoryIncrementSchema
from app.schemas.app_schema import App as AppSchema
from app.core.logging_config import get_business_logger


# 获取业务逻辑专用日志器
business_logger = get_business_logger()


def get_current_workspace_type(
    db: Session, 
    workspace_id: uuid.UUID,
    current_user: User
) -> Optional[str]:
    """获取当前工作空间类型"""
    business_logger.info(f"获取工作空间类型: workspace_id={workspace_id}, 操作者: {current_user.username}")
    
    try:
        from app.repositories.workspace_repository import get_workspace_by_id
        
        workspace = get_workspace_by_id(db, workspace_id)
        if not workspace:
            business_logger.warning(f"工作空间不存在: workspace_id={workspace_id}")
            return None
            
        business_logger.info(f"成功获取工作空间类型: {workspace.storage_type}")
        return workspace.storage_type
        
    except Exception as e:
        business_logger.error(f"获取工作空间类型失败: workspace_id={workspace_id} - {str(e)}")
        raise


def get_workspace_end_users(
    db: Session, 
    workspace_id: uuid.UUID, 
    current_user: User
) -> List[EndUser]:
    """获取工作空间的所有宿主"""
    business_logger.info(f"获取工作空间宿主列表: workspace_id={workspace_id}, 操作者: {current_user.username}")
    
    try:        
        # 查询应用（ORM）并转换为 Pydantic 模型
        apps_orm = app_repository.get_apps_by_workspace_id(db, workspace_id)
        apps = [AppSchema.model_validate(h) for h in apps_orm]
        app_ids = [app.id for app in apps]
        end_users = []
        for app_id in app_ids:
            end_user_orm_list = end_user_repository.get_end_users_by_app_id(db, app_id)
            end_users.extend([EndUserSchema.model_validate(h) for h in end_user_orm_list])
        
        business_logger.info(f"成功获取 {len(end_users)} 个宿主记录")
        return end_users
        
    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间宿主列表失败: workspace_id={workspace_id} - {str(e)}")
        raise


def get_workspace_memory_increment(
    db: Session, 
    workspace_id: uuid.UUID, 
    limit: int,
    current_user: User
) -> List[MemoryIncrementSchema]:
    """获取工作空间的记忆增量"""
    business_logger.info(f"获取工作空间记忆增量: workspace_id={workspace_id}, 操作者: {current_user.username}")
    
    try:        
        # 查询记忆增量
        memory_increment_orm_list = memory_increment_repository.get_memory_increments_by_workspace_id(db, workspace_id, limit)
        memory_increment = [MemoryIncrementSchema.model_validate(m) for m in memory_increment_orm_list]
        
        business_logger.info(f"成功获取 {len(memory_increment)} 条记忆增量记录")
        return memory_increment
        
    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间记忆增量失败: workspace_id={workspace_id} - {str(e)}")
        raise


def get_workspace_api_increment(
    db: Session, 
    workspace_id: uuid.UUID, 
    current_user: User
) -> int:
    """获取工作空间的API调用增量"""
    business_logger.info(f"获取工作空间API调用增量: workspace_id={workspace_id}, 操作者: {current_user.username}")
    
    try:        
        # 查询API调用增量
        api_increment = 856
        
        business_logger.info(f"成功获取 {api_increment} API调用增量")
        return api_increment
        
    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间API调用增量失败: workspace_id={workspace_id} - {str(e)}")
        raise


def write_workspace_total_memory(
    db: Session, 
    workspace_id: uuid.UUID, 
    current_user: User
) -> int:
    """写入工作空间的记忆总量"""
    business_logger.info(f"写入工作空间记忆总量: workspace_id={workspace_id}, 操作者: {current_user.username}")
    
    try:
        # 模拟记忆总量
        total_num = 1024

        # 写入记忆总量
        memory_increment_repository.write_memory_increment(db, workspace_id, total_num)
        
        business_logger.info(f"成功写入记忆总量 {total_num}")
        return total_num
        
    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"写入工作空间记忆总量失败: workspace_id={workspace_id} - {str(e)}")
        raise


def get_workspace_memory_list(
    db: Session, 
    workspace_id: uuid.UUID, 
    current_user: User,
    limit: int = 7
) -> dict:
    """
    获取工作空间的记忆列表（整合接口）
    
    整合以下三个接口的数据：
    1. total_memory - 工作空间记忆总量
    2. memory_increment - 工作空间记忆增量
    3. hosts - 工作空间宿主列表
    """
    business_logger.info(f"获取工作空间记忆列表: workspace_id={workspace_id}, 操作者: {current_user.username}")
    
    result = {}
    
    try:
        # 1. 获取记忆总量
        try:
            total_memory = write_workspace_total_memory(db, workspace_id, current_user)
            result["total_memory"] = total_memory
            business_logger.info(f"成功获取记忆总量: {total_memory}")
        except Exception as e:
            business_logger.warning(f"获取记忆总量失败: {str(e)}")
            result["total_memory"] = 0.0
        
        # 2. 获取记忆增量
        try:
            memory_increment = get_workspace_memory_increment(db, workspace_id, limit, current_user)
            result["memory_increment"] = memory_increment
            business_logger.info(f"成功获取 {len(memory_increment)} 条记忆增量记录")
        except Exception as e:
            business_logger.warning(f"获取记忆增量失败: {str(e)}")
            result["memory_increment"] = []
        
        # 3. 获取宿主列表
        try:
            hosts = get_workspace_end_users(db, workspace_id, current_user)
            result["hosts"] = hosts
            business_logger.info(f"成功获取 {len(hosts)} 个宿主记录")
        except Exception as e:
            business_logger.warning(f"获取宿主列表失败: {str(e)}")
            result["hosts"] = []
        
        business_logger.info("成功获取工作空间记忆列表")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间记忆列表失败: workspace_id={workspace_id} - {str(e)}")
        raise


def get_workspace_total_end_users(
    db: Session, 
    workspace_id: uuid.UUID, 
    current_user: User
) -> dict:
    """
    获取用户列表的总用户数
    """
    business_logger.info(f"获取用户列表的总用户数: workspace_id={workspace_id}, 操作者: {current_user.username}")
    
    try:
        # 复用原有的 get_workspace_end_users 逻辑
        end_users = get_workspace_end_users(db, workspace_id, current_user)
        
        business_logger.info(f"成功获取 {len(end_users)} 个宿主记录")
        return {
            "total_num": len(end_users),
            "online_num": len(end_users)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取用户列表失败: workspace_id={workspace_id} - {str(e)}")
        raise


async def get_workspace_total_memory_count(
    db: Session, 
    workspace_id: uuid.UUID, 
    current_user: User,
    end_user_id: str = None
) -> dict:
    """
    获取工作空间的记忆总量（通过聚合所有host的记忆数）
    
    逻辑：
    1. 从 memory_list 获取所有 host_id
    2. 对每个 host_id 调用 search_all 获取 total
    3. 将所有 total 求和返回
    """
    business_logger.info(f"获取工作空间记忆总量: workspace_id={workspace_id}, 操作者: {current_user.username}")
    
    try:
        # 1. 获取所有 hosts
        hosts = get_workspace_end_users(db, workspace_id, current_user)
        business_logger.info(f"获取到 {len(hosts)} 个宿主")
        
        if not hosts:
            business_logger.warning("未找到任何宿主，返回0")
            return {
                "total_memory_count": 0,
                "host_count": 0,
                "details": []
            }
        
        # 2. 对每个 host_id 调用 search_all 获取 total
        from app.services import memory_storage_service
        
        total_count = 0
        details = []
        
        # 如果提供了 end_user_id，只查询该用户
        if end_user_id:
            search_result = await memory_storage_service.search_all(end_user_id=end_user_id)
            return {
                "total_memory_count": search_result.get("total", 0),
                "host_count": 1,
                "details": [{"end_user_id": end_user_id, "count": search_result.get("total", 0)}]
            }
        
        for host in hosts:
            try:
                end_user_id_str = str(host.id)
                
                search_result = await memory_storage_service.search_all(
                    end_user_id=end_user_id_str
                )
                
                host_total = search_result.get("total", 0)
                total_count += host_total
                
                details.append({
                    "end_user_id": end_user_id_str,
                    "count": host_total
                })
                
                business_logger.debug(f"EndUser {end_user_id_str} 记忆数: {host_total}")
                
            except Exception as e:
                business_logger.warning(f"获取 end_user {host.id} 记忆数失败: {str(e)}")
                # 失败的 host 记为 0
                details.append({
                    "end_user_id": str(host.id),
                    "count": 0
                })
        
        result = {
            "total_memory_count": total_count,
            "host_count": len(hosts),
            "details": details
        }
        
        business_logger.info(f"成功获取工作空间记忆总量: {total_count} (来自 {len(hosts)} 个宿主)")
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间记忆总量失败: workspace_id={workspace_id} - {str(e)}")
        raise


# ======== RAG 相关服务 ========
def get_rag_total_doc(
    db: Session, 
    current_user: User
) -> int:
    """
    根据当前用户所在的workspace_id查询konwledges表所有doc_num的总和
    """
    workspace_id = current_user.current_workspace_id
    business_logger.info(f"获取RAG总文档数: workspace_id={workspace_id}, 操作者: {current_user.username}")
    
    try:
        total_doc = knowledge_repository.get_total_doc_num_by_workspace(db, workspace_id)
        business_logger.info(f"成功获取RAG总文档数: {total_doc}")
        return total_doc
    except Exception as e:
        business_logger.error(f"获取RAG总文档数失败: workspace_id={workspace_id} - {str(e)}")
        raise


def get_rag_total_chunk(
    db: Session,
    current_user: User
) -> int:
    """
    根据当前用户所在的workspace_id查询konwledges表所有chunk_num的总和
    """
    workspace_id = current_user.current_workspace_id
    business_logger.info(f"获取RAG总chunk数: workspace_id={workspace_id}, 操作者: {current_user.username}")
    
    try:
        total_chunk = knowledge_repository.get_total_chunk_num_by_workspace(db, workspace_id)
        business_logger.info(f"成功获取RAG总chunk数: {total_chunk}")
        return total_chunk
    except Exception as e:
        business_logger.error(f"获取RAG总chunk数失败: workspace_id={workspace_id} - {str(e)}")
        raise


def get_rag_total_kb(
    db: Session,
    current_user: User
) -> int:
    """
    根据当前用户所在的workspace_id查询konwledges表所有不同id的数量
    """
    workspace_id = current_user.current_workspace_id
    business_logger.info(f"获取RAG总知识库数: workspace_id={workspace_id}, 操作者: {current_user.username}")
    
    try:
        total_kb = knowledge_repository.get_total_kb_count_by_workspace(db, workspace_id)
        business_logger.info(f"成功获取RAG总知识库数: {total_kb}")
        return total_kb
    except Exception as e:
        business_logger.error(f"获取RAG总知识库数失败: workspace_id={workspace_id} - {str(e)}")
        raise

def get_current_user_total_chunk(
    end_user_id: str,
    db: Session,
    current_user: User
) -> int:
    """
    计算documents表中file_name=='end_user_id'+'.txt'的所有记录chunk_num的总和
    """
    business_logger.info(f"获取用户总chunk数: end_user_id={end_user_id}, 操作者: {current_user.username}")
    
    try:
        from app.models.document_model import Document
        from sqlalchemy import func
        
        # 构造文件名
        file_name = f"{end_user_id}.txt"
        
        # 查询并求和
        total_chunk = db.query(func.sum(Document.chunk_num)).filter(
            Document.file_name == file_name
        ).scalar() or 0
        
        business_logger.info(f"成功获取用户总chunk数: {total_chunk} (file_name={file_name})")
        return int(total_chunk)
        
    except Exception as e:
        business_logger.error(f"获取用户总chunk数失败: end_user_id={end_user_id} - {str(e)}")
        raise

def get_rag_content(
    end_user_id: str,
    limit: int,
    db: Session,
    current_user: User
) -> dict:
    """
    先在documents表中查询file_name=='end_user_id'+'.txt'的id和kb_id,
    然后调用/chunks/{kb_id}/{document_id}/chunks接口的相关代码获取所有内容，
    接着对获取的内容进行提取，只要page_content的内容，
    最后返回数据
    """
    business_logger.info(f"获取RAG内容: end_user_id={end_user_id}, limit={limit}, 操作者: {current_user.username}")
    
    try:
        from app.models.document_model import Document
        from app.core.rag.vdb.elasticsearch.elasticsearch_vector import ElasticSearchVectorFactory
        
        # 1. 构造文件名
        file_name = f"{end_user_id}.txt"
        
        # 2. 查询documents表获取id和kb_id
        documents = db.query(Document).filter(
            Document.file_name == file_name
        ).all()
        
        if not documents:
            business_logger.warning(f"未找到文件: {file_name}")
            return {
                "total": 0,
                "contents": []
            }
        
        business_logger.info(f"找到 {len(documents)} 个文档记录")
        
        # 3. 获取所有chunks的page_content
        all_contents = []
        total_chunks = 0
        
        for document in documents:
            try:
                # 获取知识库信息
                kb = knowledge_repository.get_knowledge_by_id(db, document.kb_id)
                if not kb:
                    business_logger.warning(f"知识库不存在: kb_id={document.kb_id}")
                    continue
                
                # 初始化向量服务
                vector_service = ElasticSearchVectorFactory().init_vector(knowledge=kb)
                
                # 获取该文档的所有chunks（分页获取）
                page = 1
                pagesize = 100  # 每页100条
                
                while True:
                    total, items = vector_service.search_by_segment(
                        document_id=str(document.id),
                        query=None,
                        pagesize=pagesize,
                        page=page,
                        asc=True
                    )
                    
                    if not items:
                        break
                    
                    # 提取page_content
                    for item in items:
                        all_contents.append(item.page_content)
                        total_chunks += 1
                        
                        # # 如果达到limit限制，直接返回
                        # if limit > 0 and total_chunks >= limit:
                        #     business_logger.info(f"已达到limit限制: {limit}")
                        #     return {
                        #         "total": total_chunks,
                        #         "contents": all_contents[:limit]
                        #     }
                    
                    # 检查是否还有下一页
                    if page * pagesize >= total:
                        break
                    
                    page += 1
                
                business_logger.info(f"文档 {document.id} 获取了 {len(items)} 个chunks")
                
            except Exception as e:
                business_logger.error(f"获取文档 {document.id} 的chunks失败: {str(e)}")
                continue
        
        # 4. 返回结果
        result = {
            "total": total_chunks,
            "contents": all_contents[:limit] if limit > 0 else all_contents
        }
        
        business_logger.info(f"成功获取RAG内容: total={total_chunks}, 返回={len(result['contents'])} 条")
        return result
        
    except Exception as e:
        business_logger.error(f"获取RAG内容失败: end_user_id={end_user_id} - {str(e)}")
        raise


async def get_chunk_summary_and_tags(
    end_user_id: str,
    limit: int,
    max_tags: int,
    db: Session,
    current_user: User
) -> dict:
    """
    获取chunk的总结、标签和人物形象
    
    Args:
        end_user_id: 宿主ID
        limit: 返回的chunk数量限制
        max_tags: 最大标签数量
        db: 数据库会话
        current_user: 当前用户
    
    Returns:
        包含summary、tags和personas的字典
    """
    business_logger.info(f"获取chunk摘要、标签和人物形象: end_user_id={end_user_id}, limit={limit}, 操作者: {current_user.username}")
    
    try:
        # 1. 获取chunk内容
        rag_content = get_rag_content(end_user_id, limit, db, current_user)
        chunks = rag_content.get("contents", [])
        
        if not chunks:
            business_logger.warning(f"未找到chunk内容: end_user_id={end_user_id}")
            return {
                "summary": "暂无内容",
                "tags": [],
                "personas": []
            }
        
        # 2. 导入RAG工具函数
        from app.core.rag_utils import generate_chunk_summary, extract_chunk_tags, extract_chunk_persona
        
        # 3. 并发生成摘要、提取标签和人物形象
        import asyncio
        summary_task = generate_chunk_summary(chunks, max_chunks=limit)
        tags_task = extract_chunk_tags(chunks, max_tags=max_tags, max_chunks=limit)
        personas_task = extract_chunk_persona(chunks, max_personas=5, max_chunks=limit)
        
        summary, tags_with_freq, personas = await asyncio.gather(summary_task, tags_task, personas_task)
        
        # 4. 格式化标签数据
        tags = [{"tag": tag, "frequency": freq} for tag, freq in tags_with_freq]
        
        result = {
            "summary": summary,
            "tags": tags,
            "personas": personas
        }
        
        business_logger.info(f"成功获取chunk摘要、{len(tags)} 个标签和 {len(personas)} 个人物形象")
        return result
        
    except Exception as e:
        business_logger.error(f"获取chunk摘要、标签和人物形象失败: end_user_id={end_user_id} - {str(e)}")
        raise


async def get_chunk_insight(
    end_user_id: str,
    limit: int,
    db: Session,
    current_user: User
) -> dict:
    """
    获取chunk的洞察分析
    
    Args:
        end_user_id: 宿主ID
        limit: 返回的chunk数量限制
        db: 数据库会话
        current_user: 当前用户
    
    Returns:
        包含insight的字典
    """
    business_logger.info(f"获取chunk洞察: end_user_id={end_user_id}, limit={limit}, 操作者: {current_user.username}")
    
    try:
        # 1. 获取chunk内容
        rag_content = get_rag_content(end_user_id, limit, db, current_user)
        chunks = rag_content.get("contents", [])
        
        if not chunks:
            business_logger.warning(f"未找到chunk内容: end_user_id={end_user_id}")
            return {
                "insight": "暂无足够数据生成洞察报告"
            }
        
        # 2. 导入RAG工具函数
        from app.core.rag_utils import generate_chunk_insight
        
        # 3. 生成洞察
        insight = await generate_chunk_insight(chunks, max_chunks=limit)
        
        result = {
            "insight": insight
        }
        
        business_logger.info("成功获取chunk洞察")
        return result
        
    except Exception as e:
        business_logger.error(f"获取chunk洞察失败: end_user_id={end_user_id} - {str(e)}")
        raise
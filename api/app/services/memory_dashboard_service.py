from sqlalchemy.orm import Session
from sqlalchemy import desc, nullslast, or_, and_, cast, String
from typing import List, Optional, Dict, Any
import uuid
from fastapi import HTTPException

from app.models.user_model import User
from app.models.app_model import App
from app.models.end_user_model import EndUser, EndUser as EndUserModel
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
    """获取工作空间的所有宿主（优化版本：减少数据库查询次数）
    返回结果按 created_at 从新到旧排序（NULL 值排在最后）
    """
    business_logger.info(f"获取工作空间宿主列表: workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        # 查询应用（ORM）
        apps_orm = app_repository.get_apps_by_workspace_id(db, workspace_id)

        if not apps_orm:
            business_logger.info("工作空间下没有应用")
            return []

        # 提取所有 app_id
        # app_ids = [app.id for app in apps_orm]
        # 批量查询所有 end_users（一次查询而非循环查询）
        # 按 created_at 降序排序，NULL 值排在最后；id 作为次级排序键保证确定性
        end_users_orm = db.query(EndUserModel).filter(
            EndUserModel.workspace_id == workspace_id
        ).order_by(
            nullslast(desc(EndUserModel.created_at)),
            desc(EndUserModel.id)
        ).all()

        # 转换为 Pydantic 模型（只在需要时转换）
        end_users = [EndUserSchema.model_validate(eu) for eu in end_users_orm]

        business_logger.info(f"成功获取 {len(end_users)} 个宿主记录")
        return end_users

    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间宿主列表失败: workspace_id={workspace_id} - {str(e)}")
        raise


def get_workspace_end_users_paginated(
    db: Session,
    workspace_id: uuid.UUID,
    current_user: User,
    page: int,
    pagesize: int,
    keyword: Optional[str] = None
) -> Dict[str, Any]:
    """获取工作空间的宿主列表（分页版本，支持模糊搜索）

    返回结果按 created_at 从新到旧排序（NULL 值排在最后）
    支持通过 keyword 参数同时模糊搜索 other_name 和 id 字段

    Args:
        db: 数据库会话
        workspace_id: 工作空间ID
        current_user: 当前用户
        page: 页码（从1开始）
        pagesize: 每页数量
        keyword: 搜索关键词（可选，同时模糊匹配 other_name 和 id）

    Returns:
        dict: 包含 items（宿主列表）和 total（总记录数）的字典
    """
    business_logger.info(f"获取工作空间宿主列表（分页）: workspace_id={workspace_id}, keyword={keyword}, page={page}, pagesize={pagesize}, 操作者: {current_user.username}")

    try:
        # 构建基础查询
        base_query = db.query(EndUserModel).filter(
            EndUserModel.workspace_id == workspace_id
        )

        # 构建搜索条件（过滤空字符串和None）
        keyword = keyword.strip() if keyword else None

        if keyword:
            keyword_pattern = f"%{keyword}%"
            # other_name 匹配始终生效；id 匹配仅对 other_name 为空的记录生效
            base_query = base_query.filter(
                or_(
                    EndUserModel.other_name.ilike(keyword_pattern),
                    and_(
                        or_(
                            EndUserModel.other_name.is_(None),
                            EndUserModel.other_name == "",
                        ),
                        cast(EndUserModel.id, String).ilike(keyword_pattern),
                    ),
                )
            )
            business_logger.info(f"应用模糊搜索: keyword={keyword}（匹配 other_name；other_name 为空时匹配 id）")

        # 获取总记录数
        total = base_query.count()

        if total == 0:
            business_logger.info("工作空间下没有宿主")
            return {"items": [], "total": 0}

        # 分页查询
        # 按 created_at 降序排序，NULL 值排在最后；id 作为次级排序键保证确定性
        end_users_orm = base_query.order_by(
            nullslast(desc(EndUserModel.created_at)),
            desc(EndUserModel.id)
        ).offset((page - 1) * pagesize).limit(pagesize).all()

        # 转换为 Pydantic 模型
        end_users = [EndUserSchema.model_validate(eu) for eu in end_users_orm]

        business_logger.info(f"成功获取 {len(end_users)} 个宿主记录，总计 {total} 条")
        return {"items": end_users, "total": total}

    except HTTPException:
        raise
    except Exception as e:
        business_logger.error(f"获取工作空间宿主列表（分页）失败: workspace_id={workspace_id} - {str(e)}")
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
        
        # 2. 使用 search_all_batch 批量查询所有宿主的记忆数量
        from app.services import memory_storage_service
        
        # 如果提供了 end_user_id，只查询该用户
        if end_user_id:
            batch_result = await memory_storage_service.search_all_batch([end_user_id])
            count = batch_result.get(end_user_id, 0)
            # 查询用户名称
            from app.repositories.end_user_repository import EndUserRepository
            repo = EndUserRepository(db)
            end_user = repo.get_by_id(uuid.UUID(end_user_id))
            user_name = end_user.other_name if end_user else None
            
            return {
                "total_memory_count": count,
                "host_count": 1,
                "details": [{
                    "end_user_id": end_user_id, 
                    "count": count,
                    "name": user_name
                }]
            }
        
        # 批量查询所有宿主记忆数量（一次 Neo4j 查询）
        end_user_ids = [str(host.id) for host in hosts]
        batch_result = await memory_storage_service.search_all_batch(end_user_ids)
        
        # 构建 host name 映射
        host_name_map = {str(host.id): host.other_name for host in hosts}
        
        total_count = sum(batch_result.values())
        details = [
            {
                "end_user_id": uid,
                "count": batch_result.get(uid, 0),
                "name": host_name_map.get(uid)
            }
            for uid in end_user_ids
        ]
        
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
    根据当前用户所在的workspace_id查询konwledges表中排除用户知识库（permission_id!='Memory'）的数量
    """
    workspace_id = current_user.current_workspace_id
    business_logger.info(f"获取RAG总知识库数(排除用户知识库): workspace_id={workspace_id}, 操作者: {current_user.username}")
    
    try:
        total_kb = knowledge_repository.get_non_user_kb_count_by_workspace(db, workspace_id)
        business_logger.info(f"成功获取RAG总知识库数: {total_kb}")
        return total_kb
    except Exception as e:
        business_logger.error(f"获取RAG总知识库数失败: workspace_id={workspace_id} - {str(e)}")
        raise


def get_rag_user_kb_total_chunk(
    db: Session,
    current_user: User
) -> int:
    """
    根据当前用户所在的workspace_id，从documents表统计所有用户知识库的chunk总数。
    与 /end_users 接口保持同源：查询 file_name 匹配 end_user_id.txt 的文档 chunk_num 之和。
    """
    workspace_id = current_user.current_workspace_id
    business_logger.info(f"获取用户知识库总chunk数(documents表): workspace_id={workspace_id}, 操作者: {current_user.username}")

    try:
        from app.models.document_model import Document
        from app.models.end_user_model import EndUser
        from app.models.app_model import App
        from sqlalchemy import func

        # 通过 App 关联取该 workspace 下所有 end_user_id
        end_user_ids = [
            str(eid) for (eid,) in db.query(EndUser.id)
            .join(App, EndUser.app_id == App.id)
            .filter(App.workspace_id == workspace_id)
            .all()
        ]
        if not end_user_ids:
            return 0

        file_names = [f"{uid}.txt" for uid in end_user_ids]
        result = db.query(func.sum(Document.chunk_num)).filter(
            Document.file_name.in_(file_names)
        ).scalar()

        total_chunk = int(result or 0)
        business_logger.info(f"成功获取用户知识库总chunk数: {total_chunk}")
        return total_chunk
    except Exception as e:
        business_logger.error(f"获取用户知识库总chunk数失败: workspace_id={workspace_id} - {str(e)}")
        raise

def get_dashboard_yesterday_changes(
    db: Session,
    workspace_id: uuid.UUID,
    storage_type: str,
    today_data: dict
) -> dict:
    """
    计算各指标相比昨天的变化百分比。

    - total_app_change / total_knowledge_change：只看活跃记录，
      百分比 = (截止今日活跃总量 - 截止昨日活跃总量) / 截止昨日活跃总量
    - total_memory_change / total_api_call_change：
      百分比 = (今日总量 - 昨日总量) / 昨日总量

    昨日总量为 0 时返回 None。返回值为浮点数，例如 0.5 表示增长 50%。

    Args:
        db: 数据库会话
        workspace_id: 工作空间ID
        storage_type: 存储类型 'neo4j' | 'rag'
        today_data: 当前数据，包含 total_memory, total_app, total_knowledge, total_api_call

    Returns:
        {
            "total_memory_change": float | None,
            "total_app_change": float | None,
            "total_knowledge_change": float | None,
            "total_api_call_change": float | None
        }
    """
    from datetime import datetime
    from sqlalchemy import func
    from app.models.api_key_model import ApiKey, ApiKeyLog
    from app.models.knowledge_model import Knowledge
    from app.models.app_model import App
    from app.models.appshare_model import AppShare

    business_logger.info(f"计算昨日对比百分比: workspace_id={workspace_id}, storage_type={storage_type}")

    now_local = datetime.now()
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)

    changes = {
        "total_memory_change": None,
        "total_app_change": None,
        "total_knowledge_change": None,
        "total_api_call_change": None,
    }

    def _calc_percentage(today_val, yesterday_val):
        """计算百分比，昨日为0时返回None"""
        if yesterday_val is None or yesterday_val == 0:
            return None
        return round((today_val - yesterday_val) / yesterday_val, 4)

    # --- total_api_call_change: (截止今日累计总数 - 截止昨日累计总数) / 截止昨日累计总数 ---
    try:
        api_key_ids = [
            row[0] for row in db.query(ApiKey.id).filter(
                ApiKey.workspace_id == workspace_id
            ).all()
        ]
        if api_key_ids:
            # 截止今日的累计调用总数
            total_api_until_now = db.query(func.count(ApiKeyLog.id)).filter(
                ApiKeyLog.api_key_id.in_(api_key_ids),
                ApiKeyLog.created_at < now_local
            ).scalar() or 0
            # 截止昨日的累计调用总数（today_start 即昨日结束）
            total_api_until_yesterday = db.query(func.count(ApiKeyLog.id)).filter(
                ApiKeyLog.api_key_id.in_(api_key_ids),
                ApiKeyLog.created_at < today_start
            ).scalar() or 0
            changes["total_api_call_change"] = _calc_percentage(total_api_until_now, total_api_until_yesterday)
        else:
            changes["total_api_call_change"] = None
    except Exception as e:
        business_logger.warning(f"计算API调用昨日对比失败: {str(e)}")

    # --- total_knowledge_change: 只看活跃(status=1)且为顶层知识库(parent_id=workspace_id)，百分比 = (今日活跃总量 - 昨日活跃总量) / 昨日活跃总量 ---
    try:
        # 截止今日的活跃知识库总量（当前 status=1，parent_id=workspace_id）
        today_knowledge = db.query(func.count(Knowledge.id)).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1,
            Knowledge.parent_id == Knowledge.workspace_id
        ).scalar() or 0
        # 截止昨日的活跃知识库总量（昨日之前创建的、当前仍 status=1，parent_id=workspace_id）
        yesterday_knowledge = db.query(func.count(Knowledge.id)).filter(
            Knowledge.workspace_id == workspace_id,
            Knowledge.status == 1,
            Knowledge.parent_id == Knowledge.workspace_id,
            Knowledge.created_at < today_start
        ).scalar() or 0

        changes["total_knowledge_change"] = _calc_percentage(today_knowledge, yesterday_knowledge)
    except Exception as e:
        business_logger.warning(f"计算知识库昨日对比失败: {str(e)}")

    # --- total_app_change: 只看活跃(is_active=True)，百分比 = (今日活跃总量 - 昨日活跃总量) / 昨日活跃总量 ---
    try:
        # === 自有app ===
        today_own_apps = db.query(func.count(App.id)).filter(
            App.workspace_id == workspace_id,
            App.is_active == True
        ).scalar() or 0
        yesterday_own_apps = db.query(func.count(App.id)).filter(
            App.workspace_id == workspace_id,
            App.is_active == True,
            App.created_at < today_start
        ).scalar() or 0

        # === 被分享app ===
        today_shared_apps = db.query(func.count(AppShare.id)).filter(
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active == True
        ).scalar() or 0
        yesterday_shared_apps = db.query(func.count(AppShare.id)).filter(
            AppShare.target_workspace_id == workspace_id,
            AppShare.is_active == True,
            AppShare.created_at < today_start
        ).scalar() or 0

        today_total_app = today_own_apps + today_shared_apps
        yesterday_total_app = yesterday_own_apps + yesterday_shared_apps

        changes["total_app_change"] = _calc_percentage(today_total_app, yesterday_total_app)
    except Exception as e:
        business_logger.warning(f"计算应用数量昨日对比失败: {str(e)}")

    # --- total_memory_change: (今日总量 - 昨日总量) / 昨日总量 ---
    try:
        today_memory = today_data.get("total_memory")
        if today_memory is None:
            changes["total_memory_change"] = None
        elif storage_type == "neo4j":
            last_record = db.query(MemoryIncrement).filter(
                MemoryIncrement.workspace_id == workspace_id,
                MemoryIncrement.created_at < today_start
            ).order_by(desc(MemoryIncrement.created_at)).first()
            if last_record is None or last_record.total_num == 0:
                changes["total_memory_change"] = None
            else:
                changes["total_memory_change"] = _calc_percentage(today_memory, last_record.total_num)
        elif storage_type == "rag":
            from app.models.document_model import Document
            from app.models.end_user_model import EndUser as _EndUser
            from app.models.app_model import App as _App

            end_user_ids = [
                str(eid) for (eid,) in db.query(_EndUser.id)
                .join(_App, _EndUser.app_id == _App.id)
                .filter(_App.workspace_id == workspace_id)
                .all()
            ]
            if not end_user_ids:
                changes["total_memory_change"] = None
            else:
                file_names = [f"{uid}.txt" for uid in end_user_ids]
                yesterday_chunk = int(db.query(func.sum(Document.chunk_num)).filter(
                    Document.file_name.in_(file_names),
                    Document.created_at < today_start
                ).scalar() or 0)
                if yesterday_chunk == 0:
                    changes["total_memory_change"] = None
                else:
                    changes["total_memory_change"] = _calc_percentage(today_memory, yesterday_chunk)
    except Exception as e:
        business_logger.warning(f"计算记忆总量昨日对比失败: {str(e)}")

    business_logger.info(f"昨日对比百分比计算完成: {changes}")
    return changes


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


def get_users_total_chunk_batch(
    end_user_ids: List[str],
    db: Session,
    current_user: User
) -> dict:
    """
    批量获取多个用户的总chunk数（性能优化版本）
    
    Args:
        end_user_ids: 用户ID列表
        db: 数据库会话
        current_user: 当前用户
        
    Returns:
        字典，key为end_user_id，value为chunk总数
        格式: {"user_id_1": 100, "user_id_2": 50, ...}
    """
    business_logger.info(f"批量获取 {len(end_user_ids)} 个用户的总chunk数, 操作者: {current_user.username}")
    
    try:
        from app.models.document_model import Document
        from sqlalchemy import func, case
        
        if not end_user_ids:
            return {}
        
        # 构造所有文件名
        file_names = [f"{user_id}.txt" for user_id in end_user_ids]
        
        # 一次查询获取所有用户的chunk总数
        # 使用 GROUP BY file_name 来分组统计
        results = db.query(
            Document.file_name,
            func.sum(Document.chunk_num).label('total_chunk')
        ).filter(
            Document.file_name.in_(file_names)
        ).group_by(
            Document.file_name
        ).all()
        
        # 构建结果字典
        chunk_map = {}
        for file_name, total_chunk in results:
            # 从文件名中提取 end_user_id (去掉 .txt 后缀)
            user_id = file_name.replace('.txt', '')
            chunk_map[user_id] = int(total_chunk or 0)
        
        # 对于没有记录的用户，设置为0
        for user_id in end_user_ids:
            if user_id not in chunk_map:
                chunk_map[user_id] = 0
        
        business_logger.info(f"成功批量获取 {len(chunk_map)} 个用户的总chunk数")
        return chunk_map
        
    except Exception as e:
        business_logger.error(f"批量获取用户总chunk数失败: {str(e)}")
        raise


def get_rag_content(
    end_user_id: str,
    page: int,
    pagesize: int,
    db: Session,
    current_user: User
) -> dict:
    """
    先在documents表中查询file_name=='end_user_id'+'.txt'的id和kb_id,
    然后调用/chunks/{kb_id}/{document_id}/chunks接口的相关代码获取所有内容，
    接着对获取的内容进行提取，只要page_content的内容，
    最后返回分页数据
    """
    business_logger.info(f"获取RAG内容: end_user_id={end_user_id}, page={page}, pagesize={pagesize}, 操作者: {current_user.username}")
    
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
                "page": {
                    "page": page,
                    "pagesize": pagesize,
                    "hasnext": False,
                },
                "items": []
            }
        
        business_logger.info(f"找到 {len(documents)} 个文档记录")
        
        # 3. 按全局偏移量计算当前页数据
        # 全局偏移范围：[offset_start, offset_end)
        offset_start = (page - 1) * pagesize
        offset_end = offset_start + pagesize
        
        global_total = 0    # 所有文档的 chunk 总数
        page_contents = []  # 当前页的内容
        
        for document in documents:
            try:
                kb = knowledge_repository.get_knowledge_by_id(db, document.kb_id)
                if not kb:
                    business_logger.warning(f"知识库不存在: kb_id={document.kb_id}")
                    continue
                
                vector_service = ElasticSearchVectorFactory().init_vector(knowledge=kb)
                
                # 先用 pagesize=1 获取该文档的 chunk 总数
                doc_total, _ = vector_service.search_by_segment(
                    document_id=str(document.id),
                    query=None,
                    pagesize=1,
                    page=1,
                    asc=True
                )
                
                doc_offset_start = global_total            # 该文档在全局中的起始偏移
                doc_offset_end = global_total + doc_total  # 该文档在全局中的结束偏移
                global_total += doc_total
                
                # 当前页与该文档无交集，跳过
                if doc_offset_end <= offset_start or doc_offset_start >= offset_end:
                    continue
                
                # 计算需要从该文档取的局部范围
                local_start = max(offset_start - doc_offset_start, 0)
                local_end = min(offset_end - doc_offset_start, doc_total)
                need_count = local_end - local_start
                
                # 换算成 ES 分页参数（ES page 从1开始）
                es_page = (local_start // pagesize) + 1
                es_offset_in_page = local_start % pagesize
                
                fetched = []
                while len(fetched) < es_offset_in_page + need_count:
                    _, items = vector_service.search_by_segment(
                        document_id=str(document.id),
                        query=None,
                        pagesize=pagesize,
                        page=es_page,
                        asc=True
                    )
                    if not items:
                        break
                    fetched.extend(items)
                    es_page += 1
                
                slice_items = fetched[es_offset_in_page: es_offset_in_page + need_count]
                page_contents.extend([item.page_content for item in slice_items])
                
            except Exception as e:
                business_logger.error(f"获取文档 {document.id} 的chunks失败: {str(e)}")
                continue
        
        # 4. 将所有 page_content 拼接后按角色分割为对话列表
        merged_text = "\n".join(page_contents)
        conversations = []
        if merged_text.strip():
            import re
            # 在任意位置匹配 "user:" 或 "assistant:"，不限于行首
            parts = re.split(r'(user|assistant):', merged_text)
            # parts 结构: ['', 'user', ' content...', 'assistant', ' content...', ...]
            i = 1
            while i < len(parts) - 1:
                role = parts[i].strip()
                content = parts[i + 1].strip()
                # 将 content 中的 \n 还原为真实换行
                content = content.replace("\\n", "\n")
                if role in ("user", "assistant") and content:
                    conversations.append({"role": role, "content": content})
                i += 2

        result = {
            "page": {
                "page": page,
                "pagesize": pagesize,
                "hasnext": offset_end < global_total,
            },
            "items": conversations
        }
        
        business_logger.info(f"成功获取RAG内容: page={page}, 返回={len(conversations)} 条对话")
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
    纯读库：从end_user表返回RAG摘要、标签和人物形象缓存。
    无数据时返回空结构，不触发LLM生成。
    """
    import json
    from app.repositories.end_user_repository import EndUserRepository

    business_logger.info(f"读取chunk摘要/标签/人物形象缓存: end_user_id={end_user_id}")

    repo = EndUserRepository(db)
    end_user = repo.get_by_id(uuid.UUID(end_user_id))

    if not end_user:
        return {"summary": "", "tags": [], "personas": [], "generated": False}

    return {
        "summary": end_user.user_summary or "",
        "tags": json.loads(end_user.rag_tags) if end_user.rag_tags else [],
        "personas": json.loads(end_user.rag_personas) if end_user.rag_personas else [],
        "generated": bool(end_user.user_summary),
    }


async def get_chunk_insight(
    end_user_id: str,
    limit: int,
    db: Session,
    current_user: User
) -> dict:
    """
    纯读库：从end_user表返回RAG洞察缓存。
    无数据时返回空结构，不触发LLM生成。
    """
    from app.repositories.end_user_repository import EndUserRepository

    business_logger.info(f"读取chunk洞察缓存: end_user_id={end_user_id}")

    repo = EndUserRepository(db)
    end_user = repo.get_by_id(uuid.UUID(end_user_id))

    if not end_user:
        return {"insight": "", "behavior_pattern": "", "key_findings": "", "growth_trajectory": "", "generated": False}

    return {
        "insight": end_user.memory_insight or "",
        "behavior_pattern": end_user.behavior_pattern or "",
        "key_findings": end_user.key_findings or "",
        "growth_trajectory": end_user.growth_trajectory or "",
        "generated": bool(end_user.memory_insight),
    }


async def generate_rag_profile(
    end_user_id: str,
    limit: int,
    max_tags: int,
    db: Session,
    current_user: User,
) -> dict:
    """
    生产接口：为RAG存储模式的end_user全量重新生成并持久化完整画像数据。
    每次调用都会重新生成，覆盖已有数据。

    生成内容：
      - user_summary / rag_tags / rag_personas
      - memory_insight / behavior_pattern / key_findings / growth_trajectory
    """
    import json
    import asyncio
    from app.repositories.end_user_repository import EndUserRepository
    from app.core.rag_utils import (
        generate_chunk_summary,
        extract_chunk_tags,
        extract_chunk_persona,
        generate_chunk_insight_sections,
    )

    business_logger.info(f"开始生产RAG画像: end_user_id={end_user_id}, 操作者: {current_user.username}")

    repo = EndUserRepository(db)
    end_user = repo.get_by_id(uuid.UUID(end_user_id))

    if not end_user:
        raise ValueError(f"end_user {end_user_id} 不存在")

    rag_content = get_rag_content(end_user_id, page=1, pagesize=limit, db=db, current_user=current_user)
    chunks = rag_content.get("items", [])

    if not chunks:
        business_logger.warning(f"未找到chunk内容，无法生产RAG画像: end_user_id={end_user_id}")
        raise ValueError("暂无chunk内容，无法生成画像")

    summary, tags_with_freq, personas, insight_sections = await asyncio.gather(
        generate_chunk_summary(chunks, max_chunks=limit, end_user_id=end_user_id),
        extract_chunk_tags(chunks, max_tags=max_tags, max_chunks=limit, end_user_id=end_user_id),
        extract_chunk_persona(chunks, max_personas=5, max_chunks=limit, end_user_id=end_user_id),
        generate_chunk_insight_sections(chunks, max_chunks=limit, end_user_id=end_user_id),
    )

    tags = [{"tag": tag, "frequency": freq} for tag, freq in tags_with_freq]

    repo.update_rag_summary_tags(
        end_user_id=end_user.id,
        user_summary=summary,
        rag_tags=json.dumps(tags, ensure_ascii=False),
        rag_personas=json.dumps(personas, ensure_ascii=False),
    )

    repo.update_memory_insight(
        end_user_id=end_user.id,
        memory_insight=insight_sections.get("memory_insight", ""),
        behavior_pattern=insight_sections.get("behavior_pattern", ""),
        key_findings=insight_sections.get("key_findings", ""),
        growth_trajectory=insight_sections.get("growth_trajectory", ""),
    )

    business_logger.info(f"RAG画像生产完成: end_user_id={end_user_id}, tags={len(tags)}, personas={len(personas)}")

    return {
        "end_user_id": end_user_id,
        "summary_length": len(summary),
        "tags_count": len(tags),
        "personas_count": len(personas),
        "insight_generated": bool(insight_sections.get("memory_insight")),
    }


def get_dashboard_common_stats(db: Session, workspace_id) -> dict:
    """
    获取 dashboard 中 neo4j/rag 分支共享的统计数据：
    total_app、total_knowledge、total_api_call

    Returns:
        dict: {"total_app": int, "total_knowledge": int, "total_api_call": int}
    """
    result = {"total_app": 0, "total_knowledge": 0, "total_api_call": 0}

    # total_app: 统计当前空间下的所有app数量（包含自有 + 被分享给本工作空间的app）
    try:
        from app.services import app_service as _app_svc
        _, total_app = _app_svc.AppService(db).list_apps(
            workspace_id=workspace_id, include_shared=True, pagesize=1
        )
        result["total_app"] = total_app
    except Exception as e:
        business_logger.warning(f"获取应用数量失败: {e}")

    # total_knowledge: 统计顶层知识库（parent_id = workspace_id）
    try:
        from sqlalchemy import func as _func
        from app.models.knowledge_model import Knowledge as _Knowledge
        total_knowledge = db.query(_func.count(_Knowledge.id)).filter(
            _Knowledge.workspace_id == workspace_id,
            _Knowledge.status == 1,
            _Knowledge.parent_id == _Knowledge.workspace_id
        ).scalar() or 0
        result["total_knowledge"] = total_knowledge
    except Exception as e:
        business_logger.warning(f"获取知识库数量失败: {e}")

    # total_api_call: 截止当前的历史累计调用总数
    try:
        from sqlalchemy import func as _api_func
        from app.models.api_key_model import ApiKey as _ApiKey, ApiKeyLog as _ApiKeyLog

        _api_key_ids = [
            row[0] for row in db.query(_ApiKey.id).filter(
                _ApiKey.workspace_id == workspace_id
            ).all()
        ]
        if _api_key_ids:
            total_api_calls = db.query(_api_func.count(_ApiKeyLog.id)).filter(
                _ApiKeyLog.api_key_id.in_(_api_key_ids)
            ).scalar() or 0
        else:
            total_api_calls = 0
        result["total_api_call"] = total_api_calls
    except Exception as e:
        business_logger.warning(f"获取API调用统计失败: {e}")

    return result

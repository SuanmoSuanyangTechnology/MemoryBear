import asyncio
import uuid
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional
from app.core.response_utils import success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.response_schema import ApiResponse

from app.services import memory_dashboard_service, memory_storage_service, workspace_service
from app.services.memory_agent_service import get_end_users_connected_configs_batch
from app.services.app_statistics_service import AppStatisticsService
from app.core.logging_config import get_api_logger

# 获取API专用日志器
api_logger = get_api_logger()

router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"],
    dependencies=[Depends(get_current_user)] # Apply auth to all routes in this controller
)


@router.get("/total_end_users", response_model=ApiResponse)
def get_workspace_total_end_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取用户列表的总用户数
    """
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的宿主列表")
    total_end_users = memory_dashboard_service.get_workspace_total_end_users(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user
    )
    api_logger.info(f"成功获取最新用户总数: total_num={total_end_users.get('total_num', 0)}")
    return success(data=total_end_users, msg="用户数量获取成功")





@router.get("/end_users", response_model=ApiResponse)
async def get_workspace_end_users(
    workspace_id: Optional[uuid.UUID] = Query(None, description="工作空间ID（可选，默认当前用户工作空间）"),
    keyword: Optional[str] = Query(None, description="搜索关键词（同时模糊匹配 other_name 和 id）"),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    pagesize: int = Query(10, ge=1, description="每页数量"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取工作空间的宿主列表（分页查询，支持模糊搜索）

    返回工作空间下的宿主列表，支持分页查询和模糊搜索。
    通过 keyword 参数同时模糊匹配 other_name 和 id 字段。

    Args:
        workspace_id: 工作空间ID（可选，默认当前用户工作空间）
        keyword: 搜索关键词（可选，同时模糊匹配 other_name 和 id）
        page: 页码（从1开始，默认1）
        pagesize: 每页数量（默认10）
        db: 数据库会话
        current_user: 当前用户

    Returns:
        ApiResponse: 包含宿主列表和分页信息
    """
    # 如果未提供 workspace_id，使用当前用户的工作空间
    if workspace_id is None:
        workspace_id = current_user.current_workspace_id
    # 获取当前空间类型
    current_workspace_type = memory_dashboard_service.get_current_workspace_type(db, workspace_id, current_user)
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的宿主列表, 类型: {current_workspace_type}")

    # 获取分页的 end_users
    end_users_result = memory_dashboard_service.get_workspace_end_users_paginated(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user,
        page=page,
        pagesize=pagesize,
        keyword=keyword
    )

    end_users = end_users_result.get("items", [])
    total = end_users_result.get("total", 0)

    if not end_users:
        api_logger.info(f"工作空间下没有宿主或当前页无数据: total={total}, page={page}")
        return success(data={
            "items": [],
            "page": {
                "page": page,
                "pagesize": pagesize,
                "total": total,
                "hasnext": (page * pagesize) < total
            }
        }, msg="宿主列表获取成功")

    end_user_ids = [str(user.id) for user in end_users]

    # 并发执行两个独立的查询任务
    async def get_memory_configs():
        """获取记忆配置（在线程池中执行同步查询）"""
        try:
            return await asyncio.to_thread(
                get_end_users_connected_configs_batch,
                end_user_ids, db
            )
        except Exception as e:
            api_logger.error(f"批量获取记忆配置失败: {str(e)}")
            return {}

    async def get_memory_nums():
        """获取记忆数量"""
        if current_workspace_type == "rag":
            # RAG 模式：批量查询
            try:
                chunk_map = await asyncio.to_thread(
                    memory_dashboard_service.get_users_total_chunk_batch,
                    end_user_ids, db, current_user
                )
                return {uid: {"total": count} for uid, count in chunk_map.items()}
            except Exception as e:
                api_logger.error(f"批量获取 RAG chunk 数量失败: {str(e)}")
                return {uid: {"total": 0} for uid in end_user_ids}

        elif current_workspace_type == "neo4j":
            # Neo4j 模式：批量查询（简化版本，只返回total）
            try:
                batch_result = await memory_storage_service.search_all_batch(end_user_ids)
                return {uid: {"total": count} for uid, count in batch_result.items()}
            except Exception as e:
                api_logger.error(f"批量获取 Neo4j 记忆数量失败: {str(e)}")
                return {uid: {"total": 0} for uid in end_user_ids}

        return {uid: {"total": 0} for uid in end_user_ids}

    # 触发按需初始化：为 implicit_emotions_storage 中没有记录的用户异步生成数据
    try:
        from app.celery_app import celery_app as _celery_app
        _celery_app.send_task(
            "app.tasks.init_implicit_emotions_for_users",
            kwargs={"end_user_ids": end_user_ids},
        )
        _celery_app.send_task(
            "app.tasks.init_interest_distribution_for_users",
            kwargs={"end_user_ids": end_user_ids},
        )
        api_logger.info(f"已触发按需初始化任务，候选用户数: {len(end_user_ids)}")
    except Exception as e:
        api_logger.warning(f"触发按需初始化任务失败（不影响主流程）: {e}")

    # 并发执行配置查询和记忆数量查询
    memory_configs_map, memory_nums_map = await asyncio.gather(
        get_memory_configs(),
        get_memory_nums()
    )

    # 构建结果列表
    items = []
    for end_user in end_users:
        user_id = str(end_user.id)
        config_info = memory_configs_map.get(user_id, {})
        items.append({
            'end_user': {
                'id': user_id,
                'other_name': end_user.other_name
            },
            'memory_num': memory_nums_map.get(user_id, {"total": 0}),
            'memory_config': {
                "memory_config_id": config_info.get("memory_config_id"),
                "memory_config_name": config_info.get("memory_config_name")
            }
        })

    # 触发社区聚类补全任务（异步，不阻塞接口响应）
    try:
        from app.tasks import init_community_clustering_for_users
        init_community_clustering_for_users.delay(end_user_ids=end_user_ids, workspace_id=str(workspace_id))
        api_logger.info(f"已触发社区聚类补全任务，候选用户数: {len(end_user_ids)}")
    except Exception as e:
        api_logger.warning(f"触发社区聚类补全任务失败（不影响主流程）: {str(e)}")

    # 构建分页响应
    result = {
        "items": items,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "hasnext": (page * pagesize) < total
        }
    }

    api_logger.info(f"成功获取 {len(end_users)} 个宿主记录，总计 {total} 条")
    return success(data=result, msg="宿主列表获取成功")


@router.get("/memory_increment", response_model=ApiResponse)
def get_workspace_memory_increment(
    limit: int = Query(7, description="返回记录数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取工作空间的记忆增量"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的记忆增量")
    memory_increment = memory_dashboard_service.get_workspace_memory_increment(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user,
        limit=limit
    )
    api_logger.info(f"成功获取 {len(memory_increment)} 条记忆增量记录")
    return success(data=memory_increment, msg="记忆增量获取成功")


@router.get("/api_increment", response_model=ApiResponse)
def get_workspace_api_increment(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取API调用趋势"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的API调用增量")
    api_increment = memory_dashboard_service.get_workspace_api_increment(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user
    )
    api_logger.info(f"成功获取 {api_increment} API调用增量")
    return success(data=api_increment, msg="API调用增量获取成功")


@router.post("/total_memory", response_model=ApiResponse)
def write_workspace_total_memory(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """工作空间记忆总量的写入（异步任务）"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求写入工作空间 {workspace_id} 的记忆总量")
    
    # 触发 Celery 异步任务
    from app.celery_app import celery_app
    task = celery_app.send_task(
        "app.controllers.memory_storage_controller.search_all",
        kwargs={"workspace_id": str(workspace_id)}
    )
    
    api_logger.info(f"已触发记忆总量统计任务，task_id: {task.id}")
    return success(
        data={"task_id": task.id, "workspace_id": str(workspace_id)},
        msg="记忆总量统计任务已启动"
    )


@router.get("/task_status/{task_id}", response_model=ApiResponse)
def get_task_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """查询异步任务的执行状态和结果"""
    api_logger.info(f"用户 {current_user.username} 查询任务状态: task_id={task_id}")
    
    from app.celery_app import celery_app
    from celery.result import AsyncResult
    
    # 获取任务结果
    task_result = AsyncResult(task_id, app=celery_app)
    
    response_data = {
        "task_id": task_id,
        "status": task_result.state,  # PENDING, STARTED, SUCCESS, FAILURE, RETRY, REVOKED
    }
    
    # 如果任务完成，返回结果
    if task_result.ready():
        if task_result.successful():
            response_data["result"] = task_result.result
            api_logger.info(f"任务 {task_id} 执行成功")
            return success(data=response_data, msg="任务执行成功")
        else:
            # 任务失败
            response_data["error"] = str(task_result.result)
            api_logger.error(f"任务 {task_id} 执行失败: {task_result.result}")
            return success(data=response_data, msg="任务执行失败")
    else:
        # 任务还在执行中
        api_logger.info(f"任务 {task_id} 状态: {task_result.state}")
        return success(data=response_data, msg=f"任务状态: {task_result.state}")


@router.get("/memory_list", response_model=ApiResponse)
def get_workspace_memory_list(
    limit: int = Query(7, description="记忆增量返回记录数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    用户记忆列表整合接口
    
    整合以下三个接口的数据：
    1. total_memory - 工作空间记忆总量
    2. memory_increment - 工作空间记忆增量
    3. hosts - 工作空间宿主列表
    
    返回格式：
    {
        "total_memory": float,
        "memory_increment": [
            {"date": "2024-01-01", "count": 100},
            ...
        ],
        "hosts": [
            {"id": "uuid", "name": "宿主名", ...},
            ...
        ]
    }
    """
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的记忆列表")
    memory_list = memory_dashboard_service.get_workspace_memory_list(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user,
        limit=limit
    )
    api_logger.info("成功获取记忆列表")
    return success(data=memory_list, msg="记忆列表获取成功")


@router.get("/total_memory_count", response_model=ApiResponse)
async def get_workspace_total_memory_count(
    end_user_id: Optional[str] = Query(None, description="可选的用户ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取工作空间的记忆总量（通过聚合所有host的记忆数）
    
    逻辑：
    1. 从 memory_list 获取所有 host_id
    2. 对每个 host_id 调用 search_all 获取 total
    3. 将所有 total 求和返回
    
    返回格式：
    {
        "total_memory_count": int,
        "host_count": int,
        "details": [
            {"end_user_id": "uuid", "count": 100, "name": "用户名称"},
            ...
        ]
    }
    """
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的记忆总量")
    total_memory_count = await memory_dashboard_service.get_workspace_total_memory_count(
        db=db,
        workspace_id=workspace_id,
        current_user=current_user,
        end_user_id=end_user_id
    )
    api_logger.info(f"成功获取记忆总量: {total_memory_count.get('total_memory_count', 0)}")
    return success(data=total_memory_count, msg="记忆总量获取成功")


# ======== RAG 数据统计 ========
@router.get("/total_rag_count", response_model=ApiResponse)
def get_workspace_total_rag_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    获取 rag 的总文档数、总chunk数、总知识库数量、总api调用数量
    """
    total_documents = memory_dashboard_service.get_rag_total_doc(db, current_user)
    total_chunk = memory_dashboard_service.get_rag_total_chunk(db, current_user)
    total_kb = memory_dashboard_service.get_rag_total_kb(db, current_user)
    data = {
        'total_documents':total_documents,
        'total_chunk':total_chunk,
        'total_kb':total_kb,
        'total_api':1024
    }
    return success(data=data, msg="RAG相关数据获取成功")

@router.get("/current_user_rag_total_num", response_model=ApiResponse)
def get_current_user_rag_total_num(
    end_user_id: str = Query(..., description="宿主ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取当前宿主的 RAG 的总chunk数量
    """
    total_chunk = memory_dashboard_service.get_current_user_total_chunk(end_user_id, db, current_user)
    return success(data=total_chunk, msg="宿主RAG知识数据获取成功")

@router.get("/rag_content", response_model=ApiResponse)
def get_rag_content(
    end_user_id: str = Query(..., description="宿主ID"),
    page: int = Query(1, gt=0, description="页码，从1开始"),
    pagesize: int = Query(15, gt=0, le=100, description="每页返回记录数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    获取当前宿主知识库中的chunk内容（分页）
    """
    data = memory_dashboard_service.get_rag_content(end_user_id, page, pagesize, db, current_user)
    return success(data=data, msg="宿主RAGchunk数据获取成功")


@router.get("/chunk_summary_tag", response_model=ApiResponse)
async def get_chunk_summary_tag(
    end_user_id: str = Query(..., description="宿主ID"),
    limit: int = Query(15, description="返回记录数"),
    max_tags: int = Query(10, description="最大标签数量"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    读取RAG摘要、标签和人物形象（纯读库，不触发生成）。

    返回格式：
    {
        "summary": "用户摘要",
        "tags": [{"tag": "标签1", "frequency": 5}, ...],
        "personas": ["产品设计师", ...],
        "generated": true/false  // false表示尚未生产，请调用 /generate_rag_profile
    }
    """
    api_logger.info(f"用户 {current_user.username} 读取宿主 {end_user_id} 的RAG摘要/标签/人物形象")

    data = await memory_dashboard_service.get_chunk_summary_and_tags(
        end_user_id=end_user_id,
        limit=limit,
        max_tags=max_tags,
        db=db,
        current_user=current_user
    )

    return success(data=data, msg="获取成功")


@router.get("/chunk_insight", response_model=ApiResponse)
async def get_chunk_insight(
    end_user_id: str = Query(..., description="宿主ID"),
    limit: int = Query(15, description="返回记录数"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    读取RAG洞察报告（纯读库，不触发生成）。

    返回格式：
    {
        "insight": "总体概述",
        "behavior_pattern": "行为模式",
        "key_findings": "关键发现",
        "growth_trajectory": "成长轨迹",
        "generated": true/false  // false表示尚未生产，请调用 /generate_rag_profile
    }
    """
    api_logger.info(f"用户 {current_user.username} 读取宿主 {end_user_id} 的RAG洞察")

    data = await memory_dashboard_service.get_chunk_insight(
        end_user_id=end_user_id,
        limit=limit,
        db=db,
        current_user=current_user
    )

    return success(data=data, msg="获取成功")


class GenerateRagProfileRequest(BaseModel):
    end_user_id: str = Field(..., description="宿主ID")
    limit: int = Field(15, description="参与生成的chunk数量上限")
    max_tags: int = Field(10, description="最大标签数量")


@router.post("/generate_rag_profile", response_model=ApiResponse)
async def generate_rag_profile(
    body: GenerateRagProfileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    生产接口：为RAG存储模式的宿主全量重新生成完整画像并持久化到end_user表。
    每次请求都会重新生成，覆盖已有数据。
    """
    api_logger.info(f"用户 {current_user.username} 触发RAG画像生产: end_user_id={body.end_user_id}")

    data = await memory_dashboard_service.generate_rag_profile(
        end_user_id=body.end_user_id,
        limit=body.limit,
        max_tags=body.max_tags,
        db=db,
        current_user=current_user,
    )

    api_logger.info(f"RAG画像生产完成: {data}")
    return success(data=data, msg="RAG画像生产完成")


@router.get("/dashboard_data", response_model=ApiResponse)
async def dashboard_data(
    end_user_id: Optional[str] = Query(None, description="可选的用户ID"),
    start_date: Optional[int] = Query(None, description="开始时间戳（毫秒）"),
    end_date: Optional[int] = Query(None, description="结束时间戳（毫秒）"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    整合dashboard数据接口
    
    整合以下接口的数据：
    1. /dashboard/total_memory_count - 记忆总量
    2. /dashboard/api_increment - API调用增量
    3. /memory/stats/types - 知识库类型统计（只要total数据）
    4. /dashboard/total_rag_count - RAG相关数据
    
    根据 storage_type 判断调用不同的接口
    
    返回格式：
    {
        "storage_type": str,
        "neo4j_data": {
            "total_memory": int,
            "total_app": int,
            "total_knowledge": int,
            "total_api_call": int
        } | null,
        "rag_data": {
            "total_memory": int,
            "total_app": int,
            "total_knowledge": int,
            "total_api_call": int
        } | null
    }
    """
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的dashboard整合数据")
    
    # 如果没有提供时间范围，默认使用最近30天
    if start_date is None or end_date is None:
        from datetime import datetime, timedelta
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=30)
        end_date = int(end_dt.timestamp() * 1000)
        start_date = int(start_dt.timestamp() * 1000)
        api_logger.info(f"使用默认时间范围: {start_dt} 到 {end_dt}")
    
    # 获取 storage_type，如果为 None 则使用默认值
    storage_type = workspace_service.get_workspace_storage_type(
        db=db,
        workspace_id=workspace_id,
        user=current_user
    )
    if storage_type is None:
        storage_type = 'neo4j'
    
    
    # 根据 storage_type 决定返回哪个数据对象
    # 如果是 'rag'，neo4j_data 为 null；否则 rag_data 为 null
    result = {
        "storage_type": storage_type,
        "neo4j_data": None,
        "rag_data": None
    }
    
    try:
        # 如果 storage_type 为 'neo4j' 或空，获取 neo4j_data
        if storage_type == 'neo4j':
            neo4j_data = {
                "total_memory": None,
                "total_app": None,
                "total_knowledge": None,
                "total_api_call": None
            }
            
            # 1. 获取记忆总量（total_memory）
            try:
                total_memory_data = await memory_dashboard_service.get_workspace_total_memory_count(
                    db=db,
                    workspace_id=workspace_id,
                    current_user=current_user,
                    end_user_id=end_user_id
                )
                neo4j_data["total_memory"] = total_memory_data.get("total_memory_count", 0)
                # total_app: 统计当前空间下的所有app数量
                # 包含自有app + 被分享给本工作空间的app
                from app.services import app_service as _app_svc
                _, total_app = _app_svc.AppService(db).list_apps(
                    workspace_id=workspace_id, include_shared=True, pagesize=1
                )
                neo4j_data["total_app"] = total_app
                api_logger.info(f"成功获取记忆总量: {neo4j_data['total_memory']}, 应用数量: {neo4j_data['total_app']}")
            except Exception as e:
                api_logger.warning(f"获取记忆总量失败: {str(e)}")
            
            # 2. 获取知识库数量（total_knowledge），排除用户知识库(permission_id='Memory')
            try:
                from sqlalchemy import func as _func
                from app.models.knowledge_model import Knowledge as _Knowledge
                total_knowledge = db.query(_func.count(_Knowledge.id)).filter(
                    _Knowledge.workspace_id == workspace_id,
                    _Knowledge.status == 1,
                    _Knowledge.permission_id != "Memory"
                ).scalar() or 0
                neo4j_data["total_knowledge"] = total_knowledge
                api_logger.info(f"成功获取知识库数量: {neo4j_data['total_knowledge']}")
            except Exception as e:
                api_logger.warning(f"获取知识库数量失败: {str(e)}")
            
            # 3. 获取API调用统计（total_api_call）—— 仅统计当天api_key_log调用次数
            try:
                from datetime import datetime
                from sqlalchemy import func as _api_func
                from app.models.api_key_model import ApiKey as _ApiKey, ApiKeyLog as _ApiKeyLog

                _now = datetime.now()
                _today_start = _now.replace(hour=0, minute=0, second=0, microsecond=0)

                _api_key_ids = [
                    row[0] for row in db.query(_ApiKey.id).filter(
                        _ApiKey.workspace_id == workspace_id
                    ).all()
                ]
                if _api_key_ids:
                    total_api_calls = db.query(_api_func.count(_ApiKeyLog.id)).filter(
                        _ApiKeyLog.api_key_id.in_(_api_key_ids),
                        _ApiKeyLog.created_at >= _today_start,
                        _ApiKeyLog.created_at < _now
                    ).scalar() or 0
                else:
                    total_api_calls = 0
                neo4j_data["total_api_call"] = total_api_calls
                api_logger.info(f"成功获取API调用统计(当天): {neo4j_data['total_api_call']}")
            except Exception as e:
                api_logger.error(f"获取API调用统计失败: {str(e)}")
                neo4j_data["total_api_call"] = 0
            
            # 计算昨日对比
            try:
                changes = memory_dashboard_service.get_dashboard_yesterday_changes(
                    db=db,
                    workspace_id=workspace_id,
                    storage_type=storage_type,
                    today_data=neo4j_data
                )
                neo4j_data.update(changes)
            except Exception as e:
                api_logger.warning(f"计算neo4j昨日对比失败: {str(e)}")
                neo4j_data.update({
                    "total_memory_change": None,
                    "total_app_change": None,
                    "total_knowledge_change": None,
                    "total_api_call_change": None,
                })

            result["neo4j_data"] = neo4j_data
            api_logger.info("成功获取neo4j_data")
        
        # 如果 storage_type 为 'rag'，获取 rag_data
        elif storage_type == 'rag':
            rag_data = {
                "total_memory": None,
                "total_app": None,
                "total_knowledge": None,
                "total_api_call": None
            }
            
            # 获取RAG相关数据
            try:
                # total_memory: 只统计用户知识库（permission_id='Memory'）的chunk数
                total_chunk = memory_dashboard_service.get_rag_user_kb_total_chunk(db, current_user)
                rag_data["total_memory"] = total_chunk
                
                # total_app: 统计当前空间下的所有app数量
                # 包含自有app + 被分享给本工作空间的app
                from app.services import app_service as _app_svc
                _, total_app = _app_svc.AppService(db).list_apps(
                    workspace_id=workspace_id, include_shared=True, pagesize=1
                )
                rag_data["total_app"] = total_app
                
                # total_knowledge: 直接查knowledges表status=1的总数，排除用户知识库(permission_id='Memory')
                from sqlalchemy import func as _func
                from app.models.knowledge_model import Knowledge as _Knowledge
                total_kb = db.query(_func.count(_Knowledge.id)).filter(
                    _Knowledge.workspace_id == workspace_id,
                    _Knowledge.status == 1,
                    _Knowledge.permission_id != "Memory"
                ).scalar() or 0
                rag_data["total_knowledge"] = total_kb
                
                # total_api_call: 仅统计当天api_key_log调用次数
                try:
                    from datetime import datetime
                    from sqlalchemy import func as _api_func
                    from app.models.api_key_model import ApiKey as _ApiKey, ApiKeyLog as _ApiKeyLog

                    _now = datetime.now()
                    _today_start = _now.replace(hour=0, minute=0, second=0, microsecond=0)

                    _api_key_ids = [
                        row[0] for row in db.query(_ApiKey.id).filter(
                            _ApiKey.workspace_id == workspace_id
                        ).all()
                    ]
                    if _api_key_ids:
                        total_api_calls = db.query(_api_func.count(_ApiKeyLog.id)).filter(
                            _ApiKeyLog.api_key_id.in_(_api_key_ids),
                            _ApiKeyLog.created_at >= _today_start,
                            _ApiKeyLog.created_at < _now
                        ).scalar() or 0
                    else:
                        total_api_calls = 0
                    rag_data["total_api_call"] = total_api_calls
                    api_logger.info(f"成功获取RAG模式API调用统计(当天): {rag_data['total_api_call']}")
                except Exception as e:
                    api_logger.warning(f"获取RAG模式API调用统计失败，使用默认值: {str(e)}")
                    rag_data["total_api_call"] = 0
                
                api_logger.info(f"成功获取RAG相关数据: memory={total_chunk}, app={total_app}, knowledge={total_kb}, api_calls={rag_data['total_api_call']}")
            except Exception as e:
                api_logger.warning(f"获取RAG相关数据失败: {str(e)}")
            
            # 计算昨日对比
            try:
                changes = memory_dashboard_service.get_dashboard_yesterday_changes(
                    db=db,
                    workspace_id=workspace_id,
                    storage_type=storage_type,
                    today_data=rag_data
                )
                rag_data.update(changes)
            except Exception as e:
                api_logger.warning(f"计算RAG昨日对比失败: {str(e)}")
                rag_data.update({
                    "total_memory_change": None,
                    "total_app_change": None,
                    "total_knowledge_change": None,
                    "total_api_call_change": None,
                })

            result["rag_data"] = rag_data
            api_logger.info("成功获取rag_data")
        
        api_logger.info("成功获取dashboard整合数据")
        return success(data=result, msg="Dashboard数据获取成功")
        
    except Exception as e:
        api_logger.error(f"获取dashboard整合数据失败: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取dashboard整合数据失败: {str(e)}"
        )
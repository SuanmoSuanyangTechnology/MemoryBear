import uuid
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.logging_config import get_api_logger
from app.core.response_utils import success
from app.core.utils.datetime_utils import to_timestamp_ms, utcnow_naive
from app.db import get_async_db_context
from app.dependencies import CurrentUserSnapshot, get_current_user_async
from app.schemas.response_schema import ApiResponse
from app.services import memory_dashboard_service, workspace_service
from app.services.memory_agent_service import get_end_users_connected_configs_batch_async

# 获取API专用日志器
api_logger = get_api_logger()


def _dispatch_dashboard_async_jobs(workspace_id: uuid.UUID, end_user_ids: List[str]) -> None:
    """异步派发 dashboard 相关的 Celery 任务（Redis 节流 + send_task）。

    用 FastAPI BackgroundTasks 在响应返回之后调用，避免阻塞接口主链路。
    冷启动场景下 Redis 连接池初始化 + 3 次 broker publish 可能消耗几百毫秒，
    放到响应后执行可显著降低首响时间。

    所有异常都吞掉，避免污染请求/响应生命周期。
    """
    if not end_user_ids:
        return

    try:
        from app.celery_app import celery_app as _celery_app
        from app.tasks import get_sync_redis_client

        _redis = get_sync_redis_client()
        if _redis is None:
            return

        # 按需初始化任务（节流 60s）
        _throttle_key = f"dashboard:init_tasks:throttle:{workspace_id}"
        if _redis.set(_throttle_key, "1", nx=True, ex=60):
            _celery_app.send_task(
                "app.tasks.init_implicit_emotions_for_users",
                kwargs={"end_user_ids": end_user_ids},
            )
            _celery_app.send_task(
                "app.tasks.init_interest_distribution_for_users",
                kwargs={"end_user_ids": end_user_ids},
            )

        # 社区聚类补全任务（节流 60s）
        _cluster_key = f"dashboard:cluster_task:throttle:{workspace_id}"
        if _redis.set(_cluster_key, "1", nx=True, ex=60):
            _celery_app.send_task(
                "app.tasks.init_community_clustering_for_users",
                kwargs={"end_user_ids": end_user_ids, "workspace_id": str(workspace_id)},
            )
    except Exception as _e:
        api_logger.warning(
            f"后台任务派发失败（不影响已返回响应）: {_e}",
            exc_info=True,
        )


router = APIRouter(
    prefix="/dashboard",
    tags=["Dashboard"],
    dependencies=[Depends(get_current_user_async)],  # 所有路由都需要认证
)

@router.get("/total_end_users", response_model=ApiResponse)
async def get_workspace_total_end_users(
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """获取用户列表的总用户数（异步版本）。"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的宿主列表")

    async with get_async_db_context() as db:
        total_end_users = await memory_dashboard_service.get_workspace_total_end_users_async(
            db=db,
            workspace_id=workspace_id,
            current_user=current_user,
        )

    api_logger.info(f"成功获取最新用户总数: total_num={total_end_users.get('total_num', 0)}")
    return success(data=total_end_users, msg="用户数量获取成功")


@router.get("/end_users", response_model=ApiResponse)
async def get_workspace_end_users(
    background_tasks: BackgroundTasks,
    workspace_id: Optional[uuid.UUID] = Query(None, description="工作空间ID（可选，默认当前用户工作空间）"),
    keyword: Optional[str] = Query(None, description="搜索关键词（同时模糊匹配 other_name 和 id）"),
    page: int = Query(1, ge=1, description="页码，从1开始"),
    pagesize: int = Query(10, ge=1, le=100, description="每页数量"),
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """获取工作空间的宿主列表（分页查询，支持模糊搜索，异步版本）。"""
    if workspace_id is None:
        workspace_id = current_user.current_workspace_id

    async with get_async_db_context() as db:
        current_workspace_type = await memory_dashboard_service.get_current_workspace_type_async(
            db, workspace_id, current_user
        )
        api_logger.info(
            f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的宿主列表, "
            f"类型: {current_workspace_type}"
        )

        if current_workspace_type == "rag":
            end_users_result = await memory_dashboard_service.get_workspace_end_users_paginated_rag_async(
                db=db,
                workspace_id=workspace_id,
                current_user=current_user,
                page=page,
                pagesize=pagesize,
                keyword=keyword,
            )
            raw_items = end_users_result.get("items", [])
            end_users = [item["end_user"] for item in raw_items]
        else:
            end_users_result = await memory_dashboard_service.get_workspace_end_users_paginated_async(
                db=db,
                workspace_id=workspace_id,
                current_user=current_user,
                page=page,
                pagesize=pagesize,
                keyword=keyword,
            )
            raw_items = end_users_result.get("items", [])
            end_users = raw_items

        total = end_users_result.get("total", 0)

        if not end_users:
            api_logger.info(f"工作空间下没有宿主或当前页无数据: total={total}, page={page}")
            return success(data={
                "items": [],
                "page": {
                    "page": page,
                    "pagesize": pagesize,
                    "total": total,
                    "hasnext": (page * pagesize) < total,
                },
            }, msg="宿主列表获取成功")

        end_user_ids = [str(user.id) for user in end_users]

        try:
            memory_configs_map = await get_end_users_connected_configs_batch_async(end_user_ids, db)
        except Exception as e:
            api_logger.error(f"批量获取记忆配置失败: {str(e)}")
            memory_configs_map = {}

        items = []
        for index, end_user in enumerate(end_users):
            user_id = str(end_user.id)  # NOTE: 此处 user_id 是 end_user_id
            config_info = memory_configs_map.get(user_id, {})

            if current_workspace_type == "rag":
                memory_total = int(raw_items[index].get("memory_count", 0) or 0)
            else:
                memory_total = int(getattr(end_user, "memory_count", 0) or 0)

            items.append({
                "end_user_id": user_id,
                "end_user": {
                    "id": user_id,
                    "other_name": end_user.other_name,
                },
                "memory_num": {"total": memory_total},
                "memory_config": {
                    "memory_config_id": config_info.get("memory_config_id"),
                    "memory_config_name": config_info.get("memory_config_name"),
                },
            })

    result = {
        "items": items,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "hasnext": (page * pagesize) < total,
        },
    }

    background_tasks.add_task(_dispatch_dashboard_async_jobs, workspace_id, end_user_ids)

    api_logger.info(f"成功获取 {len(end_users)} 个宿主记录，总计 {total} 条")
    return success(data=result, msg="宿主列表获取成功")


@router.get("/memory_increment", response_model=ApiResponse)
async def get_workspace_memory_increment(
    limit: int = Query(7, description="返回记录数"),
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """获取工作空间的记忆增量（异步版本）。"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的记忆增量")

    async with get_async_db_context() as db:
        memory_increment = await memory_dashboard_service.get_workspace_memory_increment_async(
            db=db,
            workspace_id=workspace_id,
            limit=limit,
            current_user=current_user,
        )

    api_logger.info(f"成功获取 {len(memory_increment)} 条记忆增量记录")
    return success(data=memory_increment, msg="记忆增量获取成功")


@router.get("/api_increment", response_model=ApiResponse)
async def get_workspace_api_increment(
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """获取 API 调用趋势（异步版本）。"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的API调用增量")

    async with get_async_db_context() as db:
        api_increment = await memory_dashboard_service.get_workspace_api_increment_async(
            db=db,
            workspace_id=workspace_id,
            current_user=current_user,
        )

    api_logger.info(f"成功获取 {api_increment} API调用增量")
    return success(data=api_increment, msg="API调用增量获取成功")


@router.post("/total_memory", response_model=ApiResponse)
async def write_workspace_total_memory(
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """工作空间记忆总量的写入（异步任务，异步端点版本）。"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求写入工作空间 {workspace_id} 的记忆总量")

    from app.celery_app import celery_app
    task = celery_app.send_task(
        "app.controllers.memory_storage_controller.search_all",
        kwargs={"workspace_id": str(workspace_id)},
    )

    api_logger.info(f"已触发记忆总量统计任务，task_id: {task.id}")
    return success(
        data={"task_id": task.id, "workspace_id": str(workspace_id)},
        msg="记忆总量统计任务已启动",
    )


@router.get("/task_status/{task_id}", response_model=ApiResponse)
async def get_task_status(
    task_id: str,
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """查询异步任务的执行状态和结果（异步端点版本）。"""
    api_logger.info(f"用户 {current_user.username} 查询任务状态: task_id={task_id}")

    from app.celery_app import celery_app
    from celery.result import AsyncResult

    task_result = AsyncResult(task_id, app=celery_app)

    response_data = {
        "task_id": task_id,
        "status": task_result.state,
    }

    if task_result.ready():
        if task_result.successful():
            response_data["result"] = task_result.result
            api_logger.info(f"任务 {task_id} 执行成功")
            return success(data=response_data, msg="任务执行成功")
        response_data["error"] = str(task_result.result)
        api_logger.error(f"任务 {task_id} 执行失败: {task_result.result}")
        return success(data=response_data, msg="任务执行失败")

    api_logger.info(f"任务 {task_id} 状态: {task_result.state}")
    return success(data=response_data, msg=f"任务状态: {task_result.state}")


@router.get("/memory_list", response_model=ApiResponse)
async def get_workspace_memory_list(
    limit: int = Query(7, description="记忆增量返回记录数"),
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """用户记忆列表整合接口（异步版本）。"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的记忆列表")

    async with get_async_db_context() as db:
        memory_list = await memory_dashboard_service.get_workspace_memory_list_async(
            db=db,
            workspace_id=workspace_id,
            current_user=current_user,
            limit=limit,
        )

    api_logger.info("成功获取记忆列表")
    return success(data=memory_list, msg="记忆列表获取成功")


@router.get("/total_memory_count", response_model=ApiResponse)
async def get_workspace_total_memory_count(
    end_user_id: Optional[str] = Query(None, description="可选的用户ID"),
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """获取工作空间的记忆总量（异步版本）。"""
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的记忆总量")

    async with get_async_db_context() as db:
        total_memory_count = await memory_dashboard_service.get_workspace_total_memory_count_async(
            db=db,
            workspace_id=workspace_id,
            current_user=current_user,
            end_user_id=end_user_id,
        )

    api_logger.info(f"成功获取记忆总量: {total_memory_count.get('total_memory_count', 0)}")
    return success(data=total_memory_count, msg="记忆总量获取成功")


# ======== RAG 数据统计 ========

@router.get("/total_rag_count", response_model=ApiResponse)
async def get_workspace_total_rag_count(
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """Get RAG total documents, chunks, knowledge bases, and API calls (async)."""

    async with get_async_db_context() as db:
        total_documents = await memory_dashboard_service.get_rag_total_doc_async(db, current_user)
        total_chunk = await memory_dashboard_service.get_rag_total_chunk_async(db, current_user)
        total_kb = await memory_dashboard_service.get_rag_total_kb_async(db, current_user)
        api_increment = await memory_dashboard_service.get_workspace_api_increment_async(
            db=db,
            workspace_id=current_user.current_workspace_id,
            current_user=current_user,
        )

    data = {
        "total_documents": total_documents,
        "total_chunk": total_chunk,
        "total_kb": total_kb,
        "total_api": api_increment,
    }
    return success(data=data, msg="RAG相关数据获取成功")


@router.get("/current_user_rag_total_num", response_model=ApiResponse)
async def get_current_user_rag_total_num(
    end_user_id: str = Query(..., description="宿主ID"),
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """获取当前宿主的 RAG 的总 chunk 数量（异步版本）。"""
    async with get_async_db_context() as db:
        total_chunk = await memory_dashboard_service.get_current_user_total_chunk_async(
            end_user_id, db, current_user
        )
    return success(data=total_chunk, msg="宿主RAG知识数据获取成功")


@router.get("/rag_content", response_model=ApiResponse)
async def get_rag_content(
    end_user_id: str = Query(..., description="宿主ID"),
    page: int = Query(1, gt=0, description="页码，从1开始"),
    pagesize: int = Query(15, gt=0, le=100, description="每页返回记录数"),
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """获取当前宿主知识库中的 chunk 内容（异步版本）。"""
    async with get_async_db_context() as db:
        data = await memory_dashboard_service.get_rag_content_async(
            end_user_id, page, pagesize, db, current_user
        )
    return success(data=data, msg="宿主RAGchunk数据获取成功")


@router.get("/chunk_summary_tag", response_model=ApiResponse)
async def get_chunk_summary_tag(
    end_user_id: str = Query(..., description="宿主ID"),
    limit: int = Query(15, description="返回记录数"),
    max_tags: int = Query(10, description="最大标签数量"),
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """读取 RAG 摘要、标签和人物形象（异步版本）。"""
    api_logger.info(f"用户 {current_user.username} 读取宿主 {end_user_id} 的RAG摘要/标签/人物形象")

    async with get_async_db_context() as db:
        data = await memory_dashboard_service.get_chunk_summary_and_tags_async(
            end_user_id=end_user_id,
            db=db,
        )

    return success(data=data, msg="获取成功")


@router.get("/chunk_insight", response_model=ApiResponse)
async def get_chunk_insight(
    end_user_id: str = Query(..., description="宿主ID"),
    limit: int = Query(15, description="返回记录数"),
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """读取 RAG 洞察报告（异步版本）。"""
    api_logger.info(f"用户 {current_user.username} 读取宿主 {end_user_id} 的RAG洞察")

    async with get_async_db_context() as db:
        data = await memory_dashboard_service.get_chunk_insight_async(
            end_user_id=end_user_id,
            db=db,
        )

    return success(data=data, msg="获取成功")


class GenerateRagProfileRequest(BaseModel):
    end_user_id: str = Field(..., description="宿主ID")
    limit: int = Field(15, description="参与生成的chunk数量上限")
    max_tags: int = Field(10, description="最大标签数量")


@router.post("/generate_rag_profile", response_model=ApiResponse)
async def generate_rag_profile(
    body: GenerateRagProfileRequest,
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """为 RAG 存储模式的宿主全量重新生成完整画像（异步版本）。"""
    api_logger.info(f"用户 {current_user.username} 触发RAG画像生产: end_user_id={body.end_user_id}")

    async with get_async_db_context() as db:
        data = await memory_dashboard_service.generate_rag_profile_async(
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
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """整合 dashboard 数据接口（异步版本）。

    - storage_type 查询走 async workspace_service（非阻塞）
    - dashboard service 全部走 async Session
    """
    workspace_id = current_user.current_workspace_id
    api_logger.info(f"用户 {current_user.username} 请求获取工作空间 {workspace_id} 的dashboard整合数据")

    # 默认时间范围：最近 30 天
    if start_date is None or end_date is None:
        from datetime import timedelta
        end_dt = utcnow_naive()
        start_dt = end_dt - timedelta(days=30)
        end_date = to_timestamp_ms(end_dt)
        start_date = to_timestamp_ms(start_dt)
        api_logger.info(f"使用默认时间范围: {start_dt} 到 {end_dt}")

    result = {
        "storage_type": None,
        "neo4j_data": None,
        "rag_data": None,
    }

    try:
        async with get_async_db_context() as db:
            storage_type = await workspace_service.get_workspace_storage_type_async(
                db=db,
                workspace_id=workspace_id,
                user=current_user,
            )
            if storage_type is None:
                storage_type = "neo4j"
            result["storage_type"] = storage_type

            if storage_type == "neo4j":
                neo4j_data = {
                    "total_memory": None,
                    "total_app": None,
                    "total_knowledge": None,
                    "total_api_call": None,
                }

                # 记忆总量
                try:
                    total_memory_data = await memory_dashboard_service.get_workspace_total_memory_count_async(
                        db=db,
                        workspace_id=workspace_id,
                        current_user=current_user,
                        end_user_id=end_user_id,
                    )
                    neo4j_data["total_memory"] = total_memory_data.get("total_memory_count", 0)
                    api_logger.info(f"成功获取记忆总量: {neo4j_data['total_memory']}")
                except Exception as e:
                    api_logger.warning(f"获取记忆总量失败: {str(e)}")

                # 共享统计
                common_stats = await memory_dashboard_service.get_dashboard_common_stats_async(db, workspace_id)
                neo4j_data.update(common_stats)
                api_logger.info(
                    f"成功获取共享统计: app={common_stats['total_app']}, "
                    f"knowledge={common_stats['total_knowledge']}, "
                    f"api_call={common_stats['total_api_call']}"
                )

                # 昨日对比
                try:
                    changes = await memory_dashboard_service.get_dashboard_yesterday_changes_async(
                        db=db,
                        workspace_id=workspace_id,
                        storage_type=storage_type,
                        today_data=neo4j_data,
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

            elif storage_type == "rag":
                rag_data = {
                    "total_memory": None,
                    "total_app": None,
                    "total_knowledge": None,
                    "total_api_call": None,
                }

                # 记忆总量（RAG）
                try:
                    total_chunk = await memory_dashboard_service.get_rag_user_kb_total_chunk_async(db, current_user)
                    rag_data["total_memory"] = total_chunk
                    api_logger.info(f"成功获取RAG记忆总量: {total_chunk}")
                except Exception as e:
                    api_logger.warning(f"获取RAG记忆总量失败: {str(e)}")

                # 共享统计
                common_stats = await memory_dashboard_service.get_dashboard_common_stats_async(db, workspace_id)
                rag_data.update(common_stats)
                api_logger.info(
                    f"成功获取共享统计: app={common_stats['total_app']}, "
                    f"knowledge={common_stats['total_knowledge']}, "
                    f"api_call={common_stats['total_api_call']}"
                )

                # 昨日对比
                try:
                    changes = await memory_dashboard_service.get_dashboard_yesterday_changes_async(
                        db=db,
                        workspace_id=workspace_id,
                        storage_type=storage_type,
                        today_data=rag_data,
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

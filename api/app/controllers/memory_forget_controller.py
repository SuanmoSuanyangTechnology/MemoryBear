"""
遗忘引擎控制器模块

本模块提供遗忘引擎的 REST API 接口，包括：
1. 手动触发遗忘周期
2. 获取和更新配置
3. 获取统计信息
4. 获取遗忘曲线数据

所有接口都需要用户认证，并自动关联到当前工作空间。
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.quota_manager import get_end_user_memory_limit_async
from app.core.response_utils import fail, success
from app.db import get_async_db_context, get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.repositories.end_user_repository import get_tenant_id_by_end_user_id_async
from app.repositories.forget_log_repository import ForgetLogRepository
from app.repositories.memory_config_repository import MemoryConfigRepository
from app.repositories.workspace_repository import get_workspace_memory_config_id_async
from app.schemas.memory_storage_schema import (
    ForgettingTriggerRequest,
    ForgettingStatsResponse,
    ForgettingReportResponse,
    ForgettingCurveRequest,
    ForgettingCurveResponse,
    ForgettingCurvePoint,
    PendingNodesResponse,
)
from app.schemas.response_schema import ApiResponse
from app.services import memory_forget_service
from app.services.memory_config_service import MemoryConfigService
from app.services.memory_forget_service import MemoryForgetService
from app.utils.config_utils import resolve_config_id
from app.utils.redis_cache import invalidate_cache

# 获取API专用日志器
api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory/forget-memory",
    tags=["Memory Forgetting Engine"],
    dependencies=[Depends(get_current_user)]  # 所有路由都需要认证
)

# 初始化服务
forget_service = MemoryForgetService()


# ==================== API 端点 ====================

@router.post("/trigger", response_model=ApiResponse)
async def trigger_forgetting_cycle(
        payload: ForgettingTriggerRequest,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """
    手动触发遗忘周期
    
    执行一次完整的遗忘周期，识别并融合低激活值节点。
    
    Args:
        payload: 触发请求参数
        current_user: 当前用户
        db: 数据库会话
    
    Returns:
        ApiResponse: 包含遗忘报告的响应
    """
    workspace_id = current_user.current_workspace_id
    end_user_id = payload.end_user_id  # 从 payload 中获取 end_user_id

    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试触发遗忘周期但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    # 通过 end_user_id 获取关联的 config_id
    try:
        config_service = MemoryConfigService(db)
        config_id = config_service.get_config_id_by_end_user(end_user_id)

        if config_id is None:
            api_logger.warning(f"终端用户 {end_user_id} 未关联记忆配置")
            return fail(BizCode.INVALID_PARAMETER, f"终端用户 {end_user_id} 未关联记忆配置", "memory_config_id is None")

        api_logger.debug(f"通过 end_user_id={end_user_id} 获取到 config_id={config_id}")
    except ValueError as e:
        api_logger.warning(f"获取终端用户配置失败: {str(e)}")
        return fail(BizCode.INVALID_PARAMETER, str(e), "ValueError")
    except Exception as e:
        api_logger.error(f"获取终端用户配置时发生错误: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取终端用户配置失败", str(e))

    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求触发遗忘周期: "
        f"end_user_id={end_user_id}, config_id={config_id}, max_batch={payload.max_merge_batch_size}, "
        f"min_days={payload.min_days_since_access}"
    )

    try:
        # 调用服务层执行遗忘周期
        report = await forget_service.trigger_forgetting_cycle(
            db=db,
            end_user_id=end_user_id,  # 服务层方法的参数名是 end_user_id
            max_merge_batch_size=payload.max_merge_batch_size,
            min_days_since_access=payload.min_days_since_access,
            config_id=config_id
        )

        # 构建响应
        response_data = ForgettingReportResponse(**report)

        return success(data=response_data.model_dump(), msg="遗忘周期执行成功")

    except RuntimeError as e:
        api_logger.warning(f"遗忘周期执行被拒绝: {str(e)}")
        return fail(BizCode.INVALID_PARAMETER, str(e), "RuntimeError")

    except Exception as e:
        api_logger.error(f"触发遗忘周期失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "触发遗忘周期失败", str(e))


# ==================== 记忆配置接口已迁移 ====================
# read_forgetting_config / update_forgetting_config 已迁移至 memory_config_controller
# （/memory_config/read_config_forgetting、/memory_config/update_config_forgetting）。


@router.get("/stats", response_model=ApiResponse)
async def get_forgetting_stats(
        end_user_id: Optional[str] = None,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """
    获取遗忘引擎统计信息
    
    返回知识层节点统计、激活值分布等信息。
    
    Args:
        end_user_id: 组ID（即 end_user_id，可选）
        current_user: 当前用户
        db: 数据库会话
    
    Returns:
        ApiResponse: 包含统计信息的响应
    """
    workspace_id = current_user.current_workspace_id
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试获取遗忘引擎统计但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    # 如果提供了 end_user_id，通过它获取 config_id
    config_id = None
    if end_user_id:
        try:
            config_service = MemoryConfigService(db)
            config_id = config_service.get_config_id_by_end_user(end_user_id)

            if config_id is None:
                api_logger.warning(f"终端用户 {end_user_id} 未关联记忆配置")
                return fail(BizCode.INVALID_PARAMETER, f"终端用户 {end_user_id} 未关联记忆配置",
                            "memory_config_id is None")

            api_logger.debug(f"通过 end_user_id={end_user_id} 获取到 config_id={config_id}")
        except ValueError as e:
            api_logger.warning(f"获取终端用户配置失败: {str(e)}")
            return fail(BizCode.INVALID_PARAMETER, str(e), "ValueError")
        except Exception as e:
            api_logger.error(f"获取终端用户配置时发生错误: {str(e)}")
            return fail(BizCode.INTERNAL_ERROR, "获取终端用户配置失败", str(e))

    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求获取遗忘引擎统计: "
        f"end_user_id={end_user_id}, config_id={config_id}"
    )

    try:
        # 调用服务层获取统计信息
        stats = await forget_service.get_forgetting_stats(
            db=db,
            end_user_id=end_user_id,
            config_id=config_id
        )

        # 构建响应
        response_data = ForgettingStatsResponse(**stats)

        return success(data=response_data.model_dump(), msg="查询成功")

    except Exception as e:
        api_logger.error(f"获取遗忘引擎统计失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取遗忘引擎统计失败", str(e))


@router.get("/pending-nodes", response_model=ApiResponse)
async def get_pending_nodes(
        end_user_id: str,
        page: int = 1,
        pagesize: int = 10,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """
    获取待遗忘节点列表（独立分页接口）

    查询满足遗忘条件的节点（激活值低于阈值且最后访问时间超过最小天数）。
    此接口独立分页，与 /stats 接口分离。

    Args:
        end_user_id: 组ID（即 end_user_id，必填）
        page: 页码（从1开始，默认1）
        pagesize: 每页数量（默认10）
        current_user: 当前用户
        db: 数据库会话

    Returns:
        ApiResponse: 包含待遗忘节点列表和分页信息的响应

    Examples:
        - 第1页，每页10条：GET /memory/forget-memory/pending-nodes?end_user_id=xxx&page=1&pagesize=10
        - 第2页，每页20条：GET /memory/forget-memory/pending-nodes?end_user_id=xxx&page=2&pagesize=20

    Notes:
        - page 从1开始，pagesize 必须大于0
        - 返回格式：{"items": [...], "page": {"page": 1, "pagesize": 10, "total": 100, "hasnext": true}}
    """
    workspace_id = current_user.current_workspace_id
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试获取待遗忘节点但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    # 验证 end_user_id 必填
    if not end_user_id:
        api_logger.warning(f"用户 {current_user.username} 尝试获取待遗忘节点但未提供 end_user_id")
        return fail(BizCode.INVALID_PARAMETER, "end_user_id 不能为空", "end_user_id is required")

    # 通过 end_user_id 获取关联的 config_id
    try:
        config_service = MemoryConfigService(db)
        config_id = config_service.get_config_id_by_end_user(end_user_id)

        if config_id is None:
            api_logger.warning(f"终端用户 {end_user_id} 未关联记忆配置")
            return fail(BizCode.INVALID_PARAMETER, f"终端用户 {end_user_id} 未关联记忆配置", "memory_config_id is None")

        api_logger.debug(f"通过 end_user_id={end_user_id} 获取到 config_id={config_id}")
    except ValueError as e:
        api_logger.warning(f"获取终端用户配置失败: {str(e)}")
        return fail(BizCode.INVALID_PARAMETER, str(e), "ValueError")
    except Exception as e:
        api_logger.error(f"获取终端用户配置时发生错误: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取终端用户配置失败", str(e))

    # 验证分页参数
    if page < 1:
        return fail(BizCode.INVALID_PARAMETER, "page 必须大于等于1", "page < 1")
    if pagesize < 1:
        return fail(BizCode.INVALID_PARAMETER, "pagesize 必须大于等于1", "pagesize < 1")

    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求获取待遗忘节点: "
        f"end_user_id={end_user_id}, page={page}, pagesize={pagesize}"
    )

    try:
        # 调用服务层获取待遗忘节点列表
        result = await forget_service.get_pending_nodes(
            db=db,
            end_user_id=end_user_id,
            config_id=config_id,
            page=page,
            pagesize=pagesize
        )

        # 构建响应
        response_data = PendingNodesResponse(**result)

        return success(data=response_data.model_dump(), msg="查询成功")

    except Exception as e:
        api_logger.error(f"获取待遗忘节点列表失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取待遗忘节点列表失败", str(e))


@router.post("/forgetting_curve", response_model=ApiResponse)
async def get_forgetting_curve(
        request: ForgettingCurveRequest,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """
    获取遗忘曲线数据
    
    生成遗忘曲线数据用于可视化，模拟记忆激活值随时间的衰减。
    
    Args:
        request: 遗忘曲线请求参数
        current_user: 当前用户
        db: 数据库会话
    
    Returns:
        ApiResponse: 包含遗忘曲线数据的响应
    """
    workspace_id = current_user.current_workspace_id
    request.config_id = resolve_config_id(request.config_id, db)
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试获取遗忘曲线但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求获取遗忘曲线: "
        f"importance_score={request.importance_score}, days={request.days}, config_id={request.config_id}"
    )

    try:
        # 调用服务层生成遗忘曲线
        result = await forget_service.get_forgetting_curve(
            db=db,
            importance_score=request.importance_score,
            days=request.days,
            config_id=request.config_id
        )

        # 转换为响应格式
        curve_points = [
            ForgettingCurvePoint(**point)
            for point in result['curve_data']
        ]

        # 构建响应
        response_data = ForgettingCurveResponse(
            curve_data=curve_points,
            config=result['config']
        )

        return success(data=response_data.model_dump(), msg="查询成功")

    except Exception as e:
        api_logger.error(f"获取遗忘曲线失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取遗忘曲线失败", str(e))


@router.get("/{end_user_id}/memory_quota", response_model=ApiResponse)
async def get_end_user_memory_quota(
    end_user_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取宿主活跃配额详情。"""
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    try:
        stats = await memory_forget_service.get_quota_breakdown(end_user_id)
    except Exception as e:
        api_logger.error(f"获取配额统计失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取配额统计失败", str(e))

    lambda_mem = 0.5
    memory_limit = 300

    async with get_async_db_context() as db:
        try:
            tenant_id = await get_tenant_id_by_end_user_id_async(db, uuid.UUID(end_user_id))
            if tenant_id:
                limit = await get_end_user_memory_limit_async(db, tenant_id)
                if limit:
                    memory_limit = limit
        except Exception:
            pass

        try:
            active_config_id = await get_workspace_memory_config_id_async(db, workspace_id)
            if active_config_id:
                cfg = await MemoryConfigRepository.get_by_id_async(db, active_config_id)
                if cfg and cfg.lambda_mem is not None:
                    lambda_mem = float(cfg.lambda_mem)
        except Exception:
            pass

    target_count = max(int(memory_limit * (1 - lambda_mem)), 50)

    return success(data={
        "memory_limit": memory_limit,
        "trigger_count": memory_limit,
        "target_count": target_count,
        "breakdown": stats["breakdown"],
    })


@router.get("/{end_user_id}/forgetting_trend", response_model=ApiResponse)
async def get_forgetting_trend(
    end_user_id: str,
    days: int = Query(7, ge=1, le=90),
    current_user: User = Depends(get_current_user),
):
    """近 N 天遗忘记忆数量趋势。"""
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone

    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    async with get_async_db_context() as db:
        rows = await ForgetLogRepository.get_daily_trend_async(db, uuid.UUID(end_user_id), start_date.date())

    daily = defaultdict(int, rows)

    trend = []
    for i in range(days - 1, -1, -1):
        d = now - timedelta(days=i)
        day_start = d.replace(hour=0, minute=0, second=0, microsecond=0)
        ts = int(day_start.timestamp() * 1000)
        trend.append({"date": ts, "count": daily.get(day_start.strftime("%Y-%m-%d"), 0)})

    return success(data=trend)


@router.get("/{end_user_id}/forgetting_candidates", response_model=ApiResponse)
async def get_forgetting_candidates(
    end_user_id: str,
    page: int = Query(1, ge=1),
    pagesize: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
):
    """获取下一批遗忘候选节点（分页，Redis 缓存 5 分钟）。"""
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    try:
        all_candidates = await memory_forget_service.compute_forgetting_candidates(end_user_id)
    except Exception as e:
        api_logger.error(f"获取遗忘候选失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取遗忘候选失败", str(e))

    total = len(all_candidates)
    start = (page - 1) * pagesize
    page_items = all_candidates[start:start + pagesize]

    return success(data={
        "items": page_items,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "hasnext": start + pagesize < total,
        },
    })


@router.get("/{end_user_id}/forgotten_logs", response_model=ApiResponse)
async def get_forgotten_logs(
    end_user_id: str,
    page: int = Query(1, ge=1),
    pagesize: int = Query(10, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """分页查询已遗忘日志列表。"""
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    try:
        async with get_async_db_context() as db:
            items, total = await ForgetLogRepository.get_forgotten_logs_async(
                db, uuid.UUID(end_user_id), page=page, pagesize=pagesize,
            )
    except Exception as e:
        api_logger.error(f"查询遗忘日志失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "查询遗忘日志失败", str(e))

    return success(data={
        "items": items,
        "page": {
            "page": page,
            "pagesize": pagesize,
            "total": total,
            "hasnext": page * pagesize < total,
        },
    })


@router.post("/{end_user_id}/refresh_cache", response_model=ApiResponse)
async def refresh_forget_cache(
    end_user_id: str,
    current_user: User = Depends(get_current_user),
):
    """清除该用户在 forget-memory 下的 Redis 缓存（候选列表 + 配额统计）。"""
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    try:
        await invalidate_cache(prefix=f"quota_breakdown:{end_user_id}")
        await invalidate_cache(prefix=f"forget_candidates:{end_user_id}")
    except Exception as e:
        api_logger.error(f"清除缓存失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "清除缓存失败", str(e))

    return success(data={"refreshed": True}, msg="缓存已清除")

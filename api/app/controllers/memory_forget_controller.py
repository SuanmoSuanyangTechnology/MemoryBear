"""
遗忘引擎控制器模块

本模块提供遗忘引擎的 REST API 接口，包括：
1. 手动触发遗忘周期
2. 获取和更新配置
3. 获取统计信息
4. 获取遗忘曲线数据

所有接口都需要用户认证，并自动关联到当前工作空间。
"""
from typing import Optional

from fastapi import APIRouter, Depends

from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.db import get_async_db_context
from app.dependencies import CurrentUserSnapshot, get_current_user_async
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
from app.services.memory_config_service import MemoryConfigService
from app.services.memory_forget_service import MemoryForgetService
from app.utils.config_utils import resolve_config_id_async

# 获取API专用日志器
api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory/forget-memory",
    tags=["Memory Forgetting Engine"],
    dependencies=[Depends(get_current_user_async)],  # 所有路由都需要认证
)

# 初始化服务
forget_service = MemoryForgetService()


async def _resolve_config_id_by_end_user(end_user_id: str):
    """通过 end_user_id 异步解析出对应的 config_id。

    Returns:
        (config_id | None, error_response | None)
    """
    try:
        async with get_async_db_context() as async_db:
            config_service = MemoryConfigService(async_db)
            config_id = await config_service.get_config_id_by_end_user_async(end_user_id)
            return config_id, None
    except ValueError as e:
        api_logger.warning(f"获取终端用户配置失败: {str(e)}")
        return None, fail(BizCode.INVALID_PARAMETER, str(e), "ValueError")
    except Exception as e:
        api_logger.error(f"获取终端用户配置时发生错误: {str(e)}")
        return None, fail(BizCode.INTERNAL_ERROR, "获取终端用户配置失败", str(e))


# ==================== API 端点 ====================
@router.post("/trigger", response_model=ApiResponse)
async def trigger_forgetting_cycle(
        payload: ForgettingTriggerRequest,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """
    手动触发遗忘周期（异步版本）。

    执行一次完整的遗忘周期，识别并融合低激活值节点。
    """
    workspace_id = current_user.current_workspace_id
    end_user_id = payload.end_user_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试触发遗忘周期但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    config_id, err = await _resolve_config_id_by_end_user(end_user_id)
    if err is not None:
        return err
    if config_id is None:
        api_logger.warning(f"终端用户 {end_user_id} 未关联记忆配置")
        return fail(
            BizCode.INVALID_PARAMETER,
            f"终端用户 {end_user_id} 未关联记忆配置",
            "memory_config_id is None",
        )

    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求触发遗忘周期: "
        f"end_user_id={end_user_id}, config_id={config_id}, max_batch={payload.max_merge_batch_size}, "
        f"min_days={payload.min_days_since_access}"
    )

    try:
        async with get_async_db_context() as db:
            report = await forget_service.trigger_forgetting_cycle_async(
                db=db,
                end_user_id=end_user_id,
                max_merge_batch_size=payload.max_merge_batch_size,
                min_days_since_access=payload.min_days_since_access,
                config_id=config_id,
            )

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
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """
    获取遗忘引擎统计信息（异步版本）。

    返回知识层节点统计、激活值分布等信息。
    """
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试获取遗忘引擎统计但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    config_id = None
    if end_user_id:
        config_id, err = await _resolve_config_id_by_end_user(end_user_id)
        if err is not None:
            return err
        if config_id is None:
            api_logger.warning(f"终端用户 {end_user_id} 未关联记忆配置")
            return fail(
                BizCode.INVALID_PARAMETER,
                f"终端用户 {end_user_id} 未关联记忆配置",
                "memory_config_id is None",
            )

    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求获取遗忘引擎统计: "
        f"end_user_id={end_user_id}, config_id={config_id}"
    )

    try:
        async with get_async_db_context() as db:
            stats = await forget_service.get_forgetting_stats(
                db=db,
                end_user_id=end_user_id,
                config_id=config_id,
            )

        response_data = ForgettingStatsResponse(**stats)
        return success(data=response_data.model_dump(), msg="查询成功")

    except Exception as e:
        api_logger.error(f"获取遗忘引擎统计失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取遗忘引擎统计失败", str(e))


@router.get("/pending-nodes", response_model=ApiResponse)
async def get_pending_nodes(
        end_user_id: str, # 如果修改为uuid.UUID，可能需要修改前端代码
        page: int = 1,
        pagesize: int = 10,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """
    获取待遗忘节点列表（独立分页接口，异步版本）。
    """
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试获取待遗忘节点但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    if not end_user_id:
        api_logger.warning(f"用户 {current_user.username} 尝试获取待遗忘节点但未提供 end_user_id")
        return fail(BizCode.INVALID_PARAMETER, "end_user_id 不能为空", "end_user_id is required")

    config_id, err = await _resolve_config_id_by_end_user(end_user_id)
    if err is not None:
        return err
    if config_id is None:
        api_logger.warning(f"终端用户 {end_user_id} 未关联记忆配置")
        return fail(
            BizCode.INVALID_PARAMETER,
            f"终端用户 {end_user_id} 未关联记忆配置",
            "memory_config_id is None",
        )

    if page < 1:
        return fail(BizCode.INVALID_PARAMETER, "page 必须大于等于1", "page < 1")
    if pagesize < 1:
        return fail(BizCode.INVALID_PARAMETER, "pagesize 必须大于等于1", "pagesize < 1")

    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求获取待遗忘节点: "
        f"end_user_id={end_user_id}, page={page}, pagesize={pagesize}"
    )

    try:
        async with get_async_db_context() as db:
            result = await forget_service.get_pending_nodes(
                db=db,
                end_user_id=end_user_id,
                config_id=config_id,
                page=page,
                pagesize=pagesize,
            )

        response_data = PendingNodesResponse(**result)
        return success(data=response_data.model_dump(), msg="查询成功")

    except Exception as e:
        api_logger.error(f"获取待遗忘节点列表失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取待遗忘节点列表失败", str(e))


@router.post("/forgetting_curve", response_model=ApiResponse)
async def get_forgetting_curve(
        request: ForgettingCurveRequest,
        current_user: CurrentUserSnapshot = Depends(get_current_user_async),
):
    """
    获取遗忘曲线数据（异步版本）。

    生成遗忘曲线数据用于可视化，模拟记忆激活值随时间的衰减。
    """
    workspace_id = current_user.current_workspace_id
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试获取遗忘曲线但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    try:
        async with get_async_db_context() as db:
            request.config_id = await resolve_config_id_async(request.config_id, db)

            api_logger.info(
                f"用户 {current_user.username} 在工作空间 {workspace_id} 请求获取遗忘曲线: "
                f"importance_score={request.importance_score}, days={request.days}, "
                f"config_id={request.config_id}"
            )

            result = await forget_service.get_forgetting_curve(
                db=db,
                importance_score=request.importance_score,
                days=request.days,
                config_id=request.config_id,
            )

        curve_points = [
            ForgettingCurvePoint(**point)
            for point in result['curve_data']
        ]
        response_data = ForgettingCurveResponse(
            curve_data=curve_points,
            config=result['config'],
        )
        return success(data=response_data.model_dump(), msg="查询成功")

    except Exception as e:
        api_logger.error(f"获取遗忘曲线失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "获取遗忘曲线失败", str(e))

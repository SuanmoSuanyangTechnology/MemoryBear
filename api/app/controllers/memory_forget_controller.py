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
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.db import get_db
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.memory_storage_schema import (
    ForgettingTriggerRequest,
    ForgettingConfigResponse,
    ForgettingConfigUpdateRequest,
    ForgettingStatsResponse,
    ForgettingReportResponse,
    ForgettingCurveRequest,
    ForgettingCurveResponse,
    ForgettingCurvePoint,
)
from app.schemas.response_schema import ApiResponse
from app.services.memory_forget_service import MemoryForgetService
from app.utils.config_utils import resolve_config_id

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
        from app.services.memory_agent_service import get_end_user_connected_config
        
        connected_config = get_end_user_connected_config(end_user_id, db)
        config_id = connected_config.get("memory_config_id")
        config_id = resolve_config_id(int(config_id), db)


        
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


@router.get("/read_config", response_model=ApiResponse)
async def read_forgetting_config(
    config_id: UUID|int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    获取遗忘引擎配置
    
    读取指定配置ID的遗忘引擎参数。
    
    Args:
        config_id: 配置ID
        current_user: 当前用户
        db: 数据库会话
    
    Returns:
        ApiResponse: 包含配置信息的响应
    """
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试读取遗忘引擎配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求读取遗忘引擎配置: {config_id}"
    )
    
    try:
        config_id=resolve_config_id(config_id, db)
        # 调用服务层读取配置
        config = forget_service.read_forgetting_config(db=db, config_id=config_id)
        
        # 构建响应
        response_data = ForgettingConfigResponse(**config)
        
        return success(data=response_data.model_dump(), msg="查询成功")
    
    except ValueError as e:
        api_logger.warning(f"配置不存在: config_id={config_id}, 错误: {str(e)}")
        return fail(BizCode.INVALID_PARAMETER, f"配置不存在: {config_id}", str(e))
    
    except Exception as e:
        api_logger.error(f"读取遗忘引擎配置失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "查询遗忘引擎配置失败", str(e))


@router.post("/update_config", response_model=ApiResponse)
async def update_forgetting_config(
    payload: ForgettingConfigUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    更新遗忘引擎配置
    
    更新指定配置ID的遗忘引擎参数。
    
    Args:
        payload: 配置更新请求
        current_user: 当前用户
        db: 数据库会话
    
    Returns:
        ApiResponse: 包含更新结果的响应
    """
    workspace_id = current_user.current_workspace_id
    payload.config_id=resolve_config_id(int(payload.config_id), db)

    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试更新遗忘引擎配置但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(
        f"用户 {current_user.username} 在工作空间 {workspace_id} 请求更新遗忘引擎配置: {payload.config_id}"
    )
    
    try:
        # 构建更新字段字典（排除 None 值和 config_id）
        update_data = {
            key: value 
            for key, value in payload.model_dump(exclude_none=True).items() 
            if key != 'config_id'
        }
        
        # 调用服务层更新配置
        config = forget_service.update_forgetting_config(
            db=db,
            config_id=payload.config_id,
            update_fields=update_data
        )
        
        # 构建响应
        response_data = ForgettingConfigResponse(**config)
        
        return success(data=response_data.model_dump(), msg="更新成功")
    
    except ValueError as e:
        api_logger.warning(f"配置不存在: config_id={payload.config_id}, 错误: {str(e)}")
        return fail(BizCode.INVALID_PARAMETER, str(e), "ValueError")
    
    except Exception as e:
        db.rollback()
        api_logger.error(f"更新遗忘引擎配置失败: {str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "更新遗忘引擎配置失败", str(e))


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
            from app.services.memory_agent_service import get_end_user_connected_config
            
            connected_config = get_end_user_connected_config(end_user_id, db)
            config_id = connected_config.get("memory_config_id")
            config_id = resolve_config_id(config_id, db)
            
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
    request.config_id = resolve_config_id(int(request.config_id), db)
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

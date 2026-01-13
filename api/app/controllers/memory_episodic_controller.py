"""
情景记忆相关的控制器
包含情景记忆总览和详情查询接口
"""

from fastapi import APIRouter, Depends

from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.response_schema import ApiResponse
from app.schemas.memory_episodic_schema import (
    EpisodicMemoryOverviewRequest,
    EpisodicMemoryDetailsRequest,
)
from app.services.memory_episodic_service import memory_episodic_service

# Get API logger
api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory-storage",
    tags=["Episodic Memory"],
)


@router.post("/classifications/episodic-memory", response_model=ApiResponse)
async def get_episodic_memory_overview_api(
    request: EpisodicMemoryOverviewRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    获取情景记忆总览
    
    返回指定用户的所有情景记忆列表，包括标题和创建时间。
    支持通过时间范围、情景类型和标题关键词进行筛选。
    
    """
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询情景记忆总览但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    # 验证参数
    valid_time_ranges = ["all", "today", "this_week", "this_month"]
    valid_episodic_types = ["all", "conversation", "project_work", "learning", "decision", "important_event"]
    
    if request.time_range not in valid_time_ranges:
        return fail(BizCode.INVALID_PARAMETER, f"无效的时间范围参数，可选值：{', '.join(valid_time_ranges)}")
    
    if request.episodic_type not in valid_episodic_types:
        return fail(BizCode.INVALID_PARAMETER, f"无效的情景类型参数，可选值：{', '.join(valid_episodic_types)}")
    
    # 处理 title_keyword（去除首尾空格）
    title_keyword = request.title_keyword.strip() if request.title_keyword else None
    
    api_logger.info(
        f"情景记忆总览查询请求: end_user_id={request.end_user_id}, user={current_user.username}, "
        f"workspace={workspace_id}, time_range={request.time_range}, episodic_type={request.episodic_type}, "
        f"title_keyword={title_keyword}"
    )
    
    try:
        # 调用Service层方法
        result = await memory_episodic_service.get_episodic_memory_overview(
            request.end_user_id, request.time_range, request.episodic_type, title_keyword
        )
        
        api_logger.info(
            f"成功获取情景记忆总览: end_user_id={request.end_user_id}, "
            f"total={result['total']}"
        )
        return success(data=result, msg="查询成功")
        
    except Exception as e:
        api_logger.error(f"情景记忆总览查询失败: end_user_id={request.end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "情景记忆总览查询失败", str(e))


@router.post("/classifications/episodic-memory-details", response_model=ApiResponse)
async def get_episodic_memory_details_api(
    request: EpisodicMemoryDetailsRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    获取情景记忆详情
    
    返回指定情景记忆的详细信息，包括涉及对象、情景类型、内容记录和情绪。
    
    """
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询情景记忆详情但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(
        f"情景记忆详情查询请求: end_user_id={request.end_user_id}, summary_id={request.summary_id}, "
        f"user={current_user.username}, workspace={workspace_id}"
    )
    
    try:
        # 调用Service层方法
        result = await memory_episodic_service.get_episodic_memory_details(
            end_user_id=request.end_user_id,
            summary_id=request.summary_id
        )
        
        api_logger.info(
            f"成功获取情景记忆详情: end_user_id={request.end_user_id}, summary_id={request.summary_id}"
        )
        return success(data=result, msg="查询成功")
        
    except ValueError as e:
        # 处理情景记忆不存在的情况
        api_logger.warning(f"情景记忆不存在: end_user_id={request.end_user_id}, summary_id={request.summary_id}, error={str(e)}")
        return fail(BizCode.INVALID_PARAMETER, "情景记忆不存在", str(e))
    except Exception as e:
        api_logger.error(f"情景记忆详情查询失败: end_user_id={request.end_user_id}, summary_id={request.summary_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "情景记忆详情查询失败", str(e))

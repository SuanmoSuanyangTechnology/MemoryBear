"""
显性记忆控制器

处理显性记忆相关的API接口，包括情景记忆和语义记忆的查询。
"""

from fastapi import APIRouter, Depends

from app.core.logging_config import get_api_logger
from app.core.response_utils import success, fail
from app.core.error_codes import BizCode
from app.services.memory_explicit_service import MemoryExplicitService
from app.schemas.response_schema import ApiResponse
from app.schemas.memory_explicit_schema import (
    ExplicitMemoryOverviewRequest,
    ExplicitMemoryDetailsRequest,
)
from app.dependencies import get_current_user
from app.models.user_model import User

# Get API logger
api_logger = get_api_logger()

# Initialize service
memory_explicit_service = MemoryExplicitService()

router = APIRouter(
    prefix="/memory-storage",
    tags=["Explicit Memory"],
)


@router.post("/classifications/explicit-memory", response_model=ApiResponse)
async def get_explicit_memory_overview_api(
    request: ExplicitMemoryOverviewRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    获取显性记忆总览
    
    返回指定用户的所有显性记忆列表，包括标题、完整内容、创建时间和情绪信息。
    """
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询显性记忆总览但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(
        f"显性记忆总览查询请求: end_user_id={request.end_user_id}, user={current_user.username}, "
        f"workspace={workspace_id}"
    )
    
    try:
        # 调用Service层方法
        result = await memory_explicit_service.get_explicit_memory_overview(
            request.end_user_id
        )
        
        api_logger.info(
            f"成功获取显性记忆总览: end_user_id={request.end_user_id}, "
            f"total={result['total']}"
        )
        return success(data=result, msg="查询成功")
        
    except Exception as e:
        api_logger.error(f"显性记忆总览查询失败: end_user_id={request.end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "显性记忆总览查询失败", str(e))


@router.post("/classifications/explicit-memory-details", response_model=ApiResponse)
async def get_explicit_memory_details_api(
    request: ExplicitMemoryDetailsRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    获取显性记忆详情
    
    根据 memory_id 返回情景记忆或语义记忆的详细信息。
    - 情景记忆：包括标题、内容、情绪、创建时间
    - 语义记忆：包括名称、核心定义、详细笔记、创建时间
    """
    workspace_id = current_user.current_workspace_id
    
    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询显性记忆详情但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")
    
    api_logger.info(
        f"显性记忆详情查询请求: end_user_id={request.end_user_id}, memory_id={request.memory_id}, "
        f"user={current_user.username}, workspace={workspace_id}"
    )
    
    try:
        # 调用Service层方法
        result = await memory_explicit_service.get_explicit_memory_details(
            end_user_id=request.end_user_id,
            memory_id=request.memory_id
        )
        
        api_logger.info(
            f"成功获取显性记忆详情: end_user_id={request.end_user_id}, memory_id={request.memory_id}, "
            f"memory_type={result.get('memory_type')}"
        )
        return success(data=result, msg="查询成功")
        
    except ValueError as e:
        # 处理记忆不存在的情况
        api_logger.warning(f"显性记忆不存在: end_user_id={request.end_user_id}, memory_id={request.memory_id}, error={str(e)}")
        return fail(BizCode.INVALID_PARAMETER, "显性记忆不存在", str(e))
    except Exception as e:
        api_logger.error(f"显性记忆详情查询失败: end_user_id={request.end_user_id}, memory_id={request.memory_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "显性记忆详情查询失败", str(e))

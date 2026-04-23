"""
显性记忆控制器

处理显性记忆相关的API接口，包括情景记忆和语义记忆的查询。
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

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
    prefix="/memory/explicit-memory",
    tags=["Explicit Memory"],
)


@router.post("/overview", response_model=ApiResponse)
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


@router.get("/episodics", response_model=ApiResponse)
async def get_episodic_memory_list_api(
    end_user_id: str = Query(..., description="end user ID"),
    page: int = Query(1, gt=0, description="page number, starting from 1"),
    pagesize: int = Query(10, gt=0, le=100, description="number of items per page, max 100"),
    start_date: Optional[int] = Query(None, description="start timestamp (ms)"),
    end_date: Optional[int] = Query(None, description="end timestamp (ms)"),
    episodic_type: str = Query("all", description="episodic type ：all/conversation/project_work/learning/decision/important_event"),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    获取情景记忆分页列表

    返回指定用户的情景记忆列表，支持分页、时间范围筛选和情景类型筛选。

    Args:
        end_user_id: 终端用户ID（必填）
        page: 页码（从1开始，默认1）
        pagesize: 每页数量（默认10，最大100）
        start_date: 开始时间戳（可选，毫秒），自动扩展到当天 00:00:00
        end_date: 结束时间戳（可选，毫秒），自动扩展到当天 23:59:59
        episodic_type: 情景类型筛选（可选，默认all）
        current_user: 当前用户

    Returns:
        ApiResponse: 包含情景记忆分页列表

    Examples:
        - 基础分页查询：GET /episodic-list?end_user_id=xxx&page=1&pagesize=5
          返回第1页，每页5条数据
        - 按时间范围筛选：GET /episodic-list?end_user_id=xxx&page=1&pagesize=5&start_date=1738684800000&end_date=1738771199000
          返回指定时间范围内的数据
        - 按情景类型筛选：GET /episodic-list?end_user_id=xxx&page=1&pagesize=5&episodic_type=important_event
          返回类型为"重要事件"的数据

    Notes:
        - start_date 和 end_date 必须同时提供或同时不提供
        - start_date 不能大于 end_date
        - episodic_type 可选值：all, conversation, project_work, learning, decision, important_event
        - total 为该用户情景记忆总数（不受筛选条件影响）
        - page.total 为筛选后的总条数
    """
    workspace_id = current_user.current_workspace_id

    # 检查用户是否已选择工作空间
    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询情景记忆列表但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"情景记忆分页查询: end_user_id={end_user_id}, "
        f"start_date={start_date}, end_date={end_date}, episodic_type={episodic_type}, "
        f"page={page}, pagesize={pagesize}, username={current_user.username}"
    )

    # 1. 参数校验
    if page < 1 or pagesize < 1:
        api_logger.warning(f"分页参数错误: page={page}, pagesize={pagesize}")
        return fail(BizCode.INVALID_PARAMETER, "分页参数必须大于0")

    valid_episodic_types = ["all", "conversation", "project_work", "learning", "decision", "important_event"]
    if episodic_type not in valid_episodic_types:
        api_logger.warning(f"无效的情景类型参数: {episodic_type}")
        return fail(BizCode.INVALID_PARAMETER, f"无效的情景类型参数，可选值：{', '.join(valid_episodic_types)}")

    # 时间戳参数校验
    if (start_date is not None and end_date is None) or (end_date is not None and start_date is None):
        return fail(BizCode.INVALID_PARAMETER, "start_date和end_date必须同时提供")

    if start_date is not None and end_date is not None and start_date > end_date:
        return fail(BizCode.INVALID_PARAMETER, "start_date不能大于end_date")

    # 2. 执行查询
    try:
        result = await memory_explicit_service.get_episodic_memory_list(
            end_user_id=end_user_id,
            page=page,
            pagesize=pagesize,
            start_date=start_date,
            end_date=end_date,
            episodic_type=episodic_type,
        )
        api_logger.info(
            f"情景记忆分页查询成功: end_user_id={end_user_id}, "
            f"total={result['total']}, 返回={len(result['items'])}条"
        )
    except Exception as e:
        api_logger.error(f"情景记忆分页查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "情景记忆分页查询失败", str(e))

    # 3. 返回结构化响应
    return success(data=result, msg="查询成功")

@router.get("/semantics", response_model=ApiResponse)
async def get_semantic_memory_list_api(
    end_user_id: str = Query(..., description="终端用户ID"),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    获取语义记忆列表

    返回指定用户的全量语义记忆列表。

    Args:
        end_user_id: 终端用户ID（必填）
        current_user: 当前用户

    Returns:
        ApiResponse: 包含语义记忆全量列表
    """
    workspace_id = current_user.current_workspace_id

    if workspace_id is None:
        api_logger.warning(f"用户 {current_user.username} 尝试查询语义记忆列表但未选择工作空间")
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间", "current_workspace_id is None")

    api_logger.info(
        f"语义记忆列表查询: end_user_id={end_user_id}, username={current_user.username}"
    )

    try:
        result = await memory_explicit_service.get_semantic_memory_list(
            end_user_id=end_user_id
        )
        api_logger.info(
            f"语义记忆列表查询成功: end_user_id={end_user_id}, total={len(result)}"
        )
    except Exception as e:
        api_logger.error(f"语义记忆列表查询失败: end_user_id={end_user_id}, error={str(e)}")
        return fail(BizCode.INTERNAL_ERROR, "语义记忆列表查询失败", str(e))

    return success(data=result, msg="查询成功")


@router.post("/details", response_model=ApiResponse)
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

from datetime import datetime
from typing import Optional

from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.response_utils import fail, success
from app.db import get_db
from app.dependencies import (
    cur_workspace_access_guard,
    get_current_user,
)
from app.models.user_model import User
from app.schemas.response_schema import ApiResponse
from app.services.implicit_memory_service import ImplicitMemoryService
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory/implicit-memory",
    tags=["Implicit Memory"],
)


def handle_implicit_memory_error(e: Exception, operation: str, user_id: str = None) -> dict:
    """
    Centralized error handling for implicit memory operations.
    
    Args:
        e: The exception that occurred
        operation: Description of the operation that failed
        user_id: Optional user ID for logging context
        
    Returns:
        Standardized error response
    """
    error_context = f"user_id={user_id}" if user_id else "unknown user"
    
    if isinstance(e, ValueError):
        if "user" in str(e).lower() and "not found" in str(e).lower():
            api_logger.warning(f"Invalid user ID for {operation}: {error_context}")
            return fail(BizCode.INVALID_USER_ID, "无效的用户ID", str(e))
        elif "insufficient" in str(e).lower() or "no data" in str(e).lower():
            api_logger.warning(f"Insufficient data for {operation}: {error_context}")
            return fail(BizCode.INSUFFICIENT_DATA, "数据不足，无法进行分析", str(e))
        else:
            api_logger.warning(f"Invalid parameters for {operation}: {error_context}")
            return fail(BizCode.INVALID_FILTER_PARAMS, "无效的参数", str(e))
    
    elif isinstance(e, KeyError):
        api_logger.warning(f"Missing required data for {operation}: {error_context}")
        return fail(BizCode.INSUFFICIENT_DATA, "缺少必要的数据", str(e))
    
    elif isinstance(e, (ConnectionError, TimeoutError)):
        api_logger.error(f"Service unavailable for {operation}: {error_context}")
        return fail(BizCode.SERVICE_UNAVAILABLE, "服务暂时不可用", str(e))
    
    elif "analysis" in str(e).lower() or "llm" in str(e).lower():
        api_logger.error(f"Analysis failed for {operation}: {error_context}", exc_info=True)
        return fail(BizCode.ANALYSIS_FAILED, "分析处理失败", str(e))
    
    elif "storage" in str(e).lower() or "database" in str(e).lower():
        api_logger.error(f"Storage error for {operation}: {error_context}", exc_info=True)
        return fail(BizCode.PROFILE_STORAGE_ERROR, "数据存储失败", str(e))
    
    else:
        api_logger.error(f"Unexpected error for {operation}: {error_context}", exc_info=True)
        return fail(BizCode.INTERNAL_ERROR, f"{operation}失败", str(e))


def validate_user_id(user_id: str) -> None:
    """
    Validate user ID format and constraints.
    
    Args:
        user_id: User ID to validate
        
    Raises:
        ValueError: If user ID is invalid
    """
    if not user_id or not user_id.strip():
        raise ValueError("User ID cannot be empty")
    
    if len(user_id.strip()) < 1:
        raise ValueError("User ID is too short")


def validate_date_range(start_date: Optional[datetime], end_date: Optional[datetime]) -> None:
    """
    Validate date range parameters.
    
    Args:
        start_date: Start date
        end_date: End date
        
    Raises:
        ValueError: If date range is invalid
    """
    if (start_date and not end_date) or (end_date and not start_date):
        raise ValueError("Both start_date and end_date must be provided together")
    
    if start_date and end_date and start_date >= end_date:
        raise ValueError("start_date must be before end_date")
    
    if start_date and start_date > datetime.now():
        raise ValueError("start_date cannot be in the future")


def validate_confidence_threshold(threshold: float) -> None:
    """
    Validate confidence threshold parameter.
    
    Args:
        threshold: Confidence threshold to validate
        
    Raises:
        ValueError: If threshold is invalid
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("confidence_threshold must be between 0.0 and 1.0")


@router.get("/preferences/{user_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
async def get_preference_tags(
    user_id: str,
    confidence_threshold: float = Query(0.5, ge=0.0, le=1.0, description="Minimum confidence threshold"),
    tag_category: Optional[str] = Query(None, description="Filter by tag category"),
    start_date: Optional[datetime] = Query(None, description="Filter start date"),
    end_date: Optional[datetime] = Query(None, description="Filter end date"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ApiResponse:
    """
    Get user preference tags with filtering options.
    
    Args:
        user_id: Target user ID
        confidence_threshold: Minimum confidence score (0.0-1.0)
        tag_category: Optional category filter
        start_date: Optional start date filter
        end_date: Optional end date filter
        
    Returns:
        List of preference tags matching the filters
    """
    api_logger.info(f"Preference tags requested for user: {user_id}")
    
    try:
        # Validate inputs
        validate_user_id(user_id)
        validate_confidence_threshold(confidence_threshold)
        validate_date_range(start_date, end_date)
        
        # Create service with user-specific config
        service = ImplicitMemoryService(db=db, end_user_id=user_id)
        
        # Build date range
        date_range = None
        if start_date and end_date:
            from app.schemas.implicit_memory_schema import DateRange
            date_range = DateRange(start_date=start_date, end_date=end_date)
        
        # Get preference tags
        tags = await service.get_preference_tags(
            user_id=user_id,
            confidence_threshold=confidence_threshold,
            tag_category=tag_category,
            date_range=date_range
        )
        
        api_logger.info(f"Retrieved {len(tags)} preference tags for user: {user_id}")
        return success(data=[tag.dict() for tag in tags], msg="偏好标签获取成功")
        
    except Exception as e:
        return handle_implicit_memory_error(e, "偏好标签获取", user_id)


@router.get("/portrait/{user_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
async def get_dimension_portrait(
    user_id: str,
    include_history: bool = Query(False, description="Include historical trends"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ApiResponse:
    """
    Get user's four-dimension personality portrait.
    
    Args:
        user_id: Target user ID
        include_history: Whether to include historical trend data
        
    Returns:
        Four-dimension personality portrait with scores and evidence
    """
    api_logger.info(f"Dimension portrait requested for user: {user_id}")
    
    try:
        # Validate inputs
        validate_user_id(user_id)
        
        # Create service with user-specific config
        service = ImplicitMemoryService(db=db, end_user_id=user_id)
        
        portrait = await service.get_dimension_portrait(
            user_id=user_id,
            include_history=include_history
        )
        
        api_logger.info(f"Dimension portrait retrieved for user: {user_id}")
        return success(data=portrait.dict(), msg="四维画像获取成功")
        
    except Exception as e:
        return handle_implicit_memory_error(e, "四维画像获取", user_id)


@router.get("/interest-areas/{user_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
async def get_interest_area_distribution(
    user_id: str,
    include_trends: bool = Query(False, description="Include trend analysis"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ApiResponse:
    """
    Get user's interest area distribution across four areas.
    
    Args:
        user_id: Target user ID
        include_trends: Whether to include trend analysis data
        
    Returns:
        Interest area distribution with percentages and evidence
    """
    api_logger.info(f"Interest area distribution requested for user: {user_id}")
    
    try:
        # Validate inputs
        validate_user_id(user_id)
        
        # Create service with user-specific config
        service = ImplicitMemoryService(db=db, end_user_id=user_id)
        
        distribution = await service.get_interest_area_distribution(
            user_id=user_id,
            include_trends=include_trends
        )
        
        api_logger.info(f"Interest area distribution retrieved for user: {user_id}")
        return success(data=distribution.dict(), msg="兴趣领域分布获取成功")
        
    except Exception as e:
        return handle_implicit_memory_error(e, "兴趣领域分布获取", user_id)


@router.get("/habits/{user_id}", response_model=ApiResponse)
@cur_workspace_access_guard()
async def get_behavior_habits(
    user_id: str,
    confidence_level: Optional[str] = Query(None, regex="^(high|medium|low)$", description="Filter by confidence level"),
    frequency_pattern: Optional[str] = Query(None, regex="^(daily|weekly|monthly|seasonal|occasional|event_triggered)$", description="Filter by frequency pattern"),
    time_period: Optional[str] = Query(None, regex="^(current|past)$", description="Filter by time period"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ApiResponse:
    """
    Get user's behavioral habits with filtering options.
    
    Args:
        user_id: Target user ID
        confidence_level: Filter by confidence level (high, medium, low)
        frequency_pattern: Filter by frequency pattern (daily, weekly, monthly, seasonal, occasional, event_triggered)
        time_period: Filter by time period (current, past)
        
    Returns:
        List of behavioral habits matching the filters
    """
    api_logger.info(f"Behavior habits requested for user: {user_id}")
    
    try:
        # Validate inputs
        validate_user_id(user_id)
        
        # Create service with user-specific config
        service = ImplicitMemoryService(db=db, end_user_id=user_id)
        
        habits = await service.get_behavior_habits(
            user_id=user_id,
            confidence_level=confidence_level,
            frequency_pattern=frequency_pattern,
            time_period=time_period
        )
        
        api_logger.info(f"Retrieved {len(habits)} behavior habits for user: {user_id}")
        return success(data=[habit.dict() for habit in habits], msg="行为习惯获取成功")
        
    except Exception as e:
        return handle_implicit_memory_error(e, "行为习惯获取", user_id)



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
from app.schemas.implicit_memory_schema import GenerateProfileRequest
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
    Get user preference tags from cache.
    
    Args:
        user_id: Target user ID
        confidence_threshold: Minimum confidence score (0.0-1.0)
        tag_category: Optional category filter
        start_date: Optional start date filter
        end_date: Optional end date filter
        
    Returns:
        List of preference tags from cache
    """
    api_logger.info(f"Preference tags requested for user: {user_id} (from cache)")
    
    try:
        # Validate inputs
        validate_user_id(user_id)
        
        # Create service with user-specific config
        service = ImplicitMemoryService(db=db, end_user_id=user_id)
        
        # Get cached profile
        cached_profile = await service.get_cached_profile(end_user_id=user_id, db=db)
        
        if cached_profile is None:
            api_logger.info(f"用户 {user_id} 的画像缓存不存在或已过期")
            return fail(
                BizCode.NOT_FOUND,
                "画像缓存不存在或已过期，请调用 /generate_profile 接口生成新画像",
                ""
            )
        
        # Extract preferences from cache
        preferences = cached_profile.get("preferences", [])
        
        # Apply filters (client-side filtering on cached data)
        filtered_preferences = []
        for pref in preferences:
            # Filter by confidence threshold
            if confidence_threshold is not None and pref.get("confidence_score", 0) < confidence_threshold:
                continue
            
            # Filter by category if specified
            if tag_category and pref.get("category") != tag_category:
                continue
            
            # Filter by date range if specified
            if start_date or end_date:
                created_at_ts = pref.get("created_at")
                if created_at_ts:
                    created_at = datetime.fromtimestamp(created_at_ts / 1000)
                    if start_date and created_at < start_date:
                        continue
                    if end_date and created_at > end_date:
                        continue
            
            filtered_preferences.append(pref)
        
        api_logger.info(f"Retrieved {len(filtered_preferences)} preference tags for user: {user_id} (from cache)")
        return success(data=filtered_preferences, msg="偏好标签获取成功（缓存）")
        
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
    Get user's four-dimension personality portrait from cache.
    
    Args:
        user_id: Target user ID
        include_history: Whether to include historical trend data (ignored for cached data)
        
    Returns:
        Four-dimension personality portrait from cache
    """
    api_logger.info(f"Dimension portrait requested for user: {user_id} (from cache)")
    
    try:
        # Validate inputs
        validate_user_id(user_id)
        
        # Create service with user-specific config
        service = ImplicitMemoryService(db=db, end_user_id=user_id)
        
        # Get cached profile
        cached_profile = await service.get_cached_profile(end_user_id=user_id, db=db)
        
        if cached_profile is None:
            api_logger.info(f"用户 {user_id} 的画像缓存不存在或已过期")
            return fail(
                BizCode.NOT_FOUND,
                "画像缓存不存在或已过期，请调用 /generate_profile 接口生成新画像",
                ""
            )
        
        # Extract portrait from cache
        portrait = cached_profile.get("portrait", {})
        
        api_logger.info(f"Dimension portrait retrieved for user: {user_id} (from cache)")
        return success(data=portrait, msg="四维画像获取成功（缓存）")
        
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
    Get user's interest area distribution from cache.
    
    Args:
        user_id: Target user ID
        include_trends: Whether to include trend analysis data (ignored for cached data)
        
    Returns:
        Interest area distribution from cache
    """
    api_logger.info(f"Interest area distribution requested for user: {user_id} (from cache)")
    
    try:
        # Validate inputs
        validate_user_id(user_id)
        
        # Create service with user-specific config
        service = ImplicitMemoryService(db=db, end_user_id=user_id)
        
        # Get cached profile
        cached_profile = await service.get_cached_profile(end_user_id=user_id, db=db)
        
        if cached_profile is None:
            api_logger.info(f"用户 {user_id} 的画像缓存不存在或已过期")
            return fail(
                BizCode.NOT_FOUND,
                "画像缓存不存在或已过期，请调用 /generate_profile 接口生成新画像",
                ""
            )
        
        # Extract interest areas from cache
        interest_areas = cached_profile.get("interest_areas", {})
        
        api_logger.info(f"Interest area distribution retrieved for user: {user_id} (from cache)")
        return success(data=interest_areas, msg="兴趣领域分布获取成功（缓存）")
        
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
    Get user's behavioral habits from cache.
    
    Args:
        user_id: Target user ID
        confidence_level: Filter by confidence level (high, medium, low)
        frequency_pattern: Filter by frequency pattern (daily, weekly, monthly, seasonal, occasional, event_triggered)
        time_period: Filter by time period (current, past)
        
    Returns:
        List of behavioral habits from cache
    """
    api_logger.info(f"Behavior habits requested for user: {user_id} (from cache)")
    
    try:
        # Validate inputs
        validate_user_id(user_id)
        
        # Create service with user-specific config
        service = ImplicitMemoryService(db=db, end_user_id=user_id)
        
        # Get cached profile
        cached_profile = await service.get_cached_profile(end_user_id=user_id, db=db)
        
        if cached_profile is None:
            api_logger.info(f"用户 {user_id} 的画像缓存不存在或已过期")
            return fail(
                BizCode.NOT_FOUND,
                "画像缓存不存在或已过期，请调用 /generate_profile 接口生成新画像",
                ""
            )
        
        # Extract habits from cache
        habits = cached_profile.get("habits", [])
        
        # Apply filters (client-side filtering on cached data)
        filtered_habits = []
        for habit in habits:
            # Filter by confidence level
            if confidence_level:
                confidence_mapping = {
                    "high": 85,
                    "medium": 50,
                    "low": 20
                }
                numerical_confidence = confidence_mapping.get(confidence_level.lower())
                if habit.get("confidence_level", 0) < numerical_confidence:
                    continue
            
            # Filter by frequency pattern
            if frequency_pattern and habit.get("frequency_pattern") != frequency_pattern:
                continue
            
            # Filter by time period
            if time_period:
                is_current = habit.get("is_current", True)
                if time_period.lower() == "current" and not is_current:
                    continue
                elif time_period.lower() == "past" and is_current:
                    continue
            
            filtered_habits.append(habit)
        
        api_logger.info(f"Retrieved {len(filtered_habits)} behavior habits for user: {user_id} (from cache)")
        return success(data=filtered_habits, msg="行为习惯获取成功（缓存）")
        
    except Exception as e:
        return handle_implicit_memory_error(e, "行为习惯获取", user_id)





@router.post("/generate_profile", response_model=ApiResponse)
@cur_workspace_access_guard()
async def generate_implicit_memory_profile(
    request: GenerateProfileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ApiResponse:
    """
    Generate complete user profile (all 4 modules) and cache it.
    
    Args:
        request: Generate profile request with end_user_id
        db: Database session
        current_user: Current authenticated user
        
    Returns:
        Complete user profile with all modules
    """
    end_user_id = request.end_user_id
    api_logger.info(f"Generate profile requested for user: {end_user_id}")
    
    try:
        # Validate inputs
        validate_user_id(end_user_id)
        
        # Create service with user-specific config
        service = ImplicitMemoryService(db=db, end_user_id=end_user_id)
        
        # Generate complete profile (calls LLM for all 4 modules)
        api_logger.info(f"开始生成完整用户画像: user={end_user_id}")
        profile_data = await service.generate_complete_profile(user_id=end_user_id)
        
        # Save to cache
        await service.save_profile_cache(
            end_user_id=end_user_id,
            profile_data=profile_data,
            db=db,
            expires_hours=168  # 7 days
        )
        
        api_logger.info(f"用户画像生成并缓存成功: user={end_user_id}")
        
        # Add metadata
        profile_data["end_user_id"] = end_user_id
        profile_data["cached"] = False
        
        return success(data=profile_data, msg="用户画像生成成功")
        
    except Exception as e:
        api_logger.error(f"生成用户画像失败: user={end_user_id}, error={str(e)}", exc_info=True)
        return handle_implicit_memory_error(e, "用户画像生成", end_user_id)

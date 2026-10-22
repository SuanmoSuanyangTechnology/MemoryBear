"""
Memory Short Term Controller

This module provides REST API endpoints for managing short-term and long-term memory
data retrieval and analysis. It handles memory system statistics, data aggregation,
and provides comprehensive memory insights for end users.

Key Features:
- Short-term memory data retrieval and statistics
- Long-term memory data aggregation
- Entity count integration
- Multi-language response support
- Memory system analytics and reporting
"""

import uuid
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.language_utils import get_language_from_header
from app.core.logging_config import get_api_logger
from app.core.error_codes import BizCode
from app.core.response_utils import fail, success
from app.db import get_async_db, get_db
from app.dependencies import CurrentUserSnapshot, get_current_user, get_current_user_async
from app.models.user_model import User
from app.services.memory_short_service import LongService, ShortService
from app.services.memory_storage_service import search_entity
from app.repositories.end_user_repository import EndUserRepository
from app.schemas.short_term_memo_schema import MemoDateType, MemoScope, ShortTermMemoResponse
from app.services.short_term_memo_service import ShortTermMemoService

# Load environment variables for configuration
load_dotenv()

# Initialize API logger for request tracking and debugging
api_logger = get_api_logger()

# Configure router with prefix and tags for API organization
router = APIRouter(
    prefix="/memory/short",
    tags=["Memory"],
)


@router.get("/memos", response_model=ShortTermMemoResponse)
async def list_short_term_memos(
    end_user_id: uuid.UUID,
    scope: MemoScope = Query("current"),
    date_type: MemoDateType = Query("all"),
    page: int = Query(1, ge=1),
    pagesize: int = Query(20, ge=1, le=100),
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
    db: AsyncSession = Depends(get_async_db),
) -> dict | JSONResponse:
    """List authorized Statement memos with UTC timestamps and page pagination."""
    try:
        workspace_id = current_user.current_workspace_id
        if workspace_id is None:
            return JSONResponse(
                status_code=400,
                content=fail(
                    BizCode.INVALID_PARAMETER,
                    "请先切换到一个工作空间",
                    "current_workspace_id is None",
                ),
            )
        end_user = await EndUserRepository(db).get_active_end_user_in_workspace_async(
            end_user_id, workspace_id,
        )
        if end_user is None:
            return JSONResponse(
                status_code=404,
                content=fail(BizCode.NOT_FOUND, "终端用户不存在或不可访问", "resource not found"),
            )
        result = await ShortTermMemoService().list_memos(
            str(end_user.id), scope, date_type, page, pagesize,
        )
        return success(data=result.model_dump())
    except (ServiceUnavailable, SessionExpired, TransientError, SQLAlchemyError):
        api_logger.exception("Short-term memo database unavailable")
        return JSONResponse(
            status_code=503,
            content=fail(BizCode.INTERNAL_ERROR, "短期记忆服务暂时不可用", "database unavailable"),
        )
    except Exception:
        api_logger.exception("Failed to list short-term memos")
        return JSONResponse(
            status_code=500,
            content=fail(BizCode.INTERNAL_ERROR, "短期记忆查询失败", "internal error"),
        )


@router.get("/short_term")
async def short_term_configs(
        end_user_id: str,
        language_type:str = Header(default=None, alias="X-Language-Type"),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
):
    """
    Retrieve comprehensive short-term and long-term memory statistics
    
    Provides a comprehensive overview of memory system data for a specific end user,
    including short-term memory entries, long-term memory aggregations, entity counts,
    and retrieval statistics. Supports multi-language responses based on request headers.
    
    This endpoint serves as a central dashboard for memory system analytics, combining
    data from multiple memory subsystems to provide a holistic view of user memory state.
    
    Args:
        end_user_id: Unique identifier for the end user whose memory data to retrieve
        language_type: Language preference header for response localization (optional)
        current_user: Authenticated user making the request (injected by dependency)
        db: Database session for data operations (injected by dependency)
    
    Returns:
        dict: Success response containing comprehensive memory statistics:
            - short_term: List of short-term memory entries with detailed data
            - long_term: List of long-term memory aggregations and summaries
            - entity: Count of entities associated with the end user
            - retrieval_number: Total count of short-term memory retrievals
            - long_term_number: Total count of long-term memory entries
    
    Response Structure:
        {
            "code": 200,
            "msg": "Short-term memory system data retrieved successfully",
            "data": {
                "short_term": [...],      # Short-term memory entries
                "long_term": [...],       # Long-term memory data
                "entity": 42,             # Entity count
                "retrieval_number": 156,  # Short-term retrieval count
                "long_term_number": 23    # Long-term memory count
            }
        }
    
    Raises:
        HTTPException: If end_user_id is invalid or data retrieval fails
        
    Performance Notes:
        - Combines multiple service calls for comprehensive data
        - Entity search is performed asynchronously for better performance
        - Response time depends on memory data volume for the specified user
    """
    # Use centralized language validation for consistent localization
    language = get_language_from_header(language_type)
    
    # Retrieve short-term memory data and statistics
    short_term = ShortService(end_user_id, db)
    short_result = short_term.get_short_databasets()  # Get short-term memory entries
    short_count = short_term.get_short_count()        # Get short-term retrieval count

    # Retrieve long-term memory data and aggregations
    long_term = LongService(end_user_id, db)
    long_result = long_term.get_long_databasets()     # Get long-term memory entries

    # Get entity count for the specified end user
    entity_result = await search_entity(end_user_id)
    
    # Compile comprehensive memory statistics response
    result = {
        'short_term': short_result,                    # Short-term memory entries
        'long_term': long_result,                      # Long-term memory data
        'entity': entity_result.get('num', 0),        # Entity count (default to 0 if not found)
        "retrieval_number": short_count,               # Short-term retrieval statistics
        "long_term_number": len(long_result)          # Long-term memory entry count
    }

    return success(data=result, msg="短期记忆系统数据获取成功")

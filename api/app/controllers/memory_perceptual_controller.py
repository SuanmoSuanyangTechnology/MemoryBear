import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.response_utils import success, fail
from app.db import get_db
from app.dependencies import get_current_user
from app.models import User
from app.models.memory_perceptual_model import PerceptualType
from app.schemas.memory_perceptual_schema import (
    PerceptualQuerySchema,
    PerceptualFilter
)
from app.schemas.response_schema import ApiResponse
from app.services.memory_perceptual_service import MemoryPerceptualService

api_logger = get_api_logger()

router = APIRouter(
    prefix="/memory/perceptual",
    tags=["Perceptual Memory System"],
    dependencies=[Depends(get_current_user)]
)


@router.get("/{group_id}/count", response_model=ApiResponse)
def get_memory_count(
        group_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """Retrieve perceptual memory statistics for a user group.

    Args:
        group_id: ID of the user group (usually end_user_id in this context)
        current_user: Current authenticated user
        db: Database session

    Returns:
        ApiResponse: Response containing memory count statistics
    """
    api_logger.info(f"Fetching perceptual memory statistics: user={current_user.username}, group_id={group_id}")

    try:
        service = MemoryPerceptualService(db)
        count_stats = service.get_memory_count(group_id)

        api_logger.info(f"Memory statistics fetched successfully: total={count_stats.get('total', 0)}")

        return success(
            data=count_stats,
            msg="Memory statistics retrieved successfully"
        )

    except Exception as e:
        api_logger.error(f"Failed to fetch memory statistics: group_id={group_id}, error={str(e)}")
        return fail(
            code=BizCode.INTERNAL_ERROR,
            msg="Failed to fetch memory statistics",
        )


@router.get("/{group_id}/last_visual", response_model=ApiResponse)
def get_last_visual_memory(
        group_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """Retrieve the most recent VISION-type memory for a user.

    Args:
        group_id: ID of the user group
        current_user: Current authenticated user
        db: Database session

    Returns:
        ApiResponse: Metadata of the latest visual memory
    """
    api_logger.info(f"Fetching latest visual memory: user={current_user.username}, group_id={group_id}")

    try:
        service = MemoryPerceptualService(db)
        visual_memory = service.get_latest_visual_memory(group_id)

        if visual_memory is None:
            api_logger.info(f"No visual memory found: group_id={group_id}")
            return success(
                data=None,
                msg="No visual memory available"
            )

        api_logger.info(f"Latest visual memory retrieved successfully: file={visual_memory.get('file_name')}")

        return success(
            data=visual_memory,
            msg="Latest visual memory retrieved successfully"
        )

    except Exception as e:
        api_logger.error(f"Failed to fetch latest visual memory: group_id={group_id}, error={str(e)}")
        return fail(
            code=BizCode.INTERNAL_ERROR,
            msg="Failed to fetch latest visual memory",
        )


@router.get("/{group_id}/last_listen", response_model=ApiResponse)
def get_last_memory_listen(
        group_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """Retrieve the most recent AUDIO-type memory for a user.

    Args:
        group_id: ID of the user group
        current_user: Current authenticated user
        db: Database session

    Returns:
        ApiResponse: Metadata of the latest audio memory
    """
    api_logger.info(f"Fetching latest audio memory: user={current_user.username}, group_id={group_id}")

    try:
        service = MemoryPerceptualService(db)
        audio_memory = service.get_latest_audio_memory(group_id)

        if audio_memory is None:
            api_logger.info(f"No audio memory found: group_id={group_id}")
            return success(
                data=None,
                msg="No audio memory available"
            )

        api_logger.info(f"Latest audio memory retrieved successfully: file={audio_memory.get('file_name')}")

        return success(
            data=audio_memory,
            msg="Latest audio memory retrieved successfully"
        )

    except Exception as e:
        api_logger.error(f"Failed to fetch latest audio memory: group_id={group_id}, error={str(e)}")
        return fail(
            code=BizCode.INTERNAL_ERROR,
            msg="Failed to fetch latest audio memory",
        )


@router.get("/{group_id}/last_text", response_model=ApiResponse)
def get_last_text_memory(
        group_id: uuid.UUID,
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """Retrieve the most recent TEXT-type memory for a user.

    Args:
        group_id: ID of the user group
        current_user: Current authenticated user
        db: Database session

    Returns:
        ApiResponse: Metadata of the latest text memory
    """
    api_logger.info(f"Fetching latest text memory: user={current_user.username}, group_id={group_id}")

    try:
        # 调用服务层获取最近的文本记忆
        service = MemoryPerceptualService(db)
        text_memory = service.get_latest_text_memory(group_id)

        if text_memory is None:
            api_logger.info(f"No text memory found: group_id={group_id}")
            return success(
                data=None,
                msg="No text memory available"
            )

        api_logger.info(f"Latest text memory retrieved successfully: file={text_memory.get('file_name')}")

        return success(
            data=text_memory,
            msg="Latest text memory retrieved successfully"
        )

    except Exception as e:
        api_logger.error(f"Failed to fetch latest text memory: group_id={group_id}, error={str(e)}")
        return fail(
            code=BizCode.INTERNAL_ERROR,
            msg="Failed to fetch latest text memory",
        )


@router.get("/{group_id}/timeline", response_model=ApiResponse)
def get_memory_time_line(
        group_id: uuid.UUID,
        perceptual_type: Optional[PerceptualType] = Query(None, description="感知类型过滤"),
        page: int = Query(1, ge=1, description="页码"),
        page_size: int = Query(10, ge=1, le=100, description="每页大小"),
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db)
):
    """Retrieve a timeline of perceptual memories for a user group.

    Args:
        group_id: ID of the user group
        perceptual_type: Optional filter for perceptual type
        page: Page number for pagination
        page_size: Number of items per page
        current_user: Current authenticated user
        db: Database session

    Returns:
        ApiResponse: Timeline data of perceptual memories
    """
    api_logger.info(
        f"Fetching perceptual memory timeline: user={current_user.username}, "
        f"group_id={group_id}, type={perceptual_type}, page={page}"
    )

    try:
        query = PerceptualQuerySchema(
            filter=PerceptualFilter(type=perceptual_type),
            page=page,
            page_size=page_size
        )

        service = MemoryPerceptualService(db)
        timeline_data = service.get_time_line(group_id, query)

        api_logger.info(
            f"Perceptual memory timeline retrieved successfully: total={timeline_data.total}, "
            f"returned={len(timeline_data.memories)}"
        )

        return success(
            data=timeline_data.model_dump(),
            msg="Perceptual memory timeline retrieved successfully"
        )

    except Exception as e:
        api_logger.error(
            f"Failed to fetch perceptual memory timeline: group_id={group_id}, "
            f"error={str(e)}"
        )
        return fail(
            code=BizCode.INTERNAL_ERROR,
            msg="Failed to fetch perceptual memory timeline",
        )

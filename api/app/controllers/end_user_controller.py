"""End User 管理接口 - 无需认证"""

from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.repositories.end_user_repository import EndUserRepository
from app.schemas.memory_api_schema import (
    CreateEndUserRequest,
    CreateEndUserResponse,
)
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

router = APIRouter(prefix="/end_users", tags=["End Users"])
logger = get_business_logger()


@router.post("")
async def create_end_user(
    data: CreateEndUserRequest,
    db: Session = Depends(get_db),
):
    """
    Create an end user.
    
    Creates a new end user for the given workspace.
    If an end user with the same other_id already exists in the workspace,
    returns the existing one.
    """
    logger.info(f"Create end user request - other_id: {data.other_id}, workspace_id: {data.workspace_id}")

    end_user_repo = EndUserRepository(db)
    end_user = end_user_repo.get_or_create_end_user(
        app_id=None,
        workspace_id=data.workspace_id,
        other_id=data.other_id,
    )

    logger.info(f"End user ready: {end_user.id}")

    result = {
        "id": str(end_user.id),
        "other_id": end_user.other_id or "",
        "other_name": end_user.other_name or "",
        "workspace_id": str(end_user.workspace_id),
    }

    return success(data=CreateEndUserResponse(**result).model_dump(), msg="End user created successfully")

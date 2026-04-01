"""End User 服务接口 - 基于 API Key 认证"""

import uuid

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.controllers import user_memory_controllers
from app.core.api_key_auth import require_api_key
from app.core.error_codes import BizCode
from app.core.exceptions import BusinessException
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.repositories.end_user_repository import EndUserRepository
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.end_user_info_schema import EndUserInfoUpdate
from app.schemas.memory_api_schema import CreateEndUserRequest, CreateEndUserResponse
from app.services import api_key_service
from app.services.memory_config_service import MemoryConfigService

router = APIRouter(prefix="/end_user", tags=["V1 - End User API"])
logger = get_business_logger()


def _get_current_user(api_key_auth: ApiKeyAuth, db: Session):
    """Build a current_user object from API key auth

    Args:
        api_key_auth: Validated API key auth info
        db: Database session

    Returns:
        User object with current_workspace_id set
    """
    api_key = api_key_service.ApiKeyService.get_api_key(db, api_key_auth.api_key_id, api_key_auth.workspace_id)
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id
    return current_user


@router.post("/create")
@require_api_key(scopes=["memory"])
async def create_end_user(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(..., description="Request body"),
):
    """
    Create or retrieve an end user for the workspace.

    Creates a new end user and connects it to a memory configuration.
    If an end user with the same other_id already exists in the workspace,
    returns the existing one.

    Optionally accepts a memory_config_id to connect the end user to a specific
    memory configuration. If not provided, falls back to the workspace default config.
    Optionally accepts an app_id to bind the end user to a specific app.
    """
    body = await request.json()
    payload = CreateEndUserRequest(**body)
    workspace_id = api_key_auth.workspace_id

    logger.info(f"Create end user request - other_id: {payload.other_id}, workspace_id: {workspace_id}")

    # Resolve memory_config_id: explicit > workspace default
    memory_config_id = None
    config_service = MemoryConfigService(db)

    if payload.memory_config_id:
        try:
            memory_config_id = uuid.UUID(payload.memory_config_id)
        except ValueError:
            raise BusinessException(
                f"Invalid memory_config_id format: {payload.memory_config_id}",
                BizCode.INVALID_PARAMETER
            )
        config = config_service.get_config_with_fallback(memory_config_id, workspace_id)
        if not config:
            raise BusinessException(
                f"Memory config not found: {payload.memory_config_id}",
                BizCode.MEMORY_CONFIG_NOT_FOUND
            )
        memory_config_id = config.config_id
    else:
        default_config = config_service.get_workspace_default_config(workspace_id)
        if default_config:
            memory_config_id = default_config.config_id
            logger.info(f"Using workspace default memory config: {memory_config_id}")
        else:
            logger.warning(f"No default memory config found for workspace: {workspace_id}")

    # Resolve app_id: explicit from payload, otherwise None
    app_id = None
    if payload.app_id:
        try:
            app_id = uuid.UUID(payload.app_id)
        except ValueError:
            raise BusinessException(
                f"Invalid app_id format: {payload.app_id}",
                BizCode.INVALID_PARAMETER
            )

    end_user_repo = EndUserRepository(db)
    end_user = end_user_repo.get_or_create_end_user_with_config(
        app_id=app_id,
        workspace_id=workspace_id,
        other_id=payload.other_id,
        memory_config_id=memory_config_id,
    )

    logger.info(f"End user ready: {end_user.id}")

    result = {
        "id": str(end_user.id),
        "other_id": end_user.other_id or "",
        "other_name": end_user.other_name or "",
        "workspace_id": str(end_user.workspace_id),
        "memory_config_id": str(end_user.memory_config_id) if end_user.memory_config_id else None,
    }

    return success(data=CreateEndUserResponse(**result).model_dump(), msg="End user created successfully")


@router.get("/info")
@require_api_key(scopes=["memory"])
async def get_end_user_info(
    request: Request,
    end_user_id: str,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    Get end user info.

    Retrieves the info record (aliases, meta_data, etc.) for the specified end user.
    Delegates to the manager-side controller for shared logic.
    """
    current_user = _get_current_user(api_key_auth, db)
    return await user_memory_controllers.get_end_user_info(
        end_user_id=end_user_id,
        current_user=current_user,
        db=db,
    )


@router.post("/info/update")
@require_api_key(scopes=["memory"])
async def update_end_user_info(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    message: str = Body(None, description="Request body"),
):
    """
    Update end user info.

    Updates the info record (other_name, aliases, meta_data) for the specified end user.
    Delegates to the manager-side controller for shared logic.
    """
    body = await request.json()
    payload = EndUserInfoUpdate(**body)

    current_user = _get_current_user(api_key_auth, db)
    return await user_memory_controllers.update_end_user_info(
        info_update=payload,
        current_user=current_user,
        db=db,
    )

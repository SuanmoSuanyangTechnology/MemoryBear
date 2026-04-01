"""Memory Config 服务接口 - 基于 API Key 认证"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.api_key_auth import require_api_key
from app.core.logging_config import get_business_logger
from app.core.response_utils import success
from app.db import get_db
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.memory_api_schema import ListConfigsResponse
from app.services.memory_api_service import MemoryAPIService

router = APIRouter(prefix="/memory_config", tags=["V1 - Memory Config API"])
logger = get_business_logger()


@router.get("/configs")
@require_api_key(scopes=["memory"])
async def list_memory_configs(
    request: Request,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
):
    """
    List all memory configs for the workspace.

    Returns all available memory configurations associated with the authorized workspace.
    """
    logger.info(f"List configs request - workspace_id: {api_key_auth.workspace_id}")

    memory_api_service = MemoryAPIService(db)

    result = memory_api_service.list_memory_configs(
        workspace_id=api_key_auth.workspace_id,
    )

    logger.info(f"Listed {result['total']} configs for workspace: {api_key_auth.workspace_id}")
    return success(data=ListConfigsResponse(**result).model_dump(), msg="Configs listed successfully")

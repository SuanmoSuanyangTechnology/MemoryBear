"""Dashboard service API - based on API Key authentication

Query the end user list and each end user's memory count within the workspace
bound to a workspace-level API Key. Reuses the manager-side dashboard logic
instead of reimplementing the business logic.
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.orm import Session

from app.controllers import memory_dashboard_controller
from app.core.api_key_auth import require_api_key
from app.core.logging_config import get_business_logger
from app.db import get_db
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse
from app.services import api_key_service

router = APIRouter(prefix="/dashboard", tags=["V1 - Dashboard API"])
api_logger = get_business_logger()


@router.get("/end_users", response_model=ApiResponse)
@require_api_key(scopes=["memory"])
async def get_workspace_end_users(
    request: Request,
    background_tasks: BackgroundTasks,
    api_key_auth: ApiKeyAuth = None,
    db: Session = Depends(get_db),
    keyword: Optional[str] = Query(None, description="Search keyword (fuzzy match on other_name and id)"),
    page: int = Query(1, ge=1, description="Page number, starting from 1"),
    pagesize: int = Query(10, ge=1, description="Page size"),
):
    """
    Query the end user list and each end user's memory count within the workspace
    bound to the API Key (paginated, fuzzy search supported).

    The workspace is determined by the API Key and is not accepted as input, to
    prevent cross-workspace access. The response shape matches the manager-side
    `GET /dashboard/end_users`: `items[].memory_num.total` is the memory count
    of that end user.

    Args:
        keyword: Search keyword (optional, fuzzy match on other_name and id)
        page: Page number (starting from 1, default 1)
        pagesize: Page size (default 10)

    Returns:
        ApiResponse: end user list with pagination metadata
    """
    # 1. Build current_user from the API Key and bind the current workspace
    api_key = api_key_service.ApiKeyService.get_api_key(
        db, api_key_auth.api_key_id, api_key_auth.workspace_id
    )
    current_user = api_key.creator
    current_user.current_workspace_id = api_key_auth.workspace_id

    api_logger.info(
        "V1 query workspace end user memory count: workspace_id=%s, keyword=%s, page=%s, pagesize=%s",
        api_key_auth.workspace_id, keyword, page, pagesize,
    )

    # 2. Delegate to the manager-side logic; workspace_id is forced to the API Key's workspace.
    #    background_tasks is injected by FastAPI (not part of the API contract) and passed
    #    through to preserve the same post-response Celery dispatch behavior as the manager side.
    return memory_dashboard_controller.get_workspace_end_users(
        background_tasks=background_tasks,
        workspace_id=api_key_auth.workspace_id,
        keyword=keyword,
        page=page,
        pagesize=pagesize,
        db=db,
        current_user=current_user,
    )

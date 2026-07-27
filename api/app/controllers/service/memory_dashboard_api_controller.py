"""Dashboard service API - based on API Key authentication

Query the end user list and each end user's memory count within the workspace
bound to a workspace-level API Key. Reuses the manager-side dashboard logic
instead of reimplementing the business logic.
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query, Request

from app.controllers import memory_dashboard_controller
from app.core.api_key_auth import require_api_key_self_db
from app.core.logging_config import get_business_logger
from app.db import get_db_context, get_async_db_context
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse
from app.services import api_key_service

from app.dependencies import make_snapshot

router = APIRouter(prefix="/dashboard", tags=["V1 - Dashboard API"])
api_logger = get_business_logger()


@router.get("/end_users", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_workspace_end_users(
    request: Request,
    background_tasks: BackgroundTasks,
    api_key_auth: ApiKeyAuth = None,
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
    with get_db_context() as auth_db:
        # 1. Build current_user snapshot from the API Key (extract before session closes)
        api_key = api_key_service.ApiKeyService.get_api_key(
            auth_db, api_key_auth.api_key_id, api_key_auth.workspace_id
        )
        current_user = make_snapshot(api_key.creator, api_key_auth.workspace_id)

        # 2. Delegate to the manager-side logic with the same sync session;
        #    workspace_id is forced to the API Key's workspace.
        return await memory_dashboard_controller.get_workspace_end_users(
            background_tasks=background_tasks,
            workspace_id=api_key_auth.workspace_id,
            keyword=keyword,
            page=page,
            pagesize=pagesize,
            db=auth_db,
            current_user=current_user,
        )

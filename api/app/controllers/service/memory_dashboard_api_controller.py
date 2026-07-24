"""Dashboard service API - based on API Key authentication (纯异步版本)

Query the end user list and each end user's memory count within the workspace
bound to a workspace-level API Key. Reuses the manager-side dashboard logic
instead of reimplementing the business logic.
"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query, Request

from app.controllers import memory_dashboard_controller
from app.core.api_key_auth import require_api_key_self_db
from app.core.api_key_utils import get_current_user_from_api_key_async
from app.core.logging_config import get_business_logger
from app.db import get_async_db_context
from app.dependencies import CurrentUserSnapshot
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse

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
    """
    # 1. 从 API Key 异步构建 current_user，在 session 关闭前提取为快照避免 detached 错误
    async with get_async_db_context() as db:
        user = await get_current_user_from_api_key_async(db, api_key_auth)
        current_user = CurrentUserSnapshot(
            id=user.id,
            username=user.username,
            email=user.email,
            is_active=user.is_active,
            is_superuser=user.is_superuser,
            current_workspace_id=api_key_auth.workspace_id,
            tenant_id=user.tenant_id,
        )

    api_logger.info(
        "V1 query workspace end user memory count: workspace_id=%s, keyword=%s, page=%s, pagesize=%s",
        api_key_auth.workspace_id,
        keyword,
        page,
        pagesize,
    )

    # 2. 委托到 manager-side controller。该函数已在 P2 改为纯异步版本，
    #    不再需要 db 注入；workspace_id 强制取自 API Key 的 workspace。
    return await memory_dashboard_controller.get_workspace_end_users(
        background_tasks=background_tasks,
        workspace_id=api_key_auth.workspace_id,
        keyword=keyword,
        page=page,
        pagesize=pagesize,
        current_user=current_user,
    )

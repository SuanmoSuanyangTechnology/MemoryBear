"""API Key authenticated v1 queries for explicit memory."""

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import JSONResponse

from app.core.api_key_auth import get_current_api_key_auth, require_api_key_self_db
from app.core.api_key_utils import (
    validate_end_user_in_workspace_async,
)
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.core.response_utils import fail
from app.db import get_async_db_context
from app.integrations.profile.contracts import MemoryProfileAuthSource
from app.integrations.profile.errors import MemoryProfileClientError
from app.integrations.profile.proxy import forward_memory_profile_request
from app.schemas.response_schema import ApiResponse

logger = get_business_logger()

router = APIRouter(
    prefix="/memory/explicit-memory",
    tags=["V1 - Explicit Memory API"],
)


class _ApiKeyWorkspaceContext:
    """Minimal ``current_user`` stand-in for the shared forwarder.

    The API-key face has no ``User``: workspace and tenant come from the key's
    own scope, and the acting id is still resolved inside the proxy layer from
    ``get_current_api_key_auth`` (source=APIKEY), so nothing identity-related is
    fabricated here.
    """

    def __init__(self, workspace_id, tenant_id) -> None:
        self.current_workspace_id = workspace_id
        self.tenant_id = tenant_id


@router.get("/semantics", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_semantic_memory_list(
    request: Request,
    end_user_id: str = Query(..., description="终端用户ID"),
) -> dict | JSONResponse:
    """Query semantic memories through the shared explicit-memory service."""
    api_key_auth = get_current_api_key_auth()

    async with get_async_db_context() as db:
        end_user = await validate_end_user_in_workspace_async(
            db,
            end_user_id,
            api_key_auth.workspace_id,
        )
        resolved_end_user_id = str(end_user.id)

    # 认证、绑定空间与合并用户解析保持在本 handler（现状），转发只替换最后的用例调用。
    # 已迁移的读接口无条件走 memory-profile（无 ENABLE_MEMORY_PROFILE 开关）。
    try:
        result = await forward_memory_profile_request(
            method="GET",
            path=request.scope["path"],
            current_user=_ApiKeyWorkspaceContext(
                api_key_auth.workspace_id,
                api_key_auth.tenant_id,
            ),
            source=MemoryProfileAuthSource.APIKEY,
            query={"end_user_id": resolved_end_user_id},
            language_type=request.headers.get("X-Language-Type")
            or request.headers.get("Accept-Language"),
        )
    except MemoryProfileClientError as exc:
        logger.error(
            "语义记忆列表 proxy 失败: end_user_id=%s, error=%s",
            resolved_end_user_id,
            type(exc).__name__,
        )
        return fail(
            BizCode.SERVICE_UNAVAILABLE,
            "记忆画像服务暂不可用",
            "Memory profile service unavailable",
        )
    # K 面现状语义：服务层异常 = 500 + fail（无 404 空态分支）。远端信封里的
    # INTERNAL_ERROR 在此还原为 500，其余信封原样返回（HTTP 200）。
    if result.get("code") == BizCode.INTERNAL_ERROR:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=fail(BizCode.INTERNAL_ERROR, "语义记忆列表查询失败"),
        )
    return result

"""API Key authenticated v1 queries for explicit memory."""

from fastapi import APIRouter, Query, Request

from app.core.api_key_auth import get_current_api_key_auth, require_api_key_self_db
from app.core.api_key_utils import (
    validate_end_user_in_workspace_async,
)
from app.core.error_codes import BizCode
from app.core.logging_config import get_business_logger
from app.core.response_utils import fail, success
from app.db import get_async_db_context
from app.schemas.response_schema import ApiResponse
from app.services.memory_explicit_service import MemoryExplicitService

logger = get_business_logger()
memory_explicit_service = MemoryExplicitService()

router = APIRouter(
    prefix="/memory/explicit-memory",
    tags=["V1 - Explicit Memory API"],
)


@router.get("/semantics", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_semantic_memory_list(
    request: Request,
    end_user_id: str = Query(..., description="终端用户ID"),
) -> dict:
    """Query semantic memories through the shared explicit-memory service."""
    api_key_auth = get_current_api_key_auth()

    async with get_async_db_context() as db:
        end_user = await validate_end_user_in_workspace_async(
            db,
            end_user_id,
            api_key_auth.workspace_id,
        )
        resolved_end_user_id = str(end_user.id)

    try:
        result = await memory_explicit_service.get_semantic_memory_list(
            end_user_id=resolved_end_user_id,
        )
        return success(data=result, msg="查询成功")
    except Exception as exc:
        logger.error(
            "语义记忆列表查询失败: end_user_id=%s, error=%s",
            resolved_end_user_id,
            exc,
            exc_info=True,
        )
        return fail(BizCode.INTERNAL_ERROR, "语义记忆列表查询失败")

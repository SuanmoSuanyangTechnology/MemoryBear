"""API Key authenticated v1 queries for memory value ranking."""

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.core.api_key_auth import get_current_api_key_auth, require_api_key_self_db
from app.core.api_key_utils import (
    validate_end_user_in_workspace_async,
)
from app.core.error_codes import BizCode
from app.core.response_utils import fail, success
from app.db import get_async_db_context
from app.schemas.memory_value_ranking_schema import PermanentMemoryListApiResponse
from app.services.memory_value_ranking_service import (
    MemoryValueRankingService,
    PermanentMemoryNotFound,
    PermanentMemoryUnavailable,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/memory/value-ranking",
    tags=["V1 - Memory Value Ranking API"],
)


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, PermanentMemoryNotFound):
        return JSONResponse(
            status_code=404,
            content=fail(BizCode.NOT_FOUND, "终端用户或记忆不存在", "resource not found"),
        )
    if isinstance(exc, PermanentMemoryUnavailable):
        return JSONResponse(
            status_code=503,
            content=fail(BizCode.SERVICE_UNAVAILABLE, "永久记忆服务暂时不可用", str(exc)),
        )
    logger.exception("Unexpected permanent-memory V1 API failure", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content=fail(BizCode.INTERNAL_ERROR, "永久记忆操作失败", "internal error"),
    )


@router.get("/permanent-memories", response_model=PermanentMemoryListApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def list_permanent_memories(
    request: Request,
    end_user_id: str = Query(...),
    page: int = Query(1, ge=1),
    pagesize: int = Query(20, ge=1, le=100),
) -> dict | JSONResponse:
    """Query the shared value-ranking service using the API Key workspace."""
    api_key_auth = get_current_api_key_auth()
    async with get_async_db_context() as db:
        end_user = await validate_end_user_in_workspace_async(
            db,
            end_user_id,
            api_key_auth.workspace_id,
        )
        try:
            result = await MemoryValueRankingService(db).list_permanent_memories(
                str(end_user.id),
                api_key_auth.workspace_id,
                page,
                pagesize,
            )
            return success(data=result.model_dump(), msg="查询成功")
        except Exception as exc:
            return _error_response(exc)

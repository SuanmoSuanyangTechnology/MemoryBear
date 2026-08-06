"""JWT management APIs for permanent memories in memory value ranking."""

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.error_codes import BizCode
from app.core.response_utils import fail, success
from app.db import get_db
from app.dependencies import CurrentUserSnapshot, get_current_user_async
from app.schemas.memory_value_ranking_schema import (
    PermanentMemoryListApiResponse,
    PermanentMemoryQuotaApiResponse,
    PermanentMemoryUnmarkApiResponse,
    PermanentMemoryUnmarkRequest,
)
from app.services.memory_value_ranking_service import (
    MemoryValueRankingService,
    PermanentMemoryNotFound,
    PermanentMemoryUnavailable,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/memory/value-ranking",
    tags=["Memory Value Ranking"],
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
    logger.exception("Unexpected permanent-memory API failure", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content=fail(BizCode.INTERNAL_ERROR, "永久记忆操作失败", "internal error"),
    )


def _workspace_id(current_user: CurrentUserSnapshot) -> uuid.UUID | None:
    raw = current_user.current_workspace_id
    if raw is None:
        return None
    try:
        return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None


@router.get("/permanent-memories/quota", response_model=PermanentMemoryQuotaApiResponse)
async def get_permanent_memory_quota(
    end_user_id: str = Query(...),
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
    db: Session = Depends(get_db),
) -> dict | JSONResponse:
    workspace_id = _workspace_id(current_user)
    if workspace_id is None:
        return JSONResponse(
            status_code=400,
            content=fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间"),
        )
    try:
        quota = await MemoryValueRankingService(db).get_quota(end_user_id, workspace_id)
        return success(data=quota.model_dump(), msg="查询成功")
    except Exception as exc:
        return _error_response(exc)


@router.get("/permanent-memories", response_model=PermanentMemoryListApiResponse)
async def list_permanent_memories(
    end_user_id: str = Query(...),
    page: int = Query(1, ge=1),
    pagesize: int = Query(20, ge=1, le=100),
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
    db: Session = Depends(get_db),
) -> dict | JSONResponse:
    workspace_id = _workspace_id(current_user)
    if workspace_id is None:
        return JSONResponse(
            status_code=400,
            content=fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间"),
        )
    try:
        result = await MemoryValueRankingService(db).list_permanent_memories(
            end_user_id,
            workspace_id,
            page,
            pagesize,
        )
        return success(data=result.model_dump(), msg="查询成功")
    except Exception as exc:
        return _error_response(exc)


@router.patch(
    "/permanent-memories/{node_id}",
    response_model=PermanentMemoryUnmarkApiResponse,
    description=(
        "仅将 Statement 从永久记忆集合中移除（is_permanent=false）；"
        "不会删除 Statement 节点、关联边，也不会记录手动遗忘日志。"
    ),
)
async def unmark_permanent_memory(
    node_id: str,
    request: PermanentMemoryUnmarkRequest,
    current_user: CurrentUserSnapshot = Depends(get_current_user_async),
    db: Session = Depends(get_db),
) -> dict | JSONResponse:
    workspace_id = _workspace_id(current_user)
    if workspace_id is None:
        return JSONResponse(
            status_code=400,
            content=fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间"),
        )
    try:
        result = await MemoryValueRankingService(db).unmark_permanent_memory(
            node_id,
            request.end_user_id,
            workspace_id,
        )
        return success(data=result.model_dump(), msg="已取消永久记忆标识")
    except Exception as exc:
        return _error_response(exc)

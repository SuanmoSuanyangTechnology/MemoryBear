"""Existing episodic endpoints serving community cards and scene details."""
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.error_codes import BizCode
from app.core.logging_config import get_api_logger
from app.core.language_utils import get_language_from_header
from app.core.response_utils import fail, success
from app.core.memory.storage.custom.scene_community_storage import SceneCommunityNotFoundError
from app.db import get_async_db
from app.dependencies import get_current_user
from app.integrations.profile.proxy import memory_profile_proxy
from app.models.user_model import User
from app.repositories.end_user_repository import EndUserRepository
from app.schemas.response_schema import ApiResponse
from app.schemas.memory_episodic_schema import EpisodicMemoryOverviewRequest, EpisodicMemoryDetailsRequest
from app.services.memory_episodic_service import memory_episodic_service

api_logger = get_api_logger()
router = APIRouter(prefix="/memory/episodic-memory", tags=["Episodic Memory"])


async def _query(request, user, db, method, language="zh"):
    if user.current_workspace_id is None:
        return fail(BizCode.INVALID_PARAMETER, "请先切换到一个工作空间")
    try:
        user_id = UUID(request.end_user_id)
    except ValueError:
        return fail(BizCode.INVALID_PARAMETER, "无效终端用户 ID")
    try:
        end_user = await EndUserRepository(db).get_by_id_async(user_id)
        if end_user is None or end_user.workspace_id != user.current_workspace_id:
            return fail(BizCode.USER_NOT_FOUND, "终端用户不存在或不可访问")
        result = await method(request, str(end_user.id), language)
        return success(data=result, msg="查询成功")
    except SceneCommunityNotFoundError as exc:
        return fail(BizCode.INVALID_PARAMETER, str(exc))
    except Exception:
        api_logger.exception(
            "episodic community read failed: end_user_id=%s scene_community_id=%s",
            request.end_user_id, getattr(request, "scene_community_id", None),
        )
        return fail(BizCode.INTERNAL_ERROR, "情景记忆查询失败，请重试")


@router.post("/overview", response_model=ApiResponse)
@memory_profile_proxy(
    body_arg="payload",
    body_fields=("end_user_id", "category_l1", "keyword", "sort", "page", "pagesize"),
)
async def get_episodic_memory_overview_api(
    request: Request,
    payload: EpisodicMemoryOverviewRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return await _query(payload, current_user, db, memory_episodic_service.get_episodic_memory_overview)


@router.post("/details", response_model=ApiResponse)
@memory_profile_proxy(
    body_arg="payload",
    body_fields=("end_user_id", "scene_community_id", "page", "pagesize"),
)
async def get_episodic_memory_details_api(
    request: Request,
    payload: EpisodicMemoryDetailsRequest,
    language_type: str = Header(default=None, alias="X-Language-Type"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
) -> dict:
    return await _query(payload, current_user, db, memory_episodic_service.get_episodic_memory_details,
                        get_language_from_header(language_type))

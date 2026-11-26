"""API-key routes for Coding Agent preference recall."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from src.common.errors import BizCode, ProfileError
from src.common.external_api import external_api
from src.common.responses import ApiResponse
from src.middleware.auth import SOURCE_EXTERNAL, MemoryPrincipal
from src.middleware.identity import require_memory_context
from src.modules.preference_recall.handler import OTHER_ID_HEADER, PreferenceRecallHandler
from src.schemas.preference_recall import FullPreferenceRecallResult, PreferenceRecallResult

router = APIRouter(prefix="/memory/preference", tags=["Preference Recall"])


async def _require_api_key(
    principal: Annotated[MemoryPrincipal, Depends(require_memory_context)],
) -> MemoryPrincipal:
    if principal.source != SOURCE_EXTERNAL or principal.actor_kind != "apikey":
        raise ProfileError(BizCode.PERMISSION_DENIED, "无权访问该偏好资源", status_code=403)
    return principal


Context = Annotated[MemoryPrincipal, Depends(_require_api_key)]
OtherId = Annotated[str | None, Header(alias=OTHER_ID_HEADER)]


@router.post("/recall", response_model=ApiResponse[PreferenceRecallResult])
@external_api
async def recall_coding_preferences_http(
    request: Request, principal: Context, other_id: OtherId = None
) -> ApiResponse[PreferenceRecallResult] | JSONResponse:
    handler = PreferenceRecallHandler(request.app.state.runtime)
    return await handler.recall(request, principal, other_id)


@router.get("/recall/full", response_model=ApiResponse[FullPreferenceRecallResult])
@external_api
async def recall_all_coding_preferences_http(
    request: Request, principal: Context, other_id: OtherId = None
) -> ApiResponse[FullPreferenceRecallResult] | JSONResponse:
    handler = PreferenceRecallHandler(request.app.state.runtime)
    return await handler.recall_full(request, principal, other_id)

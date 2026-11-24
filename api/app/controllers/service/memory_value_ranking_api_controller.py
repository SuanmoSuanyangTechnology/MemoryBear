"""API Key authenticated v1 queries for memory value ranking."""

from fastapi import APIRouter, Query, Request, Response

from app.core.api_key_auth import get_current_api_key_auth, require_api_key_self_db
from app.core.api_key_utils import validate_end_user_in_workspace_async
from app.db import get_async_db_context
from app.integrations.profile.proxy import memory_profile_proxy
from app.schemas.memory_value_ranking_schema import PermanentMemoryListApiResponse

router = APIRouter(
    prefix="/memory/value-ranking",
    tags=["V1 - Memory Value Ranking API"],
)


@memory_profile_proxy(
    query_fields=("end_user_id", "page", "pagesize"),
    preserve_status=True,
)
async def _forward_list(request: Request, end_user_id: str, page: int, pagesize: int):
    """Forward the workspace-validated and merge-resolved API Key query."""


@router.get("/permanent-memories", response_model=PermanentMemoryListApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def list_permanent_memories(
    request: Request,
    end_user_id: str = Query(...),
    page: int = Query(1, ge=1),
    pagesize: int = Query(20, ge=1, le=100),
) -> dict | Response:
    """Query memory-profile using the API Key workspace and resolved end user."""
    api_key_auth = get_current_api_key_auth()
    async with get_async_db_context() as db:
        end_user = await validate_end_user_in_workspace_async(
            db,
            end_user_id,
            api_key_auth.workspace_id,
        )
        resolved_end_user_id = str(end_user.id)

    return await _forward_list(
        request=request,
        end_user_id=resolved_end_user_id,
        page=page,
        pagesize=pagesize,
    )

"""工作记忆 服务接口 基于 API Key 认证

复用 manager 侧 memory_working_controller 的 handler，由 memory_profile_proxy
统一转发到 profile，与 /api/memory/work/... 返回同一份 ApiResponse。

路由前缀: /memory/work
最终路径: /v1/memory/work/{end_user_id}/conversations
         /v1/memory/work/{end_user_id}/messages
         /v1/memory/work/{end_user_id}/source_messages
认证方式: API Key (@require_api_key_self_db)
"""

from uuid import UUID

from fastapi import APIRouter, Query, Request

from app.controllers import memory_working_controller
from app.core.api_key_auth import require_api_key_self_db
from app.core.api_key_utils import (
    get_current_user_snapshot_from_api_key_async,
    validate_end_user_in_workspace_async,
)
from app.db import get_async_db_context
from app.i18n.dependencies import get_current_language
from app.schemas.api_key_schema import ApiKeyAuth
from app.schemas.response_schema import ApiResponse

router = APIRouter(prefix="/memory/work", tags=["V1 - Working Memory System API"])


@router.get("/{end_user_id}/conversations", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_conversations(
        request: Request,
        end_user_id: str,
        page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
        pagesize: int = Query(default=20, ge=1, le=100, description="每页数量，最大 100"),
        api_key_auth: ApiKeyAuth = None,
):
    """分页获取某终端用户的会话列表。

    API Key 的授权边界是 workspace：先校验 end_user 归属当前 workspace，
    再按页返回会话 id 与标题，按 updated_at 倒序，豁免内部排除 app。
    """
    language = await get_current_language(request)
    async with get_async_db_context() as db:
        end_user = await validate_end_user_in_workspace_async(
            db, end_user_id, api_key_auth.workspace_id, locale=language
        )
        # 合并路由：end_user 可能是合并目标（原 ID 已被合并且 is_active=False），
        # 必须改用 end_user.id，否则下游会按已被合并掉的旧 ID 查询而拿到空数据。
        resolved_end_user_id = end_user.id
        current_user = await get_current_user_snapshot_from_api_key_async(db, api_key_auth)

    result = await memory_working_controller.get_conversations(
        request=request,
        end_user_id=resolved_end_user_id,
        page=page,
        pagesize=pagesize,
        current_user=current_user,
    )
    return result


@router.get("/{end_user_id}/messages", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_messages(
        request: Request,
        end_user_id: str,
        conversation_id: UUID = Query(..., description="会话 ID"),
        page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
        pagesize: int = Query(default=20, ge=1, le=100, description="每页数量，最大 100"),
        keyword: str | None = Query(default=None, description="消息正文关键词"),
        start_date: int | None = Query(default=None, ge=0, description="开始时间（毫秒时间戳，UTC）"),
        end_date: int | None = Query(default=None, ge=0, description="结束时间（毫秒时间戳，UTC）"),
        api_key_auth: ApiKeyAuth = None,
):
    """分页获取指定会话的消息列表，按 created_at 从旧到新排列。

    支持关键词与闭区间毫秒时间戳过滤；start_date 不能大于 end_date。
    """
    language = await get_current_language(request)
    async with get_async_db_context() as db:
        # 授权边界校验：end_user 必须归属 API Key 所在 workspace。
        await validate_end_user_in_workspace_async(
            db, end_user_id, api_key_auth.workspace_id, locale=language
        )
        current_user = await get_current_user_snapshot_from_api_key_async(db, api_key_auth)

    result = await memory_working_controller.get_messages(
        request=request,
        conversation_id=conversation_id,
        page=page,
        pagesize=pagesize,
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
    )
    return result


@router.get("/{end_user_id}/source_messages", response_model=ApiResponse)
@require_api_key_self_db(scopes=["memory"])
async def get_source_messages(
        request: Request,
        end_user_id: str,
        source: str = Query(..., description="来源：service_api 或 mcp"),
        page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
        pagesize: int = Query(default=20, ge=1, le=100, description="每页数量，最大 100"),
        keyword: str | None = Query(default=None, description="消息正文关键词"),
        start_date: int | None = Query(default=None, ge=0, description="开始时间（毫秒时间戳，UTC）"),
        end_date: int | None = Query(default=None, ge=0, description="结束时间（毫秒时间戳，UTC）"),
        api_key_auth: ApiKeyAuth = None,
):
    """按来源分页获取终端用户的工作记忆消息，按 created_at 从旧到新排列。

    仅 `service_api` 与 `mcp` 两种来源有数据；请求其他来源时返回空列表。
    支持关键词与闭区间毫秒时间戳过滤；start_date 不能大于 end_date。
    """
    language = await get_current_language(request)
    async with get_async_db_context() as db:
        end_user = await validate_end_user_in_workspace_async(
            db, end_user_id, api_key_auth.workspace_id, locale=language
        )
        # 合并路由：end_user 可能是合并目标（原 ID 已被合并且 is_active=False），
        # 改用 end_user.id 继续查询。
        resolved_end_user_id = end_user.id
        current_user = await get_current_user_snapshot_from_api_key_async(db, api_key_auth)

    result = await memory_working_controller.get_source_messages(
        request=request,
        end_user_id=resolved_end_user_id,
        source=source,
        page=page,
        pagesize=pagesize,
        keyword=keyword,
        start_date=start_date,
        end_date=end_date,
        current_user=current_user,
    )
    return result
